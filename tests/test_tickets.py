"""Ticket Controller Service (2014 Ticket Services, routers/tickets.py): SOAP 1.1 as the WCF client sends it.

Requests are hand-written in the shape WCF's DataContractSerializer produces; replies are checked for element
names, namespaces and member order (base contract first, then alphabetical), which DataContractSerializer needs.
Each test creates its own production and ticket groups so the shared database's sample data is not used up.
"""
import os
import random
import sys
import uuid
import xml.etree.ElementTree as ET

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "kcs"))
import cloudclient as cc  # noqa: E402

pytestmark = pytest.mark.skipif(not cc.database_available(), reason="DATABASE_URL not reachable")

PATH = "/TicketControllerService/ControllerService.svc"
SOAP = "http://schemas.xmlsoap.org/soap/envelope/"
SVC = "http://www.redbox.com/ControllerServices/Tickets/2012/04"
SCH = "http://schemas.datacontract.org/2004/07/Redbox.Ticketing.ControllerServices.Contract.Schema"
I = "http://www.w3.org/2001/XMLSchema-instance"
ARR = "http://schemas.microsoft.com/2003/10/Serialization/Arrays"
SUB = {"GetTickets": ".GetTickets", "PlaceOrder": ".PlaceOrder", "UpdateTicketLock": ".UpdateTicketLock",
       "ReleaseTicketLock": ".ReleaseTicketLock", "SendNotification": ".SendNotification",
       "GetEmailByCard": ".GetEmailByCard"}


@pytest.fixture(scope="module")
def c():
    return cc.client()


@pytest.fixture
def prod(c):
    """A fresh production: group A (4 seats, display 1) and group B (10 seats), price codes 1 and 2."""
    pid = random.randint(10**8, 2 * 10**9)
    ga, gb = random.randint(10**8, 2 * 10**9), random.randint(10**8, 2 * 10**9)
    c.sql("INSERT INTO ticket_productions (production_id, venue_name, on_sale_at, off_sale_at, ticket_limit_per_order) "
          "VALUES (%s, 'Test Venue', now() - interval '1 hour', now() + interval '1 day', 8)", pid)
    c.sql("INSERT INTO ticket_price_codes VALUES (%s, 1, 'Adult'), (%s, 2, 'Child')", pid, pid)
    for gid, seats, order, note in ((ga, 4, 1, 1), (gb, 10, 2, None)):
        c.sql("INSERT INTO ticket_groups (ticket_group_id, production_id, supplier_note_log_id, zone, level, section, "
              "row_name, display_order, total_tickets) VALUES (%s, %s, %s, 'Z', '1', 'S', 'R', %s, %s)",
              gid, pid, note, order, seats)
        c.sql("INSERT INTO ticket_group_prices VALUES (%s, 1, 40.00, 5.00, 0, 0.40), (%s, 2, 20.00, 5.00, 0, 0.40)",
              gid, gid)
    yield {"id": pid, "a": ga, "b": gb}
    c.sql("DELETE FROM ticket_productions WHERE production_id = %s", pid)


# ------------------------------------------------------------------ request builders (WCF shape)

def header(kiosk, message_id=None):
    return (f'<BundleVersion xmlns="{SCH}">2.0.0.0</BundleVersion><ChannelType xmlns="{SCH}">Kiosk</ChannelType>'
            f'<EngineVersion xmlns="{SCH}">4.0.0.0</EngineVersion><KioskId xmlns="{SCH}">{kiosk}</KioskId>'
            f'<MessageDateTimeUtc xmlns="{SCH}">2014-03-01T12:00:00.1234567-06:00</MessageDateTimeUtc>'
            f'<MessageId xmlns="{SCH}">{message_id or uuid.uuid4()}</MessageId>')


def unlock_xml(unlocks):
    return "".join(f'<UnlockTicket xmlns="{SCH}"><Quantity>{q}</Quantity><TicketGroupGuid>{g}</TicketGroupGuid>'
                   f"<TicketGroupId>{gid}</TicketGroupId></UnlockTicket>" for gid, g, q in unlocks)


