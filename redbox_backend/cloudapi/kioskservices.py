"""Kiosk Services MessageService (`IMessageService.Process(json) -> json`), the cloud of engines 1.81 and older.

The transport (.NET Remoting through rb-remoting, or the REST form post) is in routers/remoting.py; it calls
`process(message)` with the request as a dict and sends back the dict returned here as JSON.

Message set (see backend/docs/cloud-apis.md, "Kiosk Services MessageService"): every MessageType that the
2014-2016 `Redbox.DVD.KioskServices.dll`, the 1.81 `Redbox.KioskServices.Proxy` and its 2024 build send,
directly or through the engine's message queue (`KioskServices` / `SoftSync` queue processors, which forward the
queued JSON unchanged). Most messages carry the same objects as their Kiosk Client Service successors, so each
handler runs the matching KCS-era endpoint of this backend (routers/transaction.py, proxy.py, inventory.py,
kioskdata.py) on the message, and reshapes the answer to the keys the Kiosk Services clients read. State is the
shared cloud_* tables; nothing is canned.

How the client reads replies (Redbox.Core JavaScriptSerializer into Dictionary<string, object>):
- `Success` must be a JSON boolean; `Errors[0].Code` is matched against the client's ResponseCode enums
  ("(D001)", "(A006)", "(B004)", ...).
- Integers arrive as Int32 (cast with `(int)`), numbers with a fraction as Decimal (cast with `(decimal)`), so
  money is always written with a fraction (Python floats) and counts as ints.
- DateTime values the client converts with ToObject<DateTime> are Redbox.Core objects
  {"theDate": ticks, "dateTimeKind", "dateString"} (dotnet.redbox_datetime). A raw `(DateTime)` cast of a
  dictionary value (customer offer ExpirationDate) needs the JavaScriptSerializer date literal "\\/Date(ms)\\/"
  (escaped slashes): handlers write "/Date(ms)/" (`ms_date`) and `dumps`, which both transports use, escapes it.
- 2014-2016 engines take `IsOnline` from every reply (UpdateOnlineStatus) and drain their message queue only
  while it is true, so every reply has IsOnline=true and ServerBusy=false.
"""
import base64
import datetime as _dt
import decimal
import json
import logging
import re
import uuid

from starlette.requests import Request

from .. import db
from . import customers as cust
from .common import ACCEPTED_KEYS, HOSTNAME, ci, ci_list, error, now, remember, replayed, to_dec, to_int, to_uuid
from ..dotnet import redbox_datetime

log = logging.getLogger("cloudapi.kioskservices")

SOURCE = "kioskservices"
_EPOCH_TICKS = 621355968000000000

# EncryptionHelper.Encrypt (Authenticate Password, UpdateAccount Pin): TripleDES-CBC, key and IV are the
# Guid.ToByteArray() of these GUIDs (Redbox.KioskEngine.Environment.EncryptionHelper, 2016 proxy). They are
# constants of the public client binaries, not secrets of this deployment. The 16-byte key is two-key
# TripleDES (written here as the equivalent K1|K2|K1); .NET Framework uses the first 8 bytes of the 16-byte IV
# (checked against TripleDESCryptoServiceProvider).
_TDES_KEY = uuid.UUID("02CAD7C4-E0F5-4b1b-BD04-808B840A61D2").bytes_le
_TDES_KEY = _TDES_KEY + _TDES_KEY[:8]
_TDES_IV = uuid.UUID("AD186FCD-2ACD-41a8-958D-6CA7DAEB1DE5").bytes_le[:8]

ALERT_TYPES = {"qlm": 1, "returnrejected": 2, "applicationcrash": 3, "hardwareerror": 4,
               "heartbeatnotreceived": 5, "machinenearingfull": 6, "machinefull": 7, "titleempty": 8}
INVENTORY_STATUS = {0: "In", 1: "Removed"}          # InventoryItemStatus
RETURN_TYPE_UNPLAYABLE = 9                          # ReturnType.UnplayableReturn
# Messages that change state: a replay with the same MessageId (queue retry) gets the stored answer.
IDEMPOTENT = {"Return", "Reconcile", "PickupReconcile", "ModResvPickup", "KioskOptIn", "UpdateAccount", "KioskAlert",
              "AuditEvent", "SoftSync", "DiscFraud", "DiscAction", "UnplayableReturn", "RBiFreeTrialRequest",
              "Authorize", "GCAuthorize", "ResvAuth", "PickupAuthorize", "EmailConfirm"}


# ------------------------------------------------------------------ entry point

