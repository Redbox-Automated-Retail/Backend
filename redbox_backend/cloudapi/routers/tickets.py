"""Ticket Controller Service stand-in (2014 kiosk engine "Ticket Services", never deployed by Redbox).

Client: `Redbox.DVD.TicketServices.dll` (kiosk engine builds of Feb/Mar 2014), a WCF client with
basicHttpBinding (SOAP 1.1, text/utf-8, no security; Transport security when the URL is https). Default URL
`http://tickets-dev.redbox.com/TicketControllerService/ControllerService.svc`.

Contract `IControllerService` (namespace NS_SVC), DataContractSerializer, wrapped document/literal:
GetTickets, UpdateTicketLock, ReleaseTicketLock, SendNotification, GetEmailByCard, PlaceOrder. Every operation
can fault with ValidationError (detail in NS_SCH) or GeneralError (detail in NS_SVC).

DataContractSerializer reads members in a fixed order (base class first, then alphabetical: none of the
contracts sets `Order`) and fails on a missing `IsRequired` member, so replies are written from the contract
table CONTRACTS below, which mirrors the client's `[DataMember]` attributes. Requests are parsed by local name.

State lives in the `ticket_*` tables (schema/52_tickets.sql): inventory per ticket group, seat locks with expiry,
orders, card-hash -> e-mail, notification sign-ups. Card authorisation follows `config.PAYMENT_MODE` like the
Transaction API (see docs/cloud-apis.md, "Ticket Services").
"""
import contextlib
import datetime as _dt
import decimal
import hashlib
import html
import logging
import uuid
import xml.etree.ElementTree as ET

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ... import config, db
from ..common import event

log = logging.getLogger("cloudapi.tickets")
router = APIRouter()

PATH = "/TicketControllerService/ControllerService.svc"
SOURCE = "tickets"

NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
NS_SVC = "http://www.redbox.com/ControllerServices/Tickets/2012/04"
NS_SCH = "http://schemas.datacontract.org/2004/07/Redbox.Ticketing.ControllerServices.Contract.Schema"
NS_GT = NS_SCH + ".GetTickets"
NS_PO = NS_SCH + ".PlaceOrder"
NS_UTL = NS_SCH + ".UpdateTicketLock"
NS_RTL = NS_SCH + ".ReleaseTicketLock"
NS_SN = NS_SCH + ".SendNotification"
NS_GE = NS_SCH + ".GetEmailByCard"
NS_I = "http://www.w3.org/2001/XMLSchema-instance"
NS_ARR = "http://schemas.microsoft.com/2003/10/Serialization/Arrays"
NS_SER = "http://schemas.microsoft.com/2003/10/Serialization/"
ACTION_PREFIX = NS_SVC + "/IControllerService/"

MAX_SUGGESTIONS = 3  # ticket groups held per GetTickets
CENT = decimal.Decimal("0.01")

# TicketsResponseCode (Redbox.TicketServices.Proxy.ComponentModel): Response.Code values the client maps.
TICKETS_NOT_AVAILABLE = "101"
NO_CONTIGUOUS_SEATS = "102"
SOLD_OUT = "103"
CARD_NOT_AUTHORIZED = "151"
CARD_NOT_AUTHORIZED_NSF = "152"
CUSTOMER_FRAUD_RISK = "153"
LOCK_EXPIRED_RELOCK_FAILED = "154"
MERCHANT_SERVICE_FAILURE = "155"
CARD_DECLINED = "161"
# PAYMENT_MODE=rules: card last four (from Track2) -> response code (same test values as the Transaction API).
DECLINE_RULES = {"0002": CARD_DECLINED, "0003": CARD_NOT_AUTHORIZED, "0005": CARD_NOT_AUTHORIZED_NSF,
                 "0008": MERCHANT_SERVICE_FAILURE, "0009": MERCHANT_SERVICE_FAILURE}

# ------------------------------------------------------------------ data contracts
# name -> (namespace, base or None, [(member, type, required)]); members in serialization order.
# Types: int, int?, string, boolean, decimal, guid, dateTime, an enum or contract name, "X[]" for arrays.

ENUMS = {
    "ChannelType": (NS_SCH, {"Web": 2, "Kiosk": 4}),
    "NotificationType": (NS_SN, {"Email": 2, "SMS": 4}),
    "SubscriptionType": (NS_SN, {"Event": 1, "General": 2}),
}