def call(c, op, inner, action=None):
    param = op[0].lower() + op[1:] + "Request"
    xml = (f'<s:Envelope xmlns:s="{SOAP}"><s:Body><{op} xmlns="{SVC}"><{param} xmlns:i="{I}">{inner}</{param}>'
           f"</{op}></s:Body></s:Envelope>")
    r = c.run(c.http.post(PATH, content=xml.encode(), headers={
        "content-type": "text/xml; charset=utf-8", "SOAPAction": f'"{action or SVC + "/IControllerService/" + op}"'}))
    assert r.headers["content-type"].startswith("text/xml")
    return r, ET.fromstring(r.content)


def result(c, op, inner):
    r, root = call(c, op, inner)
    assert r.status_code == 200, r.text
    body = root.find(f"{{{SOAP}}}Body")
    wrapper = body[0]
    assert wrapper.tag == f"{{{SVC}}}{op}Response"
    res = wrapper[0]
    assert res.tag == f"{{{SVC}}}{op}Result"
    return res


def fault(c, op, inner):
    r, root = call(c, op, inner)
    assert r.status_code == 500
    f = root.find(f"{{{SOAP}}}Body/{{{SOAP}}}Fault")
    detail = f.find("detail")
    return f.findtext("faultstring"), (detail[0] if detail is not None and len(detail) else None)


def get_tickets(c, kiosk, pid, codes, unlocks=(), message_id=None):
    pcs = "".join(f"<PriceCode><PriceCodeId>{i}</PriceCodeId><Quantity>{n}</Quantity></PriceCode>" for i, n in codes)
    ns = SCH + ".GetTickets"
    inner = (header(kiosk, message_id) +f'<PriceCodes xmlns="{ns}">{pcs}</PriceCodes><ProductionId xmlns="{ns}">{pid}</ProductionId>'
             f'<UnlockTickets xmlns="{ns}">{unlock_xml(unlocks)}</UnlockTickets>')
    return inner


def lock_req(kiosk, gid, guid, qty):
    return (header(kiosk) + f'<LockGuid xmlns="{SCH}">{guid}</LockGuid><Quantity xmlns="{SCH}">{qty}</Quantity>'
            f'<TicketGroupId xmlns="{SCH}">{gid}</TicketGroupId>')


def place_order(kiosk, items, totals, email="buyer@example.test", card_hash=None, message_id=None, unlocks=(),
                track2=";4111111111111111=39121010000000000000?"):
    ns = SCH + ".PlaceOrder"
    sub, fee, tax, total = totals
    item_xml = ""
    for gid, guid, codes in items:
        pcs = "".join(f"<OrderPriceCode><FeePerTicket>{f}</FeePerTicket><FeePerTicketTax>{ft}</FeePerTicketTax>"
                      f"<OrderTaxCollection><OrderTaxItem><TaxAmt>{ft}</TaxAmt><TaxTypeId>1</TaxTypeId></OrderTaxItem>"
                      f"</OrderTaxCollection><PriceCodeId>{pc}</PriceCodeId><PricePerTicket>{p}</PricePerTicket>"
                      f"<PricePerTicketTax>0</PricePerTicketTax><Quantity>{q}</Quantity></OrderPriceCode>"
                      for pc, q, p, f, ft in codes)
        item_xml += (f"<OrderItem><DeliverToName>Test Buyer</DeliverToName><DeliveryMethodId>2</DeliveryMethodId>"
                     f"<LockGuid>{guid}</LockGuid><PriceCodes>{pcs}</PriceCodes><SuggestionTypeId>1</SuggestionTypeId>"
                     f'<SupplierNoteLogId i:nil="true"/><TicketGroupId>{gid}</TicketGroupId></OrderItem>')
    order = (f'<Order xmlns="{ns}"><Customer><Email>{email}</Email></Customer><OrderHeader><OrderId>1</OrderId>'
             f"<Subtotal>{sub}</Subtotal><Total>{total}</Total><TotalFee>{fee}</TotalFee><TotalTax>{tax}</TotalTax>"
             f"</OrderHeader><OrderItems>{item_xml}</OrderItems><Payment><EncryptedCardNumber>ENC</EncryptedCardNumber>"
             f"<ExpirationMonth>12</ExpirationMonth><ExpirationYear>39</ExpirationYear><FirstName>Buyer</FirstName>"
             f"<HashedCardId>{card_hash or uuid.uuid4().hex}</HashedCardId><KeyId>1</KeyId><LastName>Test</LastName>"
             f"<PostalCode>60601</PostalCode><Track2>{track2}</Track2></Payment>"
             f'<PromotionList xmlns:b="{ARR}"><b:int>7</b:int></PromotionList></Order>')
    return header(kiosk, message_id) + order + f'<UnlockTickets xmlns="{ns}">{unlock_xml(unlocks)}</UnlockTickets>'