async def process(message, transport="rest"):
    if not isinstance(message, dict):
        return _reply(False, [error("(U001)", "message must be a JSON object")])
    if not message.get("MessageType") and isinstance(message.get("ReturnMessage"), dict):
        # 2024 Return queue entry: ReturnMessageWithClientData {ClientData, ReturnMessage}
        message = message["ReturnMessage"]
    mtype = str(message.get("MessageType") or "")
    handler = HANDLERS.get(mtype) or HANDLERS_CI.get(mtype.lower())
    if handler is None:
        log.warning("unknown Kiosk Services MessageType %r from kiosk %s (%s)", mtype, message.get("KioskId"), transport)
        return _reply(False, [error("(U001)", f"Unknown MessageType {mtype!r}")])
    canonical = handler.__name__[3:]
    message_id = to_uuid(message.get("MessageId"))
    endpoint = "ks:" + canonical
    if canonical in IDEMPOTENT and message_id:
        prev = await replayed(message_id, endpoint)
        if prev:
            return prev[1]
    kiosk = to_int(message.get("KioskId")) or None
    msg = _plain(message)
    result = await handler(msg, kiosk)
    result.setdefault("Success", True)
    out = _reply(result.pop("Success"), result.pop("Errors", None), message_id, **result)
    if canonical in IDEMPOTENT and message_id:
        await remember(message_id, endpoint, kiosk, 200, out)
    return out


def _reply(success, errors=None, message_id=None, **extra):
    body = {"Success": bool(success), "Errors": errors or [], "MessageId": str(message_id) if message_id else None,
            "ServerName": HOSTNAME, "server_name": HOSTNAME, "IsOnline": True, "ServerBusy": False}
    body.update(extra)
    return _wire({k: v for k, v in body.items() if v is not None})