CONTRACTS = {
    "Request": (NS_SCH, None, [("BundleVersion", "string", True), ("ChannelType", "ChannelType", True),
                               ("EngineVersion", "string", True), ("KioskId", "int", True),
                               ("MessageDateTimeUtc", "dateTime", True), ("MessageId", "guid", True)]),
    "Response": (NS_SCH, None, [("Code", "string", False), ("Message", "string", False),
                                ("Success", "boolean", True)]),
    "UnlockTicket": (NS_SCH, None, [("Quantity", "int", True), ("TicketGroupGuid", "guid", True),
                                    ("TicketGroupId", "int", True)]),
    "TicketLockRequest": (NS_SCH, "Request", [("LockGuid", "guid", True), ("Quantity", "int", True),
                                              ("TicketGroupId", "int", True)]),
    "ValidationError": (NS_SCH, None, [("ErrorCorrelationId", "guid", True), ("Message", "string", True),
                                       ("MessageId", "string", True)]),
    "GeneralError": (NS_SVC, None, [("ErrorCorrelationId", "guid", True), ("Message", "string", True),
                                    ("MessageId", "string", True)]),
    "SupplierDisclaimer": (NS_SVC, None, [("SupplierNote", "string", True), ("SupplierNoteLogId", "int", True)]),
    # GetTickets
    "GetTicketsRequest": (NS_GT, "Request", [("PriceCodes", "PriceCode[]", True), ("ProductionId", "int", True),
                                             ("UnlockTickets", "UnlockTicket[]", False)]),
    "GetTicketsResponse": (NS_GT, "Response", [("ProductionId", "int", False),
                                               ("SupplierDisclaimers", "SupplierDisclaimer[]", True),
                                               ("TicketGroups", "TicketGroup[]", True)]),
    "PriceCode": (NS_GT, None, [("PriceCodeId", "int", True), ("Quantity", "int", True)]),
    "PriceCodeResponse": (NS_GT, "PriceCode", [("FeePerTicket", "decimal", True), ("FeePerTicketTax", "decimal", True),
                                               ("PricePerTicket", "decimal", True),
                                               ("PricePerTicketTax", "decimal", True),
                                               ("TaxCollection", "TaxItem[]", True)]),
    "TaxItem": (NS_GT, None, [("TaxAmt", "decimal", True), ("TaxTypeId", "int", True)]),
    "DeliveryMethod": (NS_GT, None, [("DeliveryMethodDescription", "string", True),
                                     ("DeliveryMethodDisplayName", "string", True),
                                     ("DeliveryMethodId", "int", True)]),
    "TicketGroup": (NS_GT, None, [("DeliveryMethods", "DeliveryMethod[]", True), ("DisplayOrder", "int", True),
                                  ("Level", "string", True), ("LockDuration", "int", True), ("LockGuid", "guid", True),
                                  ("PriceCodes", "PriceCodeResponse[]", True), ("Row", "string", True),
                                  ("Section", "string", True), ("SuggestionTypeDisplayName", "string", True),
                                  ("SuggestionTypeId", "int", True), ("SupplierId", "int", True),
                                  ("SupplierNoteLogId", "int?", True), ("TicketGroupId", "int", True),
                                  ("Zone", "string", True)]),
    # UpdateTicketLock / ReleaseTicketLock
    "UpdateTicketLockRequest": (NS_UTL, "TicketLockRequest", []),
    "UpdateTicketLockResponse": (NS_UTL, "Response", [("LockDuration", "int", True)]),
    "ReleaseTicketLockRequest": (NS_RTL, "TicketLockRequest", []),
    "ReleaseTicketLockResponse": (NS_RTL, "Response", []),
    # SendNotification
    "SendNotificationRequest": (NS_SN, "Request", [("CustomerEmail", "string", True),
                                                   ("CustomerSMSPhone", "string", True),
                                                   ("Subscriptions", "Subscription[]", True)]),
    "SendNotificationResponse": (NS_SN, "Response", []),
    "Subscription": (NS_SN, None, [("NotificationType", "NotificationType", True),
                                   ("SubscriptionType", "SubscriptionType", True),
                                   ("SubscriptionValue", "string", True)]),
    # GetEmailByCard
    "GetEmailByCardRequest": (NS_GE, "Request", [("HashedCreditCardNumber", "string", True)]),
    "GetEmailByCardResponse": (NS_GE, "Response", [("Email", "string", True)]),
    # PlaceOrder
    "PlaceOrderRequest": (NS_PO, "Request", [("Order", "Order", True), ("UnlockTickets", "UnlockTicket[]", False)]),
    "PlaceOrderResponse": (NS_PO, "Response", [("OrderId", "int", True)]),
    "Order": (NS_PO, None, [("Customer", "Customer", True), ("OrderHeader", "OrderHeader", True),
                            ("OrderItems", "OrderItem[]", True), ("Payment", "Payment", True),
                            ("PromotionList", "int[]", True)]),
    "Customer": (NS_PO, None, [("Email", "string", True)]),
    "OrderHeader": (NS_PO, None, [("OrderId", "int", True), ("Subtotal", "decimal", True), ("Total", "decimal", True),
                                  ("TotalFee", "decimal", True), ("TotalTax", "decimal", True)]),
    "OrderItem": (NS_PO, None, [("DeliverToName", "string", True), ("DeliveryMethodId", "int", True),
                                ("LockGuid", "guid", True), ("PriceCodes", "OrderPriceCode[]", True),
                                ("SuggestionTypeId", "int", True), ("SupplierNoteLogId", "int?", True),
                                ("TicketGroupId", "int", True)]),
    "OrderPriceCode": (NS_PO, None, [("FeePerTicket", "decimal", True), ("FeePerTicketTax", "decimal", True),
                                     ("OrderTaxCollection", "OrderTaxItem[]", True), ("PriceCodeId", "int", True),
                                     ("PricePerTicket", "decimal", True), ("PricePerTicketTax", "decimal", True),
                                     ("Quantity", "int", True)]),
    "OrderTaxItem": (NS_PO, None, [("TaxAmt", "decimal", True), ("TaxTypeId", "int", True)]),
    "Payment": (NS_PO, None, [("EncryptedCardNumber", "string", True), ("ExpirationMonth", "int", True),
                              ("ExpirationYear", "int", True), ("FirstName", "string", True),
                              ("HashedCardId", "string", True), ("KeyId", "string", True),
                              ("LastName", "string", True), ("PostalCode", "string", True),
                              ("Track2", "string", True)]),
}

PRIMITIVES = {"int": "int", "int?": "int", "string": "string", "boolean": "boolean", "decimal": "decimal",
              "guid": "guid", "dateTime": "dateTime"}

# operation -> (parameter name, request contract, response contract)
OPERATIONS = {
    "GetTickets": ("getTicketsRequest", "GetTicketsRequest", "GetTicketsResponse"),
    "UpdateTicketLock": ("updateTicketLockRequest", "UpdateTicketLockRequest", "UpdateTicketLockResponse"),
    "ReleaseTicketLock": ("releaseTicketLockRequest", "ReleaseTicketLockRequest", "ReleaseTicketLockResponse"),
    "SendNotification": ("sendNotificationRequest", "SendNotificationRequest", "SendNotificationResponse"),
    "GetEmailByCard": ("getEmailByCardRequest", "GetEmailByCardRequest", "GetEmailByCardResponse"),
    "PlaceOrder": ("placeOrderRequest", "PlaceOrderRequest", "PlaceOrderResponse"),
}


def all_members(name):
    """[(member, type, required, namespace)] in DataContractSerializer order (base contract first)."""
    ns, base, members = CONTRACTS[name]
    out = all_members(base) if base else []
    return out + [(m, t, r, ns) for m, t, r in members]


# ------------------------------------------------------------------ XML writing

def _esc(s):
    return html.escape(str(s), quote=False)