def local(el):
    return el.tag.rsplit("}", 1)[-1]


def text(el, name):
    child = next(ch for ch in el if local(ch) == name)
    return None if child.get(f"{{{I}}}nil") == "true" else child.text


def groups(res):
    tg = next(ch for ch in res if local(ch) == "TicketGroups")
    return [{local(ch): ch for ch in g} for g in tg]


def available(c, gid):
    return c.sqlval("SELECT total_tickets - sold - coalesce((SELECT sum(quantity) FROM ticket_locks l WHERE "
                    "l.ticket_group_id = g.ticket_group_id AND status = 'held' AND expires_at > now()), 0) "
                    "FROM ticket_groups g WHERE ticket_group_id = %s", gid)


# ------------------------------------------------------------------ tests

def test_get_tickets_shape_and_locks(c, prod):
    from redbox_backend.cloudapi.routers import tickets as t
    kiosk = cc.new_kiosk()
    res = result(c, "GetTickets", get_tickets(c, kiosk, prod["id"], [(1, 2), (2, 1)]))
    # Member order and namespaces exactly as DataContractSerializer expects.
    assert [(el.tag.split("}")[0][1:], local(el)) for el in res] == [
        (ns, m) for m, _, _, ns in t.all_members("GetTicketsResponse")]
    assert text(res, "Success") == "true" and text(res, "Code") is None
    gs = groups(res)
    assert [int(g["TicketGroupId"].text) for g in gs] == [prod["a"], prod["b"]]
    first = next(ch for ch in next(ch for ch in res if local(ch) == "TicketGroups"))
    assert first.tag == f"{{{SCH}.GetTickets}}TicketGroup"
    assert [local(ch) for ch in first] == [m for m, _, _, _ in t.all_members("TicketGroup")]
    pcr = list(gs[0]["PriceCodes"])
    assert [local(p) for p in pcr] == ["PriceCodeResponse", "PriceCodeResponse"]
    assert [local(ch) for ch in pcr[0]] == [m for m, _, _, _ in t.all_members("PriceCodeResponse")]
    assert text(pcr[0], "PricePerTicket") == "40.00" and text(pcr[0], "Quantity") == "2"
    assert gs[1]["SupplierNoteLogId"].get(f"{{{I}}}nil") == "true"
    assert gs[0]["LockDuration"].text == "5"
    disc = list(next(ch for ch in res if local(ch) == "SupplierDisclaimers"))
    assert [d.tag for d in disc] == [f"{{{SVC}}}SupplierDisclaimer"] and text(disc[0], "SupplierNoteLogId") == "1"
    # Each suggested group holds 3 seats.
    assert available(c, prod["a"]) == 1 and available(c, prod["b"]) == 7
    # A new search that releases the previous locks frees them again.
    unlocks = [(int(g["TicketGroupId"].text), g["LockGuid"].text, 3) for g in gs]
    res2 = result(c, "GetTickets", get_tickets(c, kiosk, prod["id"], [(1, 5)], unlocks))
    assert [int(g["TicketGroupId"].text) for g in groups(res2)] == [prod["b"]]  # A has only 4 seats
    assert available(c, prod["a"]) == 4 and available(c, prod["b"]) == 5
    # 6 more: B has 5 left, A 4 -> not contiguous.
    res3 = result(c, "GetTickets", get_tickets(c, kiosk, prod["id"], [(1, 6)]))
    assert text(res3, "Success") == "false" and text(res3, "Code") == "102"
    assert len(groups(res3)) == 0