def _wire(o):
    """Values as the .NET client must see them (see module docstring)."""
    if isinstance(o, dict):
        return {str(k): _wire(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [_wire(v) for v in o]
    if isinstance(o, decimal.Decimal):
        return float(o)
    if isinstance(o, _dt.datetime):
        return redbox_datetime(o)
    if isinstance(o, uuid.UUID):
        return str(o)
    return o


def ms_date(value):
    """'/Date(ms)/' for a datetime or ISO text; `dumps` makes it the literal JavaScriptSerializer reads as a DateTime."""
    if isinstance(value, str):
        value = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=_dt.timezone.utc)
    return "/Date(%d)/" % int(value.timestamp() * 1000)


_MS_DATE = re.compile(r'"/Date\((-?\d+)\)/"')


def dumps(result):
    """Reply JSON as the client must receive it: ms_date values with the escaped slashes of a .NET date literal."""
    return _MS_DATE.sub(r'"\\/Date(\1)\\/"', json.dumps(result, default=str))


def _ticks_to_dt(ticks):
    return _dt.datetime(1970, 1, 1, tzinfo=_dt.timezone.utc) + _dt.timedelta(microseconds=(int(ticks) - _EPOCH_TICKS) // 10)


def _plain(o):
    """Request copy with Redbox.Core DateTime objects turned into ISO strings for the KCS-era handlers.

    The ticks are used rather than dateString: some callers build dateString with a broken format
    ("yyyy-mm-ddThh:mm:ss", EmailConfirm). Local/Unspecified times are taken as UTC (the kiosk's zone is unknown).
    """
    if isinstance(o, dict):
        keys = set(o)
        if keys and keys <= {"theDate", "dateTimeKind", "dateString"} and "theDate" in keys:
            try:
                return _ticks_to_dt(o["theDate"]).isoformat()
            except (TypeError, ValueError, OverflowError):
                return o.get("dateString")
        return {k: _plain(v) for k, v in o.items()}
    if isinstance(o, list):
        return [_plain(v) for v in o]
    return o


def decrypt_secret(value):
    """EncryptionHelper.Encrypt reversed; returns the input unchanged when it is not such a value."""
    if not value or not isinstance(value, str):
        return value
    try:
        try:
            from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
        except ImportError:  # older cryptography
            from cryptography.hazmat.primitives.ciphers.algorithms import TripleDES
        from cryptography.hazmat.primitives import padding
        from cryptography.hazmat.primitives.ciphers import Cipher, modes
        raw = base64.b64decode(value, validate=True)
        if not raw or len(raw) % 8:
            return value
        dec = Cipher(TripleDES(_TDES_KEY), modes.CBC(_TDES_IV)).decryptor()
        padded = dec.update(raw) + dec.finalize()
        unpadder = padding.PKCS7(64).unpadder()
        return (unpadder.update(padded) + unpadder.finalize()).decode("cp1252")
    except Exception:
        return value


def encrypt_secret(value):
    """EncryptionHelper.Encrypt (used by tests and harnesses to build requests)."""
    try:
        from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
    except ImportError:
        from cryptography.hazmat.primitives.ciphers.algorithms import TripleDES
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, modes
    padder = padding.PKCS7(64).padder()
    data = padder.update(value.encode("cp1252")) + padder.finalize()
    enc = Cipher(TripleDES(_TDES_KEY), modes.CBC(_TDES_IV)).encryptor()
    return base64.b64encode(enc.update(data) + enc.finalize()).decode()


# ------------------------------------------------------------------ calling the KCS-era endpoints

async def _call(fn, body=None, kiosk=None, method="POST", path="/_kioskservices", **kwargs):
    """Run a FastAPI endpoint function of this backend on `body`; returns (status, json)."""
    raw = json.dumps(body if body is not None else {}, default=str).encode()
    headers = [(b"content-type", b"application/json")]
    if kiosk:
        headers.append((b"x-redbox-kioskid", str(kiosk).encode()))
    headers.append((b"x-api-key", (next(iter(ACCEPTED_KEYS)) if ACCEPTED_KEYS else "kioskservices").encode()))
    scope = {"type": "http", "method": method, "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": headers, "scheme": "http", "server": ("kioskservices", 80), "client": None, "root_path": ""}
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}

    resp = await fn(request=Request(scope, receive), **kwargs)
    try:
        data = json.loads(resp.body) if resp.body else {}
    except ValueError:
        data = {}
    return resp.status_code, data


def _routers():
    from .routers import inventory, kioskdata, proxy, transaction
    return transaction, proxy, inventory, kioskdata


def _errs(data, default_code=None, status=200):
    errs = [e for e in ci_list(data, "Errors") if isinstance(e, dict)]
    if not errs and ci(data, "Error"):
        errs = [error(str(ci(data, "Error")), str(ci(data, "Error")))]
    if not errs and default_code and status >= 400:
        errs = [error(default_code, f"HTTP {status}")]
    return errs


# ------------------------------------------------------------------ cart conversion (2014-2016 Contents)

def _ks_cart(msg):
    """The message with a KCS ShoppingCart: 2024 messages carry one; 2014-2016 ones send `Contents`
    {group name: {Items: [{Barcode, ProductFamily, ProductType, Prices{...}, VendStatus}], Totals}} and
    DiscountCode/DiscountValue."""
    if isinstance(msg.get("ShoppingCart"), dict) and ci_list(msg["ShoppingCart"], "Groups"):
        return msg
    contents = msg.get("Contents")
    if not isinstance(contents, dict):
        return msg
    groups = []
    for name, group in contents.items():
        if not isinstance(group, dict):
            continue
        gtype = 2 if "purchase" in str(name).lower() or "sale" in str(name).lower() else 1
        items = []
        for it in ci_list(group, "Items"):
            prices = ci(it, "Prices", default={}) or {}
            items.append({"Barcode": ci(it, "Barcode"), "ProductFamily": ci(it, "ProductFamily"),
                          "ProductType": ci(it, "ProductType"), "MultiDisc": ci(it, "MultiDisc"),
                          "ProductId": to_int(ci(it, "ProductId")), "VendStatus": to_int(ci(it, "VendStatus"), 0),
                          "Pricing": prices})
        totals = ci(group, "Totals", default={}) or {}
        groups.append({"GroupType": gtype, "Items": items,
                       "Totals": {"Subtotal": ci(totals, "SubTotal", "Subtotal"), "TaxRate": ci(totals, "TaxRate"),
                                  "TaxAmount": ci(totals, "TaxAmount"), "GrandTotal": ci(totals, "GrandTotal"),
                                  "DiscountedSubtotal": ci(totals, "DiscountedSubtotal")}})
    discounts = []
    if msg.get("DiscountCode"):
        discounts.append({"DiscountType": 1, "PromotionCode": msg["DiscountCode"],
                          "Amount": to_dec(msg.get("DiscountValue"))})
    return dict(msg, ShoppingCart={"Groups": groups, "Discounts": discounts})


def _rental_items(msg):
    for g in ci_list(ci(_ks_cart(msg), "ShoppingCart", default={}), "Groups"):
        if to_int(ci(g, "GroupType"), 0) == 1:
            yield from ci_list(g, "Items")


async def _card_customer(card):
    card_row = await cust.upsert_card(card) if isinstance(card, dict) and card else None
    customer = await cust.customer_by_id(card_row["customer_id"]) if card_row and card_row.get("customer_id") else None
    return card_row, customer


def _credits(customer):
    return to_int(((customer or {}).get("info") or {}).get("credits"), 0) or 0


async def _set_info(customer, **values):
    await db.execute("UPDATE cloud_customers SET info = info || %s, updated_at = now() WHERE customer_id = %s",
                     db.jsonb(values), customer["customer_id"])


def _credit_allocation(msg, customer):
    """Free-rental credits (customer.info.credits) against the rental items of the cart, one per item."""
    available = _credits(customer)
    allocations = []
    for it in _rental_items(msg):
        if len(allocations) >= available:
            break
        allocations.append({"Barcode": ci(it, "Barcode") or "",
                            "Amount": to_dec(ci(ci(it, "Pricing", default={}) or {}, "InitialNight")) or decimal.Decimal("0.00"),
                            "NumberApplied": 1})
    return available, allocations


def _profile(customer, rbi=False):
    return {"Valid": bool(customer), "FirstName": (customer or {}).get("first_name") or "", "IsRBI": rbi}


# ------------------------------------------------------------------ store services / installer

async def ks_GetPendingStates(msg, kiosk):
    _, proxy, _, _ = _routers()
    status, data = await _call(proxy.installer_pending_states, method="GET")
    return {"Success": bool(data.get("Success")), "States": data.get("States", [])}


async def ks_GetPendingBanners(msg, kiosk):
    _, proxy, _, _ = _routers()
    status, data = await _call(proxy.installer_pending_banners, method="GET", stateid=to_int(msg.get("StateId"), 0))
    return {"Success": bool(data.get("Success")), "Banners": data.get("Banners", [])}


async def ks_GetPendingKiosks(msg, kiosk):
    _, proxy, _, _ = _routers()
    status, data = await _call(proxy.installer_pending_kiosks, method="GET", stateid=to_int(msg.get("StateId"), 0),
                               bannerid=to_int(msg.get("BannerId"), 0))
    return {"Success": bool(data.get("Success")), "PendingKiosks": data.get("PendingKiosks", [])}


async def ks_GetOpsMarket(msg, kiosk):
    _, proxy, _, _ = _routers()
    status, data = await _call(proxy.installer_ops_market, method="GET", kioskid=kiosk or 0)
    return {"Success": bool(data.get("Success")), "Errors": _errs(data), "OpsMarket": data.get("OpsMarket"),
            "KaseyaServer": data.get("KaseyaServer")}


# ------------------------------------------------------------------ authentication (field staff)

async def ks_Authenticate(msg, kiosk):
    _, proxy, _, _ = _routers()
    body = {"KioskId": kiosk, "Username": msg.get("Username"), "Password": decrypt_secret(msg.get("Password")),
            "UseNtAuthentication": msg.get("UseNTAuthentication")}
    status, data = await _call(proxy.kiosk_authenticate, body, kiosk)
    if data.get("Success"):
        return {"Success": True}
    return {"Success": False, "Errors": [error("(B004)", "Authentication failed")]}  # AuthenticationResponse.AuthenticationFailed


# ------------------------------------------------------------------ promotions / marketing / personalization

async def ks_DiscountCodeValidation(msg, kiosk):
    _, proxy, _, _ = _routers()
    status, data = await _call(proxy.promo_code_validation, {"MessageId": msg.get("MessageId"), "KioskId": kiosk,
                                                             "Code": msg.get("Code")}, kiosk)
    err = data.pop("Error", None)
    for k in ("MessageId", "KioskId"):
        data.pop(k, None)
    if err:
        return {"Success": False, "Errors": [error(err, "Promotion code not valid")]}
    return dict(data, Success=True)


async def ks_EmailConfirm(msg, kiosk):
    _, proxy, _, _ = _routers()
    email = msg.get("Email")
    status, data = await _call(proxy.marketing_email_confirmation,
                               {"MessageId": msg.get("MessageId"), "KioskId": kiosk, "Email": email,
                                "AccountNumber": msg.get("AccountNumber")}, kiosk)
    if data.get("Success") and msg.get("CardId"):  # 2014-2016: the card hash instead of the account number
        card_row = await cust.upsert_card({"CardId": msg["CardId"]})
        customer = await cust.customer_by_email(email)
        if card_row and customer and not card_row.get("customer_id"):
            await cust.link_card(card_row, customer)
    return {"Success": bool(data.get("Success")), "Errors": _errs(data, "(U001)", status),
            "ConfirmationStatus": data.get("ConfirmationStatus"), "CustomerProfileNumber": data.get("CustomerProfileNumber")}


async def ks_KioskOptIn(msg, kiosk):
    _, proxy, _, _ = _routers()
    items = [i for i in (msg.get("OptInItems") or []) if isinstance(i, dict) and i.get("OptInMethodData")]
    status, data = await _call(proxy.marketing_kiosk_optin, {"MessageId": msg.get("MessageId"), "KioskId": kiosk,
                                                             "OptInItems": items}, kiosk)
    if msg.get("CardHash") and items:  # 2014-2016: link the card to the opted-in e-mail address
        card_row = await cust.upsert_card({"CardId": msg["CardHash"]})
        for i in items:
            customer = await cust.customer_by_email(i["OptInMethodData"]) if "@" in str(i["OptInMethodData"]) else None
            if card_row and customer and not card_row.get("customer_id"):
                await cust.link_card(card_row, customer)
                break
    return {"Success": bool(data.get("Success")), "Errors": _errs(data)}


async def ks_UpdateAccount(msg, kiosk):
    _, proxy, _, _ = _routers()
    body = {"MessageId": msg.get("MessageId"), "KioskId": kiosk, "CustomerProfileNumber": msg.get("CustomerProfileNumber"),
            "Email": msg.get("Email"), "MobilePhoneNumber": msg.get("MobilePhoneNumber"),
            "Pin": decrypt_secret(msg.get("Pin")), "TextClubOptin": msg.get("TextClubOptin"),
            "PunchCardOptin": msg.get("PunchCardOptin")}
    status, data = await _call(proxy.loyalty_update_account, body, kiosk)
    if status >= 400 or data.get("Error"):
        text = str(data.get("Error") or "")
        code = "(UPA001)" if "phone" in text.lower() else "(U001)"  # UpdateAccountResponseErrors.PhoneNumberConflict
        return {"Success": False, "Errors": [error(code, text or f"HTTP {status}")]}
    return {"Success": True, "CustomerProfileNumber": data.get("CustomerProfileNumber"),
            "MobilePhoneNumber": data.get("MobilePhoneNumber"), "TempPassword": data.get("TempPassword")}


async def _estimate(msg, kiosk, fn, not_found):
    status, data = await _call(fn, _ks_cart(msg), kiosk)
    if status >= 400:
        return {"Success": False, "Errors": [error(not_found, data.get("Error") or "Account not found")]}
    return {"Success": True, "Items": data.get("Items", [])}


async def ks_EstimateAccrual(msg, kiosk):
    _, proxy, _, _ = _routers()
    return await _estimate(msg, kiosk, proxy.loyalty_estimate_accrual, "(EA001)")


async def ks_EstimateRedemption(msg, kiosk):
    _, proxy, _, _ = _routers()
    return await _estimate(msg, kiosk, proxy.loyalty_estimate_redemption, "(ER001)")


def _ks_offer(o):
    """KCS CustomerOfferResult -> the dictionary CustomerOfferProxy.ParseOffer reads."""
    out = dict(o)
    # Read with a raw (DateTime) cast, so it must be a .NET date literal; without one the client uses today.
    if out.get("ExpirationDate"):
        out["ExpirationDate"] = ms_date(out["ExpirationDate"])
    else:
        out.pop("ExpirationDate", None)
    dt = out.get("DiscountType")
    names = ["PercentOff", "AmountOff", "Treatment", "Loyalty", "Vip", "Free", "MultiNightPrice"]
    if isinstance(dt, int) and 0 <= dt < len(names):
        out["DiscountType"] = names[dt]
    elif not dt:
        out["DiscountType"] = "AmountOff"
    for src, dst in (("IncludeFormatIds", "IncludeFormats"), ("ExcludeFormatIds", "ExcludeFormats")):
        vals = out.pop(src, None)
        if dst not in out:
            out[dst] = [str(v) for v in (vals or [])]
        else:
            out[dst] = [str(v) for v in (out[dst] or [])]
    for key in ("IncludeTitleIds", "ExcludeTitleIds"):
        out[key] = [to_int(v) for v in (out.get(key) or []) if to_int(v) is not None]
    span = out.get("ExcludeIfWithinRedboxReleaseDate")
    if isinstance(span, (int, float)) and span:
        out["ExcludeIfWithinRedboxReleaseDate"] = {"theTimeSpan": int(span * 864_000_000_000)}  # days -> ticks
    elif not isinstance(span, dict):
        out.pop("ExcludeIfWithinRedboxReleaseDate", None)
    if out.get("DiscountAmount") is not None:
        out["DiscountAmount"] = to_dec(out["DiscountAmount"])
    return out


async def ks_GetCustomerOffers(msg, kiosk):
    transaction, _, _, _ = _routers()
    status, data = await _call(transaction.customer_offers, _ks_cart(msg), kiosk)
    return {"Success": bool(data.get("Success")), "Errors": _errs(data),
            "CustomerProfileNumber": data.get("CustomerProfileNumber"), "AccountNumber": data.get("AccountNumber"),
            "EmailAddress": data.get("EmailAddress"),
            "StoredOffers": [_ks_offer(o) for o in data.get("StoredOffers", [])],
            "NewOffers": [_ks_offer(o) for o in data.get("NewOffers", [])]}


# ------------------------------------------------------------------ digital services (2014-2016: Redbox Instant)

async def ks_GetCustomer(msg, kiosk):
    card_row, customer = await _card_customer({"CardId": msg.get("CardId")} if msg.get("CardId") else None)
    trial = ((customer or {}).get("info") or {}).get("rbi_free_trial")
    return {"Success": True, "IsEligibleFreeTrial": not trial, "Email": (customer or {}).get("email") or "",
            "CustomerProfile": {"HasCustomerProfile": bool(customer), "CPFirstName": (customer or {}).get("first_name") or "",
                                "CPLastName": (customer or {}).get("last_name") or ""}}


async def ks_RBiFreeTrialRequest(msg, kiosk):
    email = msg.get("Email")
    if not email:
        return {"Success": False, "Errors": [error("(A009)", "Email is required")], "IsEligibleFreeTrial": False}
    card = msg.get("CreditCard") or {}
    existed = await cust.customer_by_email(email)
    customer = existed or await cust.create_customer(email=email, first_name=ci(card, "FirstName"),
                                                     last_name=ci(card, "LastName"))
    card_row = await cust.upsert_card(card) if card else None
    await cust.link_card(card_row, customer)
    already = bool((customer.get("info") or {}).get("rbi_free_trial"))
    status = str(msg.get("RequestStatus") or "")
    declined = status.lower() in ("declined", "decline", "rejected", "no", "false", "optedout")
    granted = not already and not declined
    if granted:
        await _set_info(customer, rbi_free_trial={"status": status, "package": msg.get("FreeTrialPackageId"),
                                                  "kiosk": kiosk, "date": now().isoformat()})
    await db.record_event(kiosk, SOURCE, "rbifreetrial", {"Cpn": customer["cpn"], "Status": status, "Granted": granted})
    errs = [error("(A009)", "Free trial already used")] if already and not declined else []
    return {"Success": not errs, "Errors": errs, "IsEligibleFreeTrial": not already and declined,
            "CanCreateProfile": existed is None, "CustomerProfile": {"CanCreateProfile": existed is None}}


async def ks_GetCustomerCredits(msg, kiosk):
    card_row, customer = await _card_customer(msg.get("CreditCard"))
    if customer and customer.get("blocked"):
        return {"Success": False, "Errors": [error("(A002)", "Customer disabled")]}
    available, allocations = _credit_allocation(msg, customer)
    return {"Success": True, "CustomerProfile": _profile(customer),
            "CreditSummary": {"TotalCreditsApplied": len(allocations), "TotalCreditsAvailable": available},
            "CreditAllocations": allocations,
            "GiftCardSummary": {"MaxDiscs": 0, "MaxGames": 0}}


# ------------------------------------------------------------------ merchant services

async def _authorize(msg, kiosk, fn, gift=False):
    body = _ks_cart(msg)
    status, data = await _call(fn, body, kiosk)
    success = bool(data.get("Success"))
    customer = await cust.customer_by_cpn(data.get("CustomerProfileNumber"))
    out = {"Success": success, "Errors": _errs(data, "(A009)", status), "ReferenceNumber": data.get("ReferenceNumber"),
           "Email": data.get("Email") or "", "AccountNumber": data.get("AccountNumber") or "",
           "CustomerProfileNumber": data.get("CustomerProfileNumber") or "",
           "ConfirmationStatus": to_int(data.get("ConfirmationStatus"), 0),
           "CustomerProfile": _profile(customer), "Offers": []}
    if gift:
        g = data.get("GiftCardSummary") or {}
        out["GiftCardSummary"] = {"Exists": bool(g.get("Exists")), "Status": to_int(g.get("Status"), 0),
                                  "Balance": to_dec(g.get("Balance")), "MaxDiscs": to_int(g.get("MaxDiscs"), 0),
                                  "MaxGames": to_int(g.get("MaxGames"), 0)}
    # 2014-2016: free-rental credits are used when the customer opted in (CreditElection.OptedIn).
    available, allocations = _credit_allocation(msg, customer)
    applied = 0
    if success and customer and allocations and str(msg.get("UseCredits") or "") in ("OptedIn", "2"):
        applied = len(allocations)
        await _set_info(customer, credits=available - applied)
        await db.record_event(kiosk, SOURCE, "credits", {"Cpn": customer["cpn"], "Applied": applied,
                                                         "ReferenceNumber": out["ReferenceNumber"]})
        available -= applied
    out["CreditSummary"] = {"TotalCreditsApplied": applied, "TotalCreditsAvailable": available}
    return out


async def ks_Authorize(msg, kiosk):
    transaction, _, _, _ = _routers()
    return await _authorize(msg, kiosk, transaction.authorize)


async def ks_GCAuthorize(msg, kiosk):
    transaction, _, _, _ = _routers()
    return await _authorize(msg, kiosk, transaction.giftcard_authorize, gift=True)


async def ks_ResvAuth(msg, kiosk):
    transaction, _, _, _ = _routers()
    return await _authorize(msg, kiosk, transaction.reservation_authorize)


async def ks_PickupAuthorize(msg, kiosk):
    transaction, _, _, _ = _routers()
    return await _authorize(msg, kiosk, transaction.pickup_authorize)


async def ks_PreAuth(msg, kiosk):
    """Pre-authorisation rule check before the card authorisation. No rule engine here: no prompt, the
    rule is skipped and the amount to authorise is the cart total."""
    transaction, _, _, _ = _routers()
    card_row, customer = await _card_customer(msg.get("CreditCard"))
    if customer is None:
        customer = await cust.customer_by_cpn(msg.get("CustomerProfileNumber"))
    blocked = bool((card_row and card_row.get("blocked")) or (customer and customer.get("blocked")))
    total = transaction.cart_total(_ks_cart(msg))
    await db.record_event(kiosk, SOURCE, "preauth", {"Amount": total, "Blocked": blocked})
    if blocked:
        return {"Success": False, "Errors": [error("(A002)", "Customer disabled")]}
    return {"Success": True, "PromptRequired": False, "AuthRuleId": 0, "AuthAmount": total, "AuthDays": 0,
            "SkipAuthRule": True}


async def ks_PickupValidation(msg, kiosk):
    transaction, _, _, _ = _routers()
    status, data = await _call(transaction.reservation_validate_pickup, msg, kiosk)
    return {"Success": bool(data.get("Success")), "Errors": _errs(data, "(A009)", status),
            "ReferenceNumbers": data.get("ValidReservations") or [], "ValidReservations": data.get("ValidReservations") or [],
            "CancelledReservations": data.get("CancelledReservations") or [], "AllowRop": data.get("AllowRop", True),
            "TransactionEntities": data.get("TransactionEntities") or [],
            "AccountNumber": data.get("AccountNumber") or "", "CustomerProfileNumber": data.get("CustomerProfileNumber") or ""}


async def ks_Reconcile(msg, kiosk):
    transaction, _, _, _ = _routers()
    status, data = await _call(transaction.reconcile, _ks_cart(msg), kiosk)
    return {"Success": bool(data.get("Success")), "Errors": _errs(data, "(U001)", status),
            "CustomerProfileNumber": data.get("CustomerProfileNumber")}


async def ks_PickupReconcile(msg, kiosk):
    transaction, _, _, _ = _routers()
    status, data = await _call(transaction.reservation_pickup, _ks_cart(msg), kiosk)
    return {"Success": bool(data.get("Success")), "Errors": _errs(data, "(U001)", status),
            "CustomerProfileNumber": data.get("CustomerProfileNumber")}


async def ks_ModResvPickup(msg, kiosk):
    """Reservation picked up with a modified cart: the reservation's items become the cart's items (the vend
    itself is reconciled by PickupReconcile)."""
    transaction, _, _, _ = _routers()
    ref = to_int(msg.get("ReferenceNumber"))
    if not ref:
        return {"Success": False, "Errors": [error("(V001)", "ReferenceNumber is required")]}
    items = [{"GroupType": gt, "ProductId": to_int(ci(i, "ProductId")), "Barcode": ci(i, "Barcode"),
              "ProductType": ci(i, "ProductType")} for gt, i in transaction.cart_items(_ks_cart(msg))]
    card_row, customer = await _card_customer(msg.get("CreditCard"))
    if customer is None and msg.get("Email"):
        customer = await cust.customer_by_email(msg["Email"])
    await db.execute(
        "INSERT INTO cloud_reservations (reference_number, kiosk_id, card_key, customer_id, items) VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (reference_number) DO UPDATE SET items = CASE WHEN %s THEN EXCLUDED.items ELSE cloud_reservations.items END, "
        "kiosk_id = COALESCE(cloud_reservations.kiosk_id, EXCLUDED.kiosk_id), "
        "card_key = COALESCE(cloud_reservations.card_key, EXCLUDED.card_key), "
        "customer_id = COALESCE(cloud_reservations.customer_id, EXCLUDED.customer_id), updated_at = now()",
        ref, kiosk, card_row["card_key"] if card_row else None, customer["customer_id"] if customer else None,
        db.jsonb(items), bool(msg.get("ModifiedCart")))
    await db.record_event(kiosk, SOURCE, "modresvpickup", {"ReferenceNumber": ref, "Modified": bool(msg.get("ModifiedCart")),
                                                           "Items": len(items)})
    return {"Success": True}


# ------------------------------------------------------------------ inventory services

async def ks_Return(msg, kiosk):
    transaction, _, _, _ = _routers()
    status, data = await _call(transaction.transaction_return, msg, kiosk)
    return {"Success": bool(data.get("Success")), "Errors": _errs(data, "(U001)", status)}


async def ks_SoftSync(msg, kiosk):
    _, _, inventory, _ = _routers()
    discs = []
    for d in msg.get("Discs") or []:
        if not isinstance(d, dict):
            continue
        st = d.get("Status")
        discs.append(dict(d, Status=INVENTORY_STATUS.get(st, str(st)) if isinstance(st, int) else (st or "In")))
    status, data = await _call(inventory.kiosk_sync, dict(msg, Discs=discs), kiosk)
    return {"Success": bool(data.get("Success")), "Errors": _errs(data, "(U001)", status)}


async def ks_DiscFraud(msg, kiosk):
    _, _, inventory, _ = _routers()
    status, data = await _call(inventory.disc_fraud, msg, kiosk)
    return {"Success": bool(data.get("Success")), "Errors": _errs(data)}


async def ks_DiscAction(msg, kiosk):
    _, _, inventory, _ = _routers()
    status, data = await _call(inventory.disc_action, msg, kiosk)
    return {"Success": bool(data.get("Success")), "Errors": _errs(data)}


async def ks_RentalCount(msg, kiosk):
    """Rentals vended at this kiosk today (UTC) - inferred meaning of GetRentalCount."""
    count = await db.fetchval("SELECT count(*) FROM cloud_rentals WHERE kiosk_id = %s AND group_type = 1 "
                              "AND status IN ('out', 'returned') AND rented_at >= date_trunc('day', now())", kiosk) or 0
    return {"Success": True, "Count": int(count)}


async def ks_UnplayableReturn(msg, kiosk):
    """A customer reported the returned disc as unplayable: recorded as a return of type UnplayableReturn and
    the customer (by e-mail) gets one free-rental credit (inferred from the 2014-2016 credit flow)."""
    barcode = msg.get("Barcode")
    if not barcode:
        return {"Success": False, "Errors": [error("(RTN006)", "Invalid barcode")]}
    rental = await db.fetchrow("SELECT * FROM cloud_rentals WHERE barcode = %s ORDER BY rented_at DESC LIMIT 1", str(barcode))
    await db.execute("INSERT INTO cloud_returns (message_id, kiosk_id, barcode, return_type, return_date, rental_id) "
                     "VALUES (%s, %s, %s, %s, now(), %s)", to_uuid(msg.get("MessageId")), kiosk, str(barcode),
                     RETURN_TYPE_UNPLAYABLE, rental["id"] if rental else None)
    customer = await cust.customer_by_email(msg.get("Email")) if msg.get("Email") else None
    if customer is None and rental:
        customer = await db.fetchrow("SELECT c.* FROM cloud_customers c JOIN cloud_transactions t ON t.customer_id = c.customer_id "
                                     "WHERE t.id = %s", rental["transaction_id"])
    if customer:
        await _set_info(customer, credits=_credits(customer) + 1)
    await db.record_event(kiosk, SOURCE, "unplayable", {"Barcode": barcode, "Cpn": customer["cpn"] if customer else None})
    return {"Success": True}


# ------------------------------------------------------------------ health services (2014-2016)

async def _kioskdata(kind, body, kiosk):
    _, _, _, kioskdata = _routers()
    status, data = await _call(kioskdata.kcs_kiosk_data, body, kiosk, kind=kind)
    return bool(data.get("Success"))


async def ks_KioskPing(msg, kiosk):
    """Health ping: versions and statistics are merged into kiosks.info like the KCS KioskVersion /
    KioskStatistics posts; the reply carries ServerTime (the kiosk sets its clock when it is 5 min off)."""
    ok = True
    if isinstance(msg.get("Versions"), dict) and msg["Versions"]:
        ok &= await _kioskdata("kioskversion", {"KioskId": kiosk, "MessageId": msg.get("MessageId"),
                                                "Versions": msg["Versions"]}, kiosk)
    if isinstance(msg.get("Statistics"), dict) and msg["Statistics"]:
        ok &= await _kioskdata("kioskstatistics", {"KioskId": kiosk, "MessageId": msg.get("MessageId"),
                                                   "Statistics": msg["Statistics"]}, kiosk)
    if kiosk:
        await db.touch_kiosk(kiosk, info={"engine_version": msg.get("EngineVersion"),
                                          "bundle_version": msg.get("BundleVersion"), "last_ping": now().isoformat()})
    await db.record_event(kiosk, "health", "ping", {"EngineVersion": msg.get("EngineVersion"),
                                                     "BundleVersion": msg.get("BundleVersion")})
    return {"Success": ok, "ServerTime": redbox_datetime(now())}


async def ks_KioskAlert(msg, kiosk):
    body = {"KioskId": kiosk, "MessageId": msg.get("MessageId"), "AlertType": ALERT_TYPES.get(str(msg.get("Type") or "").lower(), 0),
            "Type": msg.get("Type"), "SubType": msg.get("SubType"), "Message": msg.get("Detail"), "DateTime": msg.get("Time")}
    return {"Success": await _kioskdata("kioskalert", body, kiosk)}


async def ks_AuditEvent(msg, kiosk):
    body = {"KioskId": kiosk, "MessageId": msg.get("MessageId"), "EventType": msg.get("EventType"),
            "LogDate": msg.get("LogDate"), "UserName": msg.get("UserName"), "Message": msg.get("Message"),
            "Source": msg.get("Source")}
    return {"Success": await _kioskdata("auditevent", body, kiosk)}


HANDLERS = {name[3:]: fn for name, fn in dict(globals()).items() if name.startswith("ks_") and callable(fn)}
HANDLERS_CI = {k.lower(): v for k, v in HANDLERS.items()}