def _fmt(t, v):
    if t == "boolean":
        return "true" if v else "false"
    if t == "decimal":
        return format(money(v), "f")
    if t == "guid":
        return str(uuid.UUID(str(v)))
    if t == "dateTime":
        return v.isoformat() if isinstance(v, _dt.datetime) else str(v)
    if t in ENUMS:
        if isinstance(v, int):
            return next(k for k, n in ENUMS[t][1].items() if n == v)
        return str(v)
    return _esc(v)


def _array_item(t):
    """(element name, namespace) of a collection item."""
    if t in PRIMITIVES:
        return PRIMITIVES[t], NS_ARR
    if t in ENUMS:
        return t, ENUMS[t][0]
    return t, CONTRACTS[t][0]


def write(t, value, name, ns, scope_ns):
    """Serialize `value` of type `t` as element `name` in `ns`; `scope_ns` is the default namespace in scope."""
    xmlns = f' xmlns="{ns}"' if ns != scope_ns else ""
    if value is None:
        if t in ("int", "boolean", "decimal", "guid", "dateTime") or t in ENUMS:
            raise ValueError(f"{name}: value type {t} cannot be nil")
        return f'<{name}{xmlns} i:nil="true"/>'
    if t.endswith("[]"):
        item_t = t[:-2]
        iname, ins = _array_item(item_t)
        inner = "".join(write(item_t, v, iname, ins, ns) for v in value)
        return f"<{name}{xmlns}>{inner}</{name}>"
    if t in CONTRACTS:
        parts = []
        for m, mt, required, mns in all_members(t):
            if m not in value:
                if required:
                    raise ValueError(f"{t}.{m} is required")
                continue
            parts.append(write(mt, value[m], m, mns, ns))
        return f"<{name}{xmlns}>{''.join(parts)}</{name}>"
    return f"<{name}{xmlns}>{_fmt(t, value)}</{name}>"


def envelope(body_xml):
    return (f'<s:Envelope xmlns:s="{NS_SOAP}" xmlns:i="{NS_I}"><s:Body>{body_xml}</s:Body></s:Envelope>')


def soap_response(op, value):
    _, _, resp_type = OPERATIONS[op]
    body = (f'<{op}Response xmlns="{NS_SVC}">' + write(resp_type, value, op + "Result", NS_SVC, NS_SVC)
            + f"</{op}Response>")
    return Response(envelope(body).encode("utf-8"), media_type="text/xml; charset=utf-8")