def test_validation_faults(c, prod):
    kiosk = cc.new_kiosk()
    msg, detail = fault(c, "GetTickets", get_tickets(c, kiosk, 1, [(1, 1)]))
    assert "Unknown ProductionId" in msg
    assert detail.tag == f"{{{SCH}}}ValidationError"
    assert [local(ch) for ch in detail] == ["ErrorCorrelationId", "Message", "MessageId"]
    msg, _ = fault(c, "GetTickets", get_tickets(c, kiosk, prod["id"], [(9, 1)]))
    assert "PriceCodeId" in msg
    msg, _ = fault(c, "GetTickets", get_tickets(c, kiosk, prod["id"], [(1, 9)]))
    assert "At most 8" in msg
    msg, detail = fault(c, "ReleaseTicketLock", lock_req(kiosk, prod["a"], uuid.uuid4(), 1))
    assert "Unknown LockGuid" in msg and detail.tag == f"{{{SCH}}}ValidationError"
    r, root = call(c, "GetTickets", get_tickets(c, kiosk, prod["id"], [(1, 1)]), action=SVC + "/IControllerService/Nope")
    assert r.status_code == 500 and "ActionNotSupported" in r.text


def test_general_error_fault(c, prod, monkeypatch):
    from redbox_backend.cloudapi.routers import tickets as t

    async def boom(req):
        raise RuntimeError("database on fire")
    monkeypatch.setitem(t.HANDLERS, "GetTickets", boom)
    mid = uuid.uuid4()
    msg, detail = fault(c, "GetTickets", get_tickets(c, cc.new_kiosk(), prod["id"], [(1, 1)], message_id=mid))
    assert detail.tag == f"{{{SVC}}}GeneralError"
    assert [ch.tag for ch in detail] == [f"{{{SVC}}}{m}" for m in ("ErrorCorrelationId", "Message", "MessageId")]
    assert "fire" not in msg and text(detail, "MessageId") == str(mid)


def test_update_and_release_lock(c, prod):
    kiosk = cc.new_kiosk()
    g = groups(result(c, "GetTickets", get_tickets(c, kiosk, prod["id"], [(1, 2)])))[0]
    gid, guid = int(g["TicketGroupId"].text), g["LockGuid"].text
    res = result(c, "UpdateTicketLock", lock_req(kiosk, gid, guid, 2))
    assert [local(ch) for ch in res] == ["Code", "Message", "Success", "LockDuration"]
    assert text(res, "Success") == "true" and text(res, "LockDuration") == "5"
    # An expired lock is renewed while the seats are still free ...
    c.sql("UPDATE ticket_locks SET expires_at = now() - interval '1 minute' WHERE lock_guid = %s", guid)
    assert text(result(c, "UpdateTicketLock", lock_req(kiosk, gid, guid, 2)), "Success") == "true"
    # ... and fails with LockExpiredRelockFailed when somebody else took them.
    c.sql("UPDATE ticket_locks SET expires_at = now() - interval '1 minute' WHERE lock_guid = %s", guid)
    other = groups(result(c, "GetTickets", get_tickets(c, kiosk, prod["id"], [(1, 4)])))
    assert int(other[0]["TicketGroupId"].text) == prod["a"]
    res = result(c, "UpdateTicketLock", lock_req(kiosk, gid, guid, 2))
    assert text(res, "Success") == "false" and text(res, "Code") == "154"
    res = result(c, "ReleaseTicketLock", lock_req(kiosk, gid, guid, 2))
    assert [local(ch) for ch in res] == ["Code", "Message", "Success"] and text(res, "Success") == "true"
    assert c.sqlval("SELECT status FROM ticket_locks WHERE lock_guid = %s", guid) == "released"


def order_lines(c, kiosk, prod, adults=2):
    gs = groups(result(c, "GetTickets", get_tickets(c, kiosk, prod["id"], [(1, adults)])))
    chosen, rest = gs[0], gs[1:]
    gid, guid = int(chosen["TicketGroupId"].text), chosen["LockGuid"].text
    unlocks = [(int(g["TicketGroupId"].text), g["LockGuid"].text, adults) for g in rest]
    sub, fee, tax = 40 * adults, 5 * adults, 0.4 * adults
    return gid, guid, unlocks, [(1, adults, "40.00", "5.00", "0.40")], (f"{sub:.2f}", f"{fee:.2f}", f"{tax:.2f}",
                                                                        f"{sub + fee + tax:.2f}")


def test_place_order_and_email_by_card(c, prod):
    kiosk = cc.new_kiosk()
    gid, guid, unlocks, codes, totals = order_lines(c, kiosk, prod)
    card_hash, email = uuid.uuid4().hex, f"t{uuid.uuid4().hex[:8]}@example.test"
    # Totals that do not add up are a ValidationError.
    msg, detail = fault(c, "PlaceOrder", place_order(kiosk, [(gid, guid, codes)], ("1.00",) + totals[1:]))
    assert "Subtotal" in msg and detail.tag == f"{{{SCH}}}ValidationError"
    mid = uuid.uuid4()
    res = result(c, "PlaceOrder", place_order(kiosk, [(gid, guid, codes)], totals, email=email, card_hash=card_hash,
                                              message_id=mid, unlocks=unlocks))
    assert [local(ch) for ch in res] == ["Code", "Message", "Success", "OrderId"]
    assert text(res, "Success") == "true"
    order_id = int(text(res, "OrderId"))
    row = c.sql("SELECT * FROM ticket_orders WHERE order_id = %s", order_id)[0]
    assert row["status"] == "approved" and str(row["total"]) == totals[3] and row["card_last_four"] == "1111"
    assert c.sqlval("SELECT sold FROM ticket_groups WHERE ticket_group_id = %s", gid) == 2
    assert c.sqlval("SELECT status FROM ticket_locks WHERE lock_guid = %s", guid) == "sold"
    for ugid, uguid, _ in unlocks:
        assert c.sqlval("SELECT status FROM ticket_locks WHERE lock_guid = %s", uguid) == "released"
    # Replayed message: same order, nothing sold twice.
    res = result(c, "PlaceOrder", place_order(kiosk, [(gid, guid, codes)], totals, email=email, card_hash=card_hash,
                                              message_id=mid))
    assert int(text(res, "OrderId")) == order_id
    assert c.sqlval("SELECT sold FROM ticket_groups WHERE ticket_group_id = %s", gid) == 2
    # The sold lock cannot be ordered again.
    msg, _ = fault(c, "PlaceOrder", place_order(kiosk, [(gid, guid, codes)], totals))
    assert "already been sold" in msg
    # The card now resolves to the buyer's e-mail.
    ns = SCH + ".GetEmailByCard"
    res = result(c, "GetEmailByCard", header(kiosk) + f'<HashedCreditCardNumber xmlns="{ns}">{card_hash}'
                                                      "</HashedCreditCardNumber>")
    assert [local(ch) for ch in res] == ["Code", "Message", "Success", "Email"]
    assert text(res, "Success") == "true" and text(res, "Email") == email
    res = result(c, "GetEmailByCard", header(kiosk) + f'<HashedCreditCardNumber xmlns="{ns}">unknown-{card_hash}'
                                                      "</HashedCreditCardNumber>")
    assert text(res, "Success") == "false" and text(res, "Email") is None