class SoapFault(Exception):
    """A declared fault: kind is "ValidationError" (client error) or "GeneralError" (server error)."""

    def __init__(self, kind, message, message_id=None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.message_id = message_id
        self.correlation_id = uuid.uuid4()


def fault_response(f: SoapFault):
    ns = NS_SCH if f.kind == "ValidationError" else NS_SVC
    detail = write(f.kind, {"ErrorCorrelationId": f.correlation_id, "Message": f.message,
                            "MessageId": str(f.message_id) if f.message_id else None}, f.kind, ns, "")
    code = "s:Client" if f.kind == "ValidationError" else "s:Server"
    body = (f"<s:Fault><faultcode>{code}</faultcode><faultstring xml:lang=\"en-US\">{_esc(f.message)}</faultstring>"
            f"<detail>{detail}</detail></s:Fault>")
    return Response(envelope(body).encode("utf-8"), status_code=500, media_type="text/xml; charset=utf-8")


def plain_fault(code, message, status=500):
    """Fault without a declared detail (unknown action, unreadable message), as WCF sends them."""
    body = (f'<s:Fault><faultcode xmlns:a="http://schemas.microsoft.com/ws/2005/05/addressing/none">{code}</faultcode>'
            f'<faultstring xml:lang="en-US">{_esc(message)}</faultstring></s:Fault>')
    return Response(envelope(body).encode("utf-8"), status_code=status, media_type="text/xml; charset=utf-8")


# ------------------------------------------------------------------ XML reading

def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _is_nil(el):
    return (el.get(f"{{{NS_I}}}nil") or "").strip().lower() == "true"


def _parse_scalar(t, text):
    text = (text or "").strip()
    if t in ("int", "int?"):
        return int(text)
    if t == "boolean":
        return text.lower() in ("true", "1")
    if t == "decimal":
        return decimal.Decimal(text)
    if t == "guid":
        return uuid.UUID(text)
    if t == "dateTime":
        try:
            return _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:  # .NET writes 7 fractional digits
            head, _, rest = text.partition(".")
            frac = "".join(ch for ch in rest if ch.isdigit())
            tz = rest[len(frac):]
            return _dt.datetime.fromisoformat(f"{head}.{frac[:6]}{tz}".replace("Z", "+00:00"))
    return text  # enums are kept as their names


def parse(t, el):
    if el is None or _is_nil(el):
        return None
    if t.endswith("[]"):
        return [parse(t[:-2], child) for child in el]
    if t in CONTRACTS:
        children = {}
        for child in el:
            children.setdefault(_local(child.tag), child)
        out = {}
        for m, mt, _required, _ns in all_members(t):
            if m in children:
                out[m] = parse(mt, children[m])
            else:
                out[m] = None
        return out
    if t == "string":
        return el.text or ""
    return _parse_scalar(t, el.text)


def check(cond, message, req):
    if not cond:
        raise SoapFault("ValidationError", message, (req or {}).get("MessageId"))


# ------------------------------------------------------------------ endpoint

@router.post(PATH)
async def controller_service(request: Request):
    raw = await request.body()
    action = (request.headers.get("soapaction") or "").strip().strip('"')
    try:
        root = ET.fromstring(raw)
        body = next((c for c in root if _local(c.tag) == "Body"), None)
        wrapper = next(iter(body)) if body is not None and len(body) else None
    except ET.ParseError as e:
        return plain_fault("s:Client", f"The message could not be read: {e}", 400)
    if wrapper is None:
        return plain_fault("s:Client", "Empty SOAP body", 400)
    op = action[len(ACTION_PREFIX):] if action.startswith(ACTION_PREFIX) else (_local(wrapper.tag) if not action else None)
    if op not in OPERATIONS or _local(wrapper.tag) != op:
        return plain_fault("a:ActionNotSupported",
                           f"The message with Action '{action}' cannot be processed at the receiver.")
    param, req_type, _ = OPERATIONS[op]
    param_el = next((c for c in wrapper if _local(c.tag) == param), None)
    req = None
    try:
        try:
            req = parse(req_type, param_el)
        except (ValueError, decimal.InvalidOperation) as e:
            raise SoapFault("ValidationError", f"Malformed {req_type}: {e}")
        check(req is not None, f"{param} is required", req)
        result = await HANDLERS[op](req)
        return soap_response(op, result)
    except SoapFault as f:
        await _event(req, op + ".fault", {"kind": f.kind, "message": f.message,
                                          "correlation_id": str(f.correlation_id)})
        return fault_response(f)
    except Exception:
        log.exception("Ticket Services %s failed", op)
        f = SoapFault("GeneralError", "An internal error occurred.", (req or {}).get("MessageId"))
        return fault_response(f)


@router.get(PATH)
async def controller_service_wsdl(request: Request):
    if "wsdl" not in {k.lower() for k in request.query_params.keys()}:
        return Response("Ticket Controller Service (self-hosted). Metadata: ?wsdl", media_type="text/plain")
    location = str(request.url.replace(query=""))
    return Response(wsdl(location).encode("utf-8"), media_type="text/xml; charset=utf-8")


async def _event(req, kind, payload):
    kiosk = (req or {}).get("KioskId")
    try:
        await event(kiosk, SOURCE, kind, payload)
    except Exception:
        log.exception("could not record ticket event")


def ok(**extra):
    return {"Code": None, "Message": None, "Success": True, **extra}


def failed(code, message, **extra):
    return {"Code": code, "Message": message, "Success": False, **extra}


# ------------------------------------------------------------------ database helpers

@contextlib.asynccontextmanager
async def transaction():
    async with db.pool().connection() as conn:
        async with conn.transaction():
            yield conn


async def q(conn, sql, *args):
    cur = await conn.execute(sql, args or None)
    return await cur.fetchall() if cur.description else []


async def q1(conn, sql, *args):
    rows = await q(conn, sql, *args)
    return rows[0] if rows else None


async def held_counts(conn, group_ids, exclude_lock=None):
    rows = await q(conn, "SELECT ticket_group_id, sum(quantity) AS n FROM ticket_locks WHERE ticket_group_id = ANY(%s) "
                         "AND status = 'held' AND expires_at > now() AND lock_guid <> %s GROUP BY ticket_group_id",
                   list(group_ids), exclude_lock or uuid.UUID(int=0))
    return {r["ticket_group_id"]: int(r["n"]) for r in rows}


async def lock_groups(conn, where, *args):
    """Lock ticket_groups rows (FOR UPDATE) and add the `available` count to each."""
    groups = await q(conn, f"SELECT * FROM ticket_groups WHERE {where} ORDER BY display_order, ticket_group_id "
                           "FOR UPDATE", *args)
    held = await held_counts(conn, [g["ticket_group_id"] for g in groups])
    for g in groups:
        g["available"] = max(0, g["total_tickets"] - g["sold"] - held.get(g["ticket_group_id"], 0))
    return groups


async def release_locks(conn, unlocks, kiosk):
    released = 0
    for u in unlocks or []:
        if not u or not u.get("TicketGroupGuid"):
            continue
        released += (await conn.execute(
            "UPDATE ticket_locks SET status = 'released', updated_at = now() WHERE lock_guid = %s "
            "AND ticket_group_id = %s AND status = 'held'", (u["TicketGroupGuid"], u.get("TicketGroupId")))).rowcount
    return released


async def lock_duration(conn, group_id):
    return await q1(conn, "SELECT p.lock_duration_minutes AS m FROM ticket_groups g JOIN ticket_productions p "
                          "USING (production_id) WHERE g.ticket_group_id = %s", group_id)


def money(v):
    return decimal.Decimal(v or 0).quantize(CENT, rounding=decimal.ROUND_HALF_UP)


# ------------------------------------------------------------------ operations

async def get_tickets(req):
    kiosk = req.get("KioskId")
    production_id = req.get("ProductionId")
    wanted = [(p["PriceCodeId"], p["Quantity"]) for p in (req.get("PriceCodes") or []) if p and p.get("Quantity")]
    total = sum(n for _, n in wanted)
    check(wanted and all(n > 0 for _, n in wanted), "PriceCodes must ask for at least one ticket", req)
    async with transaction() as conn:
        prod = await q1(conn, "SELECT * FROM ticket_productions WHERE production_id = %s", production_id)
        check(prod is not None, f"Unknown ProductionId {production_id}", req)
        known = {r["price_code_id"] for r in await q(conn, "SELECT price_code_id FROM ticket_price_codes "
                                                           "WHERE production_id = %s", production_id)}
        unknown = [c for c, _ in wanted if c not in known]
        check(not unknown, f"Unknown PriceCodeId {unknown} for production {production_id}", req)
        check(total <= prod["ticket_limit_per_order"],
              f"At most {prod['ticket_limit_per_order']} tickets per order for this event", req)
        released = await release_locks(conn, req.get("UnlockTickets"), kiosk)
        now_row = await q1(conn, "SELECT now() AS now")
        on_sale = prod["on_sale_at"] <= now_row["now"] and (prod["off_sale_at"] is None
                                                           or prod["off_sale_at"] > now_row["now"])
        if not on_sale:
            result = failed(TICKETS_NOT_AVAILABLE, "This event is not on sale", ProductionId=production_id,
                            SupplierDisclaimers=[], TicketGroups=[])
            await _event(req, "GetTickets", {"production_id": production_id, "result": "not on sale"})
            return result
        groups = await lock_groups(conn, "production_id = %s", production_id)
        prices = {}
        for r in await q(conn, "SELECT p.* FROM ticket_group_prices p JOIN ticket_groups g USING (ticket_group_id) "
                               "WHERE g.production_id = %s", production_id):
            prices.setdefault(r["ticket_group_id"], {})[r["price_code_id"]] = r
        candidates = [g for g in groups if all(c in prices.get(g["ticket_group_id"], {}) for c, _ in wanted)]
        chosen = [g for g in candidates if g["available"] >= total][:MAX_SUGGESTIONS]
        if not chosen:
            if not any(g["available"] for g in candidates):
                code, msg = SOLD_OUT, "Tickets are sold out"
            else:
                code, msg = NO_CONTIGUOUS_SEATS, f"No ticket group has {total} seats together"
            await _event(req, "GetTickets", {"production_id": production_id, "wanted": wanted, "result": code})
            return failed(code, msg, ProductionId=production_id, SupplierDisclaimers=[], TicketGroups=[])
        methods = {r["delivery_method_id"]: r for r in await q(conn, "SELECT * FROM ticket_delivery_methods")}
        minutes = prod["lock_duration_minutes"]
        out_groups, note_ids = [], []
        for g in chosen:
            lock_guid = uuid.uuid4()
            await conn.execute(
                "INSERT INTO ticket_locks (lock_guid, ticket_group_id, kiosk_id, quantity, price_codes, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, now() + make_interval(mins => %s))",
                (lock_guid, g["ticket_group_id"], kiosk, total,
                 db.jsonb([{"PriceCodeId": c, "Quantity": n} for c, n in wanted]), minutes))
            gp = prices[g["ticket_group_id"]]
            price_codes = []
            for c, n in wanted:
                p = gp[c]
                tax = money(p["price_tax"]) + money(p["fee_tax"])
                price_codes.append({"PriceCodeId": c, "Quantity": n, "FeePerTicket": money(p["fee_per_ticket"]),
                                    "FeePerTicketTax": money(p["fee_tax"]),
                                    "PricePerTicket": money(p["price_per_ticket"]),
                                    "PricePerTicketTax": money(p["price_tax"]),
                                    "TaxCollection": [{"TaxAmt": tax, "TaxTypeId": 1}] if tax else []})
            if g["supplier_note_log_id"] is not None and g["supplier_note_log_id"] not in note_ids:
                note_ids.append(g["supplier_note_log_id"])
            out_groups.append({
                "DeliveryMethods": [{"DeliveryMethodDescription": methods[m]["description"],
                                     "DeliveryMethodDisplayName": methods[m]["display_name"],
                                     "DeliveryMethodId": m} for m in g["delivery_method_ids"] if m in methods],
                "DisplayOrder": g["display_order"], "Level": g["level"], "LockDuration": minutes,
                "LockGuid": lock_guid, "PriceCodes": price_codes, "Row": g["row_name"], "Section": g["section"],
                "SuggestionTypeDisplayName": g["suggestion_type_name"], "SuggestionTypeId": g["suggestion_type_id"],
                "SupplierId": g["supplier_id"], "SupplierNoteLogId": g["supplier_note_log_id"],
                "TicketGroupId": g["ticket_group_id"], "Zone": g["zone"]})
        notes = await q(conn, "SELECT * FROM ticket_supplier_notes WHERE supplier_note_log_id = ANY(%s)", note_ids)
        disclaimers = [{"SupplierNote": n["note"], "SupplierNoteLogId": n["supplier_note_log_id"]} for n in notes]
    await _event(req, "GetTickets", {"production_id": production_id, "wanted": wanted, "released": released,
                                     "locks": [str(g["LockGuid"]) for g in out_groups]})
    return ok(ProductionId=production_id, SupplierDisclaimers=disclaimers, TicketGroups=out_groups)


async def _find_lock(conn, req):
    lock = await q1(conn, "SELECT l.*, l.expires_at > now() AS live FROM ticket_locks l WHERE lock_guid = %s "
                          "FOR UPDATE", req.get("LockGuid"))
    check(lock is not None, f"Unknown LockGuid {req.get('LockGuid')}", req)
    check(lock["ticket_group_id"] == req.get("TicketGroupId"),
          f"LockGuid {req.get('LockGuid')} does not belong to ticket group {req.get('TicketGroupId')}", req)
    return lock


async def _relock(conn, lock, quantity):
    """Hold `quantity` seats under an existing lock (extending it). False when the seats are gone."""
    group = (await lock_groups(conn, "ticket_group_id = %s", lock["ticket_group_id"]))[0]
    own = lock["quantity"] if lock["status"] == "held" and lock["live"] else 0
    if group["available"] + own < quantity:
        return None
    minutes = (await lock_duration(conn, lock["ticket_group_id"]))["m"]
    await conn.execute("UPDATE ticket_locks SET status = 'held', quantity = %s, "
                       "expires_at = now() + make_interval(mins => %s), updated_at = now() WHERE lock_guid = %s",
                       (quantity, minutes, lock["lock_guid"]))
    return minutes


async def update_ticket_lock(req):
    quantity = req.get("Quantity")
    check(quantity is not None and quantity > 0, "Quantity must be positive", req)
    async with transaction() as conn:
        lock = await _find_lock(conn, req)
        if lock["status"] == "sold":
            result = failed(None, "These tickets have already been sold", LockDuration=0)
        elif lock["status"] == "released":
            result = failed(LOCK_EXPIRED_RELOCK_FAILED, "The lock was released", LockDuration=0)
        else:
            minutes = await _relock(conn, lock, quantity)
            result = (ok(LockDuration=minutes) if minutes is not None else
                      failed(LOCK_EXPIRED_RELOCK_FAILED, "The lock expired and the seats are no longer available",
                             LockDuration=0))
            if minutes is None and lock["status"] == "held":
                await conn.execute("UPDATE ticket_locks SET status = 'expired', updated_at = now() "
                                   "WHERE lock_guid = %s AND expires_at <= now()", (lock["lock_guid"],))
    await _event(req, "UpdateTicketLock", {"lock": str(req.get("LockGuid")), "quantity": quantity,
                                           "success": result["Success"], "code": result["Code"]})
    return result


async def release_ticket_lock(req):
    async with transaction() as conn:
        lock = await _find_lock(conn, req)
        if lock["status"] == "sold":
            result = failed(None, "These tickets have already been sold")
        else:
            await conn.execute("UPDATE ticket_locks SET status = 'released', updated_at = now() WHERE lock_guid = %s",
                               (lock["lock_guid"],))
            result = ok()
    await _event(req, "ReleaseTicketLock", {"lock": str(req.get("LockGuid")), "success": result["Success"]})
    return result


async def send_notification(req):
    email = (req.get("CustomerEmail") or "").strip() or None
    phone = (req.get("CustomerSMSPhone") or "").strip() or None
    check(email or phone, "CustomerEmail or CustomerSMSPhone is required", req)
    # The 2014 client resets its array index inside the loop, so all but the first entry can arrive as nil.
    subs = [{"NotificationType": s.get("NotificationType"), "SubscriptionType": s.get("SubscriptionType"),
             "SubscriptionValue": s.get("SubscriptionValue")} for s in (req.get("Subscriptions") or []) if s]
    customer_id = None
    if email:
        customer_id = await db.fetchval("SELECT customer_id FROM cloud_customers WHERE lower(email) = lower(%s)", email)
    await db.execute("INSERT INTO ticket_notifications (kiosk_id, message_id, email, sms_phone, customer_id, "
                     "subscriptions) VALUES (%s, %s, %s, %s, %s, %s)",
                     req.get("KioskId"), req.get("MessageId"), email, phone, customer_id, db.jsonb(subs))
    await _event(req, "SendNotification", {"subscriptions": subs, "email": bool(email), "sms": bool(phone),
                                           "customer_id": customer_id})
    return ok()


async def email_for_card(hashed):
    email = await db.fetchval("SELECT email FROM ticket_card_emails WHERE hashed_card_id = %s", hashed)
    if email:
        return email
    # A card the Transaction API knows under the same id (KCS card fingerprints use CardId, see customers.card_key).
    key = hashlib.sha256(("id:" + hashed).encode()).hexdigest()
    return await db.fetchval("SELECT c.email FROM cloud_cards k JOIN cloud_customers c USING (customer_id) "
                             "WHERE k.card_key = %s AND c.email IS NOT NULL", key)


async def get_email_by_card(req):
    hashed = (req.get("HashedCreditCardNumber") or "").strip()
    check(hashed, "HashedCreditCardNumber is required", req)
    email = await email_for_card(hashed)
    await _event(req, "GetEmailByCard", {"found": bool(email)})
    if email:
        return ok(Email=email)
    return failed(None, "No e-mail address is on file for this card", Email=None)


def _last_four(track2):
    pan = (track2 or "").lstrip(";%B").split("=", 1)[0].split("^", 1)[0]
    digits = "".join(ch for ch in pan if ch.isdigit())
    return digits[-4:] if len(digits) >= 4 else None


def _card_expired(month, year):
    if not month or not year:
        return False
    year = year + 2000 if year < 100 else year
    today = _dt.date.today()
    return (year, month) < (today.year, today.month)


async def payment_code(payment, email, hashed):
    """None when approved, else the TicketsResponseCode number."""
    mode = (config.PAYMENT_MODE or "approve").strip().lower()
    if hashed:
        key = hashlib.sha256(("id:" + hashed).encode()).hexdigest()
        if await db.fetchval("SELECT 1 FROM cloud_cards k LEFT JOIN cloud_customers c USING (customer_id) "
                             "WHERE k.card_key = %s AND (k.blocked OR coalesce(c.blocked, false))", key):
            return CUSTOMER_FRAUD_RISK
    if email and await db.fetchval("SELECT 1 FROM cloud_customers WHERE lower(email) = lower(%s) AND blocked", email):
        return CUSTOMER_FRAUD_RISK
    if mode == "decline":
        return CARD_DECLINED
    if mode == "rules":
        lf = _last_four(payment.get("Track2"))
        if lf in DECLINE_RULES:
            return DECLINE_RULES[lf]
        if _card_expired(payment.get("ExpirationMonth"), payment.get("ExpirationYear")):
            return CARD_NOT_AUTHORIZED
    return None


async def place_order(req):
    kiosk = req.get("KioskId")
    order = req.get("Order") or {}
    header = order.get("OrderHeader") or {}
    payment = order.get("Payment") or {}
    email = ((order.get("Customer") or {}).get("Email") or "").strip() or None
    items = [i for i in (order.get("OrderItems") or []) if i]
    check(items, "Order.OrderItems is empty", req)
    previous = await db.fetchrow("SELECT order_id, status, code FROM ticket_orders WHERE message_id = %s",
                                 req.get("MessageId"))
    if previous:  # replayed message
        if previous["status"] == "approved":
            return ok(OrderId=previous["order_id"])
        return failed(previous["code"], "Payment was not approved", OrderId=0)
    hashed = (payment.get("HashedCardId") or "").strip() or None

    async with transaction() as conn:
        subtotal = fees = taxes = decimal.Decimal("0")
        stored_items, locks = [], []
        for item in items:
            lock = await _find_lock(conn, {**req, "LockGuid": item.get("LockGuid"),
                                           "TicketGroupId": item.get("TicketGroupId")})
            check(lock["status"] != "sold", f"Lock {lock['lock_guid']} has already been sold", req)
            group = await q1(conn, "SELECT * FROM ticket_groups WHERE ticket_group_id = %s", lock["ticket_group_id"])
            check(item.get("DeliveryMethodId") in group["delivery_method_ids"],
                  f"Delivery method {item.get('DeliveryMethodId')} is not offered for ticket group "
                  f"{group['ticket_group_id']}", req)
            server_prices = {r["price_code_id"]: r for r in await q(
                conn, "SELECT * FROM ticket_group_prices WHERE ticket_group_id = %s", group["ticket_group_id"])}
            qty = 0
            for pc in [p for p in (item.get("PriceCodes") or []) if p]:
                sp = server_prices.get(pc.get("PriceCodeId"))
                check(sp is not None, f"Price code {pc.get('PriceCodeId')} is not sold in ticket group "
                                      f"{group['ticket_group_id']}", req)
                check(pc.get("Quantity", 0) > 0, "Quantity must be positive", req)
                check(money(pc.get("PricePerTicket")) == money(sp["price_per_ticket"])
                      and money(pc.get("FeePerTicket")) == money(sp["fee_per_ticket"]),
                      f"Price of code {pc.get('PriceCodeId')} in ticket group {group['ticket_group_id']} changed", req)
                n = pc["Quantity"]
                qty += n
                subtotal += money(sp["price_per_ticket"]) * n
                fees += money(sp["fee_per_ticket"]) * n
                taxes += (money(sp["price_tax"]) + money(sp["fee_tax"])) * n
            check(qty > 0, "An order item has no tickets", req)
            check(qty <= lock["quantity"], f"Lock {lock['lock_guid']} holds {lock['quantity']} tickets, not {qty}", req)
            locks.append((lock, qty))
            stored_items.append({"TicketGroupId": group["ticket_group_id"], "LockGuid": str(lock["lock_guid"]),
                                 "DeliveryMethodId": item.get("DeliveryMethodId"),
                                 "DeliverToName": item.get("DeliverToName"), "Quantity": qty,
                                 "PriceCodes": [{"PriceCodeId": p.get("PriceCodeId"), "Quantity": p.get("Quantity")}
                                                for p in item.get("PriceCodes") or [] if p]})
        total = subtotal + fees + taxes
        for field, value in (("Subtotal", subtotal), ("TotalFee", fees), ("TotalTax", taxes), ("Total", total)):
            sent = money(header.get(field))
            check(abs(sent - value) <= CENT, f"OrderHeader.{field} is {sent}, expected {value}", req)

        # Expired locks are renewed if the seats are still there (else LockExpiredRelockFailed).
        for lock, qty in locks:
            if lock["status"] != "held" or not lock["live"]:
                if await _relock(conn, lock, lock["quantity"]) is None:
                    await _event(req, "PlaceOrder", {"result": LOCK_EXPIRED_RELOCK_FAILED,
                                                     "lock": str(lock["lock_guid"])})
                    return failed(LOCK_EXPIRED_RELOCK_FAILED, "A ticket lock expired and the seats are gone",
                                  OrderId=0)

        code = await payment_code(payment, email, hashed)
        status = "approved" if code is None else "declined"
        row = await q1(conn,
                       "INSERT INTO ticket_orders (message_id, kiosk_id, status, code, email, hashed_card_id, "
                       "card_last_four, first_name, last_name, postal_code, subtotal, total_fee, total_tax, total, "
                       "kiosk_order_ref, promotions, items) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                       "%s, %s, %s, %s, %s) RETURNING order_id",
                       req.get("MessageId"), kiosk, status, code, email, hashed, _last_four(payment.get("Track2")),
                       # the 2014 client fills FirstName from the cart's LastName and vice versa
                       payment.get("LastName"), payment.get("FirstName"), payment.get("PostalCode"),
                       subtotal, fees, taxes, total, header.get("OrderId"),
                       db.jsonb([p for p in (order.get("PromotionList") or []) if p is not None]),
                       db.jsonb(stored_items))
        order_id = row["order_id"]
        if code is None:
            for lock, qty in locks:
                await conn.execute("UPDATE ticket_locks SET status = 'sold', quantity = %s, order_id = %s, "
                                   "updated_at = now() WHERE lock_guid = %s", (qty, order_id, lock["lock_guid"]))
                await conn.execute("UPDATE ticket_groups SET sold = sold + %s WHERE ticket_group_id = %s",
                                   (qty, lock["ticket_group_id"]))
            await release_locks(conn, req.get("UnlockTickets"), kiosk)
            if hashed and email:
                await conn.execute("INSERT INTO ticket_card_emails (hashed_card_id, email) VALUES (%s, %s) "
                                   "ON CONFLICT (hashed_card_id) DO UPDATE SET email = EXCLUDED.email, "
                                   "updated_at = now()", (hashed, email))
    await _event(req, "PlaceOrder", {"order_id": order_id, "status": status, "code": code, "total": total,
                                     "tickets": sum(q for _, q in locks)})
    if code is None:
        return ok(OrderId=order_id)
    return failed(code, "Payment was not approved", OrderId=0)


HANDLERS = {
    "GetTickets": get_tickets,
    "UpdateTicketLock": update_ticket_lock,
    "ReleaseTicketLock": release_ticket_lock,
    "SendNotification": send_notification,
    "GetEmailByCard": get_email_by_card,
    "PlaceOrder": place_order,
}


# ------------------------------------------------------------------ WSDL (generated from CONTRACTS)

def _namespaces():
    nss = [NS_SVC, NS_SCH, NS_GT, NS_PO, NS_UTL, NS_RTL, NS_SN, NS_GE, NS_ARR, NS_SER]
    return {ns: f"q{i}" for i, ns in enumerate(nss)}


def _xsd_type(t, prefixes):
    if t.endswith("[]"):
        iname, ins = _array_item(t[:-2])
        return f"{prefixes[ins]}:ArrayOf{iname}"
    if t == "guid":
        return f"{prefixes[NS_SER]}:guid"
    if t in PRIMITIVES:
        return "xs:" + PRIMITIVES[t]
    ns = ENUMS[t][0] if t in ENUMS else CONTRACTS[t][0]
    return f"{prefixes[ns]}:{t}"


def _nillable(t):
    return t == "string" or t == "int?" or t.endswith("[]") or t in CONTRACTS


def wsdl(location):
    p = _namespaces()
    per_ns = {ns: [] for ns in p}

    def element(m, t, required):
        attrs = ("" if required else ' minOccurs="0"') + f' name="{m}"' + (' nillable="true"' if _nillable(t) else "")
        return f'<xs:element{attrs} type="{_xsd_type(t, p)}"/>'

    arrays = set()
    for name, (ns, base, members) in CONTRACTS.items():
        seq = "<xs:sequence>" + "".join(element(m, t, r) for m, t, r in members) + "</xs:sequence>"
        arrays.update(t for _, t, _ in members if t.endswith("[]"))
        if base:
            body = (f'<xs:complexContent mixed="false"><xs:extension base="{p[CONTRACTS[base][0]]}:{base}">{seq}'
                    "</xs:extension></xs:complexContent>")
        else:
            body = seq
        per_ns[ns].append(f'<xs:complexType name="{name}">{body}</xs:complexType>'
                          f'<xs:element name="{name}" nillable="true" type="{p[ns]}:{name}"/>')
    for t in sorted(arrays):
        iname, ins = _array_item(t[:-2])
        item_type = _xsd_type(t[:-2], p)
        nil = ' nillable="true"' if _nillable(t[:-2]) else ""
        per_ns[ins].append(f'<xs:complexType name="ArrayOf{iname}"><xs:sequence><xs:element minOccurs="0" '
                           f'maxOccurs="unbounded" name="{iname}"{nil} type="{item_type}"/></xs:sequence>'
                           f'</xs:complexType><xs:element name="ArrayOf{iname}" nillable="true" '
                           f'type="{p[ins]}:ArrayOf{iname}"/>')
    for name, (ns, values) in ENUMS.items():
        enums = "".join(f'<xs:enumeration value="{k}"><xs:annotation><xs:appinfo><EnumerationValue xmlns="{NS_SER}">'
                        f"{v}</EnumerationValue></xs:appinfo></xs:annotation></xs:enumeration>"
                        for k, v in values.items())
        per_ns[ns].append(f'<xs:simpleType name="{name}"><xs:restriction base="xs:string">{enums}</xs:restriction>'
                          f'</xs:simpleType><xs:element name="{name}" nillable="true" type="{p[ns]}:{name}"/>')
    per_ns[NS_SER].append('<xs:simpleType name="guid"><xs:restriction base="xs:string"><xs:pattern value='
                          '"[\\da-fA-F]{8}-[\\da-fA-F]{4}-[\\da-fA-F]{4}-[\\da-fA-F]{4}-[\\da-fA-F]{12}"/>'
                          '</xs:restriction></xs:simpleType><xs:element name="guid" nillable="true" type="'
                          f'{p[NS_SER]}:guid"/>')
    for op, (param, req_t, resp_t) in OPERATIONS.items():
        per_ns[NS_SVC].append(
            f'<xs:element name="{op}"><xs:complexType><xs:sequence><xs:element minOccurs="0" name="{param}" '
            f'nillable="true" type="{_xsd_type(req_t, p)}"/></xs:sequence></xs:complexType></xs:element>'
            f'<xs:element name="{op}Response"><xs:complexType><xs:sequence><xs:element minOccurs="0" '
            f'name="{op}Result" nillable="true" type="{_xsd_type(resp_t, p)}"/></xs:sequence></xs:complexType>'
            "</xs:element>")
    schemas = ""
    for ns, parts in per_ns.items():
        if not parts:
            continue
        imports = "".join(f'<xs:import namespace="{o}"/>' for o in per_ns if o != ns and per_ns[o])
        schemas += (f'<xs:schema elementFormDefault="qualified" targetNamespace="{ns}">{imports}{"".join(parts)}'
                    "</xs:schema>")
    msgs, ports, binds = "", "", ""
    faults = (("ValidationError", NS_SCH, "s:Client"), ("GeneralError", NS_SVC, "s:Server"))
    for op in OPERATIONS:
        msgs += (f'<wsdl:message name="IControllerService_{op}_InputMessage"><wsdl:part name="parameters" '
                 f'element="{p[NS_SVC]}:{op}"/></wsdl:message>'
                 f'<wsdl:message name="IControllerService_{op}_OutputMessage"><wsdl:part name="parameters" '
                 f'element="{p[NS_SVC]}:{op}Response"/></wsdl:message>')
        pf, bf = "", ""
        for kind, fns, _ in faults:
            msgs += (f'<wsdl:message name="IControllerService_{op}_{kind}Fault_FaultMessage"><wsdl:part name="detail" '
                     f'element="{p[fns]}:{kind}"/></wsdl:message>')
            pf += (f'<wsdl:fault wsaw:Action="{ACTION_PREFIX}{op}{kind}Fault" name="{kind}Fault" '
                   f'message="{p[NS_SVC]}:IControllerService_{op}_{kind}Fault_FaultMessage"/>')
            bf += (f'<wsdl:fault name="{kind}Fault"><soap:fault use="literal" name="{kind}Fault" namespace=""/>'
                   "</wsdl:fault>")
        ports += (f'<wsdl:operation name="{op}"><wsdl:input wsaw:Action="{ACTION_PREFIX}{op}" '
                  f'message="{p[NS_SVC]}:IControllerService_{op}_InputMessage"/><wsdl:output '
                  f'wsaw:Action="{ACTION_PREFIX}{op}Response" message="{p[NS_SVC]}:IControllerService_{op}_'
                  f'OutputMessage"/>{pf}</wsdl:operation>')
        binds += (f'<wsdl:operation name="{op}"><soap:operation soapAction="{ACTION_PREFIX}{op}" style="document"/>'
                  '<wsdl:input><soap:body use="literal"/></wsdl:input><wsdl:output><soap:body use="literal"/>'
                  f"</wsdl:output>{bf}</wsdl:operation>")
    xmlns = "".join(f' xmlns:{pre}="{ns}"' for ns, pre in p.items())
    return ('<?xml version="1.0" encoding="utf-8"?>'
            f'<wsdl:definitions name="ControllerService" targetNamespace="{NS_SVC}"'
            ' xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/" xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"'
            ' xmlns:xs="http://www.w3.org/2001/XMLSchema" xmlns:wsaw="http://www.w3.org/2006/05/addressing/wsdl"'
            f'{xmlns}><wsdl:types>{schemas}</wsdl:types>{msgs}'
            f'<wsdl:portType name="IControllerService">{ports}</wsdl:portType>'
            f'<wsdl:binding name="basicHttpEndpoint" type="{p[NS_SVC]}:IControllerService">'
            f'<soap:binding transport="http://schemas.xmlsoap.org/soap/http"/>{binds}</wsdl:binding>'
            f'<wsdl:service name="ControllerService"><wsdl:port name="basicHttpEndpoint" '
            f'binding="{p[NS_SVC]}:basicHttpEndpoint"><soap:address location="{html.escape(location)}"/></wsdl:port>'
            "</wsdl:service></wsdl:definitions>")