def test_place_order_declined(c, prod, monkeypatch):
    from redbox_backend import config
    kiosk = cc.new_kiosk()
    gid, guid, unlocks, codes, totals = order_lines(c, kiosk, prod)
    monkeypatch.setattr(config, "PAYMENT_MODE", "decline")
    res = result(c, "PlaceOrder", place_order(kiosk, [(gid, guid, codes)], totals))
    assert text(res, "Success") == "false" and text(res, "Code") == "161" and text(res, "OrderId") == "0"
    assert c.sqlval("SELECT sold FROM ticket_groups WHERE ticket_group_id = %s", gid) == 0
    assert c.sqlval("SELECT status FROM ticket_locks WHERE lock_guid = %s", guid) == "held"
    monkeypatch.setattr(config, "PAYMENT_MODE", "rules")
    res = result(c, "PlaceOrder", place_order(kiosk, [(gid, guid, codes)], totals,
                                              track2=";4111111111110003=39121010000000000000?"))
    assert text(res, "Code") == "151"
    res = result(c, "PlaceOrder", place_order(kiosk, [(gid, guid, codes)], totals))
    assert text(res, "Success") == "true"


def test_sold_out(c, prod):
    kiosk = cc.new_kiosk()
    c.sql("UPDATE ticket_groups SET sold = total_tickets WHERE production_id = %s", prod["id"])
    res = result(c, "GetTickets", get_tickets(c, kiosk, prod["id"], [(1, 1)]))
    assert text(res, "Success") == "false" and text(res, "Code") == "103"


def test_send_notification(c):
    kiosk = cc.new_kiosk()
    ns = SCH + ".SendNotification"
    email = f"n{uuid.uuid4().hex[:8]}@example.test"
    subs = (f"<Subscription><NotificationType>Email</NotificationType><SubscriptionType>Event</SubscriptionType>"
            f'<SubscriptionValue>5001</SubscriptionValue></Subscription><Subscription i:nil="true"/>')
    inner = (header(kiosk) + f'<CustomerEmail xmlns="{ns}">{email}</CustomerEmail>'
             f'<CustomerSMSPhone xmlns="{ns}" i:nil="true"/><Subscriptions xmlns="{ns}">{subs}</Subscriptions>')
    res = result(c, "SendNotification", inner)
    assert [local(ch) for ch in res] == ["Code", "Message", "Success"] and text(res, "Success") == "true"
    row = c.sql("SELECT * FROM ticket_notifications WHERE email = %s", email)[0]
    assert row["kiosk_id"] == kiosk and row["subscriptions"] == [
        {"NotificationType": "Email", "SubscriptionType": "Event", "SubscriptionValue": "5001"}]
    msg, _ = fault(c, "SendNotification", header(kiosk) + f'<CustomerEmail xmlns="{ns}" i:nil="true"/>'
                                                          f'<CustomerSMSPhone xmlns="{ns}" i:nil="true"/>'
                                                          f'<Subscriptions xmlns="{ns}"/>')
    assert "required" in msg


def test_wsdl(c):
    r = c.get(PATH + "?wsdl")
    assert r.status_code == 200
    root = ET.fromstring(r.content)
    ops = root.findall("{http://schemas.xmlsoap.org/wsdl/}portType/{http://schemas.xmlsoap.org/wsdl/}operation")
    assert sorted(o.get("name") for o in ops) == sorted(SUB)


def test_plain_http_listener():
    """The running rb-cloudapi container answers on plain HTTP port 80 too (skipped outside the stack)."""
    import httpx
    url = os.environ.get("CLOUDAPI_HTTP_URL", "http://rb-cloudapi:80")
    try:
        r = httpx.get(url + PATH + "?wsdl", timeout=3)
    except httpx.HTTPError:
        pytest.skip(f"{url} not reachable")
    assert r.status_code == 200 and b"IControllerService" in r.content
