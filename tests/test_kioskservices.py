"""Kiosk Services MessageService (engines 1.81 and older) through the REST transport:
POST /KioskServices/MessageService.rem, form `payload=<url-encoded JSON>`, URL-encoded JSON reply.

Every MessageType of cloudapi/kioskservices.py is sent the way the Kiosk Services proxies build it (Redbox.Core
DateTime objects, enums as numbers, 2014-2016 `Contents` carts and 2024 `ShoppingCart`s), and the state it
leaves in the cloud_* tables is checked.
"""
import hashlib
import json
import os
import sys
import urllib.parse
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "kcs"))
import cloudclient as cc  # noqa: E402

pytestmark = pytest.mark.skipif(not cc.database_available(), reason="DATABASE_URL not reachable")

PATH = "/KioskServices/MessageService.rem"


@pytest.fixture(scope="module")
def c():
    return cc.client()


def ks(c, message_type, kiosk=None, **fields):
    """Send one message like KioskServicesProxy.PerformRemoteMessage (REST) and decode the reply."""
    from redbox_backend.dotnet import redbox_datetime
    msg = {"MessageType": message_type, "MessageId": fields.pop("MessageId", str(uuid.uuid4())),
           "EngineVersion": "1.81.0.27", "BundleVersion": "4.5.0.0"}
    if kiosk:
        msg["KioskId"] = kiosk
    msg.update(fields)
    if "TransactionDate" not in msg and message_type in ("Authorize", "GCAuthorize", "Reconcile", "ResvAuth",
                                                          "PickupAuthorize", "PickupValidation", "PreAuth"):
        msg["TransactionDate"] = redbox_datetime(kind="Local")
    body = "payload=" + urllib.parse.quote_plus(json.dumps(msg))
    r = c.run(c.http.post(PATH, content=body.encode(), headers={"content-type": "application/x-www-form-urlencoded"}))
    assert r.status_code == 200, r.text
    reply = json.loads(urllib.parse.unquote_plus(r.text))
    assert isinstance(reply.get("Success"), bool)
    assert reply["IsOnline"] is True and reply["ServerBusy"] is False and reply["ServerName"]
    return reply


def code(reply):
    return reply["Errors"][0]["Code"] if reply.get("Errors") else None


def ks_card(**kw):
    """CreditCard dictionary of ProxyMessageHelper.GetCreditCardData (2024) / the 2016 proxies."""
    card = cc.card(**kw)
    card.update({"KeyId": "1", "Number": None, "KSN": None, "Track2": "", "EmvTags": None, "CardSourceType": 2})
    return card


def contents_cart(items):
    """2014-2016 `Contents` (group name -> Items with Prices, Totals)."""
    total = round(sum(i["price"] for i in items), 2)
    return {"rental": {"Items": [{"Barcode": i["barcode"], "ProductFamily": "Movie", "ProductType": "DVD",
                                  "MultiDisc": False, "VendStatus": i.get("vend", 0),
                                  "Prices": {"InitialNight": i["price"], "ExtraNight": i["price"], "NonReturn": 25.0}}
                                 for i in items],
                       "Totals": {"TaxRate": 0.0, "SubTotal": total, "TaxAmount": 0.0, "GrandTotal": total,
                                  "DiscountedSubtotal": total}}}


def new_email():
    return f"ks-{uuid.uuid4().hex[:10]}@example.test"


# ------------------------------------------------------------------ transport / dispatch

def test_unknown_message_type(c):
    r = ks(c, "NoSuchMessage", cc.new_kiosk())
    assert r["Success"] is False and code(r) == "(U001)"


def test_events_recorded_for_every_message(c):
    kiosk = cc.new_kiosk()
    ks(c, "RentalCount", kiosk)
    rows = c.sql("SELECT kind, payload FROM events WHERE kiosk_id = %s AND source = 'kioskservices'", kiosk)
    assert any(r["kind"] == "RentalCount" and r["payload"]["transport"] == "rest" for r in rows)


# ------------------------------------------------------------------ store / installer services

def test_installer_pending_states_banners_kiosks_ops_market(c):
    kid = cc.new_kiosk()
    state_id = 9000 + kid % 900
    banner_id = kid
    c.sql("INSERT INTO cloud_installer_kiosks (id, state_id, state_name, banner_id, banner_name, address, city, zip, "
          "due_time, market_name, kaseya_market_name) VALUES (%s, %s, %s, %s, %s, '1 Main St', 'Springfield', '12345', "
          "'10:00', 'Test Market', 'TESTMKT') ON CONFLICT (id) DO NOTHING", kid, state_id, f"State {state_id}",
          banner_id, f"Banner {banner_id}")
    states = ks(c, "GetPendingStates")
    assert states["Success"] and {"Id": state_id, "Description": f"State {state_id}"} in states["States"]
    banners = ks(c, "GetPendingBanners", StateId=state_id)
    assert {"Id": banner_id, "Name": f"Banner {banner_id}"} in banners["Banners"]
    kiosks = ks(c, "GetPendingKiosks", StateId=state_id, BannerId=banner_id)["PendingKiosks"]
    assert kiosks[0]["Id"] == kid and kiosks[0]["City"] == "Springfield" and kiosks[0]["KaseyaMarketName"] == "TESTMKT"
    assert ks(c, "GetOpsMarket", kid)["Success"] is False  # no market known yet
    c.sql("INSERT INTO cloud_kiosk_locations (kiosk_id, ops_market, kaseya_server) VALUES (%s, 'Chicago', 'Kaseya9')",
          kid)
    r = ks(c, "GetOpsMarket", kid)
    assert r["Success"] and r["OpsMarket"] == "Chicago" and r["KaseyaServer"] == "Kaseya9"


def test_authenticate_with_encrypted_password(c):
    from redbox_backend.cloudapi.kioskservices import decrypt_secret, encrypt_secret
    # Produced by .NET Framework TripleDESCryptoServiceProvider with the EncryptionHelper key/IV.
    assert decrypt_secret("zjCtXpJIg8SOjljQTGZcqw==") == "s3cret-Pw!"
    kiosk, user, pw = cc.new_kiosk(), "ks" + uuid.uuid4().hex[:8], "pw-" + uuid.uuid4().hex[:6]
    c.sql("INSERT INTO cloud_staff_users (username, password_sha256) VALUES (%s, %s)", user,
          hashlib.sha256(pw.encode()).hexdigest())
    try:
        ok = ks(c, "Authenticate", kiosk, Username=user, Password=encrypt_secret(pw), UseNTAuthentication=False)
        assert ok["Success"] is True
        bad = ks(c, "Authenticate", kiosk, Username=user, Password=encrypt_secret("wrong"), UseNTAuthentication=False)
        assert bad["Success"] is False and code(bad) == "(B004)"
    finally:
        c.sql("DELETE FROM cloud_staff_users WHERE username = %s", user)


# ------------------------------------------------------------------ promotions / marketing / personalization

def test_discount_code_validation(c):
    kiosk = cc.new_kiosk()
    r = ks(c, "DiscountCodeValidation", kiosk, Code="welcome")
    assert r["Success"] and r["PromotionIntentCode"] == "FirstNightDVD" and "Error" not in r
    assert code(ks(c, "DiscountCodeValidation", kiosk, Code="NOPE")) == "(D001)"
    limited = f"KS{kiosk}"
    c.sql("INSERT INTO cloud_promo_codes (code, kiosk_ids, data) VALUES (%s, %s, '{\"Amount\": 2.5}')", limited, [kiosk])
    assert ks(c, "DiscountCodeValidation", kiosk, Code=limited)["Amount"] == 2.5
    assert code(ks(c, "DiscountCodeValidation", kiosk + 1, Code=limited)) == "(D002)"


def test_email_confirm_creates_customer_and_links_card(c):
    from redbox_backend.dotnet import redbox_datetime
    kiosk, email, card_id = cc.new_kiosk(), new_email(), str(uuid.uuid4())
    r = ks(c, "EmailConfirm", kiosk, Email=email, CardId=card_id, UseMessageControl=False,
           TransactionDate={"theDate": redbox_datetime()["theDate"], "dateTimeKind": "Unspecified",
                            "dateString": "2026-15-27T09:15:44"})  # the client's broken "yyyy-mm-dd" format
    assert r["Success"] and r["ConfirmationStatus"] == 5  # NewCustomer
    cust = c.sql("SELECT * FROM cloud_customers WHERE email = %s", email)[0]
    assert r["CustomerProfileNumber"] == cust["cpn"]
    from redbox_backend.cloudapi.customers import card_key
    assert c.sqlval("SELECT customer_id FROM cloud_cards WHERE card_key = %s", card_key({"CardId": card_id})) == \
        cust["customer_id"]
    assert ks(c, "EmailConfirm", kiosk, Email=email)["ConfirmationStatus"] == 3  # NotSubscribedConfirmed


def test_kiosk_opt_in(c):
    from redbox_backend.dotnet import redbox_datetime
    kiosk, email = cc.new_kiosk(), new_email()
    ks(c, "EmailConfirm", kiosk, Email=email)
    r = ks(c, "KioskOptIn", kiosk, RequestDate=redbox_datetime(kind="Local"), CardHash=str(uuid.uuid4()),
           OptInItems=[{"ItemId": 7, "Item_Type": 1, "OptInMethodType": 1, "OptInMethodData": email,
                        "TemplateName": "t", "CampaignName": "Fall"},
                       {"ItemId": 8, "Item_Type": 1, "OptInMethodData": None}])
    assert r["Success"]
    opt_ins = c.sqlval("SELECT opt_ins FROM cloud_customers WHERE email = %s", email)
    assert {"ItemId": 7, "Type": 1, "Campaign": "Fall"} in opt_ins


def test_update_account_signup_pin_and_conflict(c):
    from redbox_backend.cloudapi.kioskservices import encrypt_secret
    kiosk, email = cc.new_kiosk(), new_email()
    phone = "312" + str(uuid.uuid4().int)[:7]
    r = ks(c, "UpdateAccount", kiosk, Email=email, MobilePhoneNumber=phone, Pin=encrypt_secret("4321"),
           PunchCardOptin=True, TextClubOptin=True, AccountSourceType=1, CustomerProfileNumber=None)
    assert r["Success"] and r["CustomerProfileNumber"] and r["TempPassword"] and r["MobilePhoneNumber"] == phone
    row = c.sql("SELECT * FROM cloud_customers WHERE cpn = %s", r["CustomerProfileNumber"])[0]
    assert row["pin"] == "4321" and row["loyalty_enrolled"] and row["text_club"]
    clash = ks(c, "UpdateAccount", kiosk, Email=new_email(), MobilePhoneNumber=phone, Pin=None,
               PunchCardOptin=True, TextClubOptin=False, AccountSourceType=1)
    assert clash["Success"] is False and code(clash) == "(UPA001)"


def test_estimate_accrual_and_redemption(c):
    kiosk = cc.new_kiosk()
    member = ks(c, "UpdateAccount", kiosk, Email=new_email(), PunchCardOptin=True, TextClubOptin=False)
    cart = cc.cart([cc.rental_item(11, cc.new_barcode()), cc.rental_item(12, cc.new_barcode(), product_type="Blu-ray")])
    acc = ks(c, "EstimateAccrual", kiosk, CustomerProfileNumber=member["CustomerProfileNumber"], ShoppingCart=cart)
    assert acc["Success"] and {i["ProductId"] for i in acc["Items"]} == {11, 12} and all(i["Points"] > 0 for i in acc["Items"])
    red = ks(c, "EstimateRedemption", kiosk, CustomerProfileNumber=member["CustomerProfileNumber"], ShoppingCart=cart)
    points = {i["ProductId"]: i["Points"] for i in red["Items"]}
    assert points[12] > points[11]
    missing = ks(c, "EstimateAccrual", kiosk, CustomerProfileNumber="1", ShoppingCart=cart)
    assert missing["Success"] is False and code(missing) == "(EA001)"
    assert code(ks(c, "EstimateRedemption", kiosk, CustomerProfileNumber="1", ShoppingCart=cart)) == "(ER001)"


def test_customer_offers(c):
    kiosk, email, card = cc.new_kiosk(), new_email(), ks_card()
    ks(c, "EmailConfirm", kiosk, Email=email, CardId=card["CardId"])
    cid = c.sqlval("SELECT customer_id FROM cloud_customers WHERE email = %s", email)
    c.sql("INSERT INTO cloud_customer_offers (customer_id, campaign_id, data, expires_at) VALUES (%s, 77, %s, now() + "
          "interval '3 days')", cid, json.dumps({"DiscountType": 1, "DiscountAmount": 1.5, "ProductType": "Any",
                                                 "OfferScopeType": "StoredRedeemable", "IncludeFormatIds": [2, 3],
                                                 "IncludeTitleIds": [55]}))
    r = ks(c, "GetCustomerOffers", str(kiosk), Lang="en-US", StorefrontEventType="CartView", UtcOffset="-05:00",
           CreditCard=card, ShoppingCart=cc.cart([cc.rental_item(1, cc.new_barcode())]))
    assert r["Success"] and r["EmailAddress"] == email and r["AccountNumber"]
    offer = next(o for o in r["StoredOffers"] if o["CampaignId"] == 77)
    assert offer["DiscountType"] == "AmountOff" and offer["IncludeFormats"] == ["2", "3"]
    assert offer["IncludeTitleIds"] == [55] and offer["StoredOfferId"] == offer["OfferId"]
    # "/Date(ms)/" in the dict; the transports write it as the escaped .NET literal (test_remoting.py)
    assert offer["ExpirationDate"].startswith("/Date(") and isinstance(offer["DiscountAmount"], float)


# ------------------------------------------------------------------ digital services (2014-2016)

def test_rbi_free_trial_get_customer(c):
    kiosk, email, card = cc.new_kiosk(), new_email(), ks_card()
    before = ks(c, "GetCustomer", kiosk, CardId=card["CardId"])
    assert before["IsEligibleFreeTrial"] is True and before["CustomerProfile"]["HasCustomerProfile"] is False
    r = ks(c, "RBiFreeTrialRequest", kiosk, UseMessageControl=True, RequestStatus="Accepted", CreditCard=card,
           Email=email, FreeTrialPackageId=1)
    assert r["Success"] and r["IsEligibleFreeTrial"] is False and r["CustomerProfile"]["CanCreateProfile"] is True
    after = ks(c, "GetCustomer", kiosk, CardId=card["CardId"])
    assert after["IsEligibleFreeTrial"] is False and after["Email"] == email
    assert after["CustomerProfile"]["HasCustomerProfile"] is True and after["CustomerProfile"]["CPFirstName"] == "Test"
    again = ks(c, "RBiFreeTrialRequest", kiosk, RequestStatus="Accepted", CreditCard=card, Email=email,
               FreeTrialPackageId=1)
    assert again["Success"] is False


def test_customer_credits_and_authorize_with_credits(c):
    kiosk, email, card = cc.new_kiosk(), new_email(), ks_card()
    ks(c, "EmailConfirm", kiosk, Email=email, CardId=card["CardId"])
    c.sql("UPDATE cloud_customers SET first_name = 'Pat', info = info || '{\"credits\": 2}' WHERE email = %s", email)
    b1, b2, b3 = cc.new_barcode(), cc.new_barcode(), cc.new_barcode()
    contents = contents_cart([{"barcode": b1, "price": 1.5}, {"barcode": b2, "price": 2.0}, {"barcode": b3, "price": 2.0}])
    r = ks(c, "GetCustomerCredits", kiosk, UseMessageControl=False, DiscountCode=None, DiscountValue=0,
           CreditCard=card, Contents=contents)
    assert r["Success"] and r["CustomerProfile"] == {"Valid": True, "FirstName": "Pat", "IsRBI": False}
    assert r["CreditSummary"] == {"TotalCreditsApplied": 2, "TotalCreditsAvailable": 2}
    assert r["CreditAllocations"][0] == {"Barcode": b1, "Amount": 1.5, "NumberApplied": 1}
    assert isinstance(r["CreditAllocations"][1]["Amount"], float)  # decimal on the client
    auth = ks(c, "Authorize", kiosk, DiscountCode=None, DiscountValue=0, CreditCard=card, Contents=contents,
              UseCredits="OptedIn")
    assert auth["Success"] and auth["CreditSummary"] == {"TotalCreditsApplied": 2, "TotalCreditsAvailable": 0}
    assert auth["CustomerProfile"]["Valid"] is True and auth["Email"] == email and isinstance(auth["ReferenceNumber"], int)
    assert c.sqlval("SELECT info->>'credits' FROM cloud_customers WHERE email = %s", email) == "0"
    txn = c.sql("SELECT * FROM cloud_transactions WHERE reference_number = %s", auth["ReferenceNumber"])[0]
    assert txn["status"] == "authorized" and float(txn["amount"]) == 5.5


# ------------------------------------------------------------------ merchant services

def test_authorize_reconcile_return_2014_contents(c):
    from redbox_backend.dotnet import redbox_datetime
    kiosk, card, email = cc.new_kiosk(), ks_card(), new_email()
    b_ok, b_stuck = cc.new_barcode(), cc.new_barcode()
    contents = contents_cart([{"barcode": b_ok, "price": 1.99}, {"barcode": b_stuck, "price": 1.99, "vend": 11}])
    auth = ks(c, "Authorize", kiosk, DiscountCode="WELCOME", DiscountValue=1.99, CreditCard=card, Contents=contents,
              UseCredits="NotSpecified")
    assert auth["Success"] and auth["CustomerProfile"]["Valid"] is False
    ref = auth["ReferenceNumber"]
    mid = str(uuid.uuid4())
    rec = ks(c, "Reconcile", kiosk, MessageId=mid, IsOffline=False, Blocklisted=False, Email=email,
             DiscountCode="WELCOME", DiscountValue=1.99, ReferenceNumber=ref, CreditCard=card, Contents=contents)
    assert rec["Success"] and rec["CustomerProfileNumber"]
    assert ks(c, "Reconcile", kiosk, MessageId=mid, ReferenceNumber=ref, CreditCard=card, Contents=contents) == rec
    txn = c.sql("SELECT * FROM cloud_transactions WHERE reference_number = %s", ref)[0]
    assert txn["status"] == "reconciled"
    rentals = {r["barcode"]: r["status"] for r in c.sql("SELECT * FROM cloud_rentals WHERE transaction_id = %s", txn["id"])}
    assert rentals == {b_ok: "out", b_stuck: "not_vended"}
    assert c.sqlval("SELECT count(*) FROM cloud_promo_redemptions WHERE transaction_id = %s", txn["id"]) == 1
    assert ks(c, "RentalCount", kiosk)["Count"] == 1
    # 2014-2016 Return message; then the 2024 wrapper (ReturnMessageWithClientData) of a second return
    ret = ks(c, "Return", kiosk, ReturnDate=redbox_datetime(kind="Local"), ReturnType=6, Barcode=b_ok, Deck=3, Slot=12)
    assert ret["Success"]
    assert c.sqlval("SELECT status FROM cloud_rentals WHERE barcode = %s", b_ok) == "returned"
    inv = c.sql("SELECT * FROM cloud_inventory WHERE kiosk_id = %s AND barcode = %s", kiosk, b_ok)[0]
    assert inv["deck"] == 3 and inv["slot"] == 12 and inv["status"] == "In"
    wrapped = {"ClientData": {"Barcode": b_stuck, "FileName": "x.jpg"},
               "ReturnMessage": {"MessageType": "Return", "MessageId": str(uuid.uuid4()), "KioskId": kiosk,
                                 "ReturnDate": redbox_datetime(kind="Local"), "ReturnType": 6, "Barcode": b_stuck,
                                 "Deck": 4, "Slot": 1, "FailedSecurityRead": True}}
    body = "payload=" + urllib.parse.quote_plus(json.dumps(wrapped))
    r = c.run(c.http.post(PATH, content=body.encode(), headers={"content-type": "application/x-www-form-urlencoded"}))
    assert json.loads(urllib.parse.unquote_plus(r.text))["Success"] is True
    assert c.sqlval("SELECT failed_security_read FROM cloud_returns WHERE barcode = %s", b_stuck) is True


def test_authorize_2024_cart_decline_and_gift_card(c):
    kiosk = cc.new_kiosk()
    cart = cc.cart([cc.rental_item(21, cc.new_barcode())])
    ok = ks(c, "Authorize", kiosk, CreditCard=ks_card(), UseCredits="NotSpecified", AuthAcceptFlag=False, AuthRuleId=0,
            AuthAmount=0, SkipAuthRule=True, PropertyBag={}, ShoppingCart=cart, CustomerProfileNumber=None)
    assert ok["Success"] and ok["ConfirmationStatus"] == 0 and ok["AccountNumber"]
    blocked_card = ks_card()
    ks(c, "Authorize", kiosk, CreditCard=blocked_card, ShoppingCart=cart)
    from redbox_backend.cloudapi.customers import card_key
    c.sql("UPDATE cloud_cards SET blocked = true WHERE card_key = %s", card_key(blocked_card))
    no = ks(c, "Authorize", kiosk, CreditCard=blocked_card, ShoppingCart=cart)
    assert no["Success"] is False and code(no) == "(A002)"
    pre = ks(c, "PreAuth", kiosk, CreditCard=blocked_card, ShoppingCart=cart, UseCredits="NotSpecified")
    assert pre["Success"] is False and code(pre) == "(A002)"
    pre = ks(c, "PreAuth", kiosk, CreditCard=ks_card(), ShoppingCart=cart, UseCredits="NotSpecified")
    assert pre["Success"] and pre["SkipAuthRule"] is True and pre["PromptRequired"] is False and pre["AuthAmount"] == 1.85
    gift = ks(c, "GCAuthorize", kiosk, CreditCard=ks_card(last_four="7777"), ShoppingCart=cart, UseCredits="NotSpecified")
    g = gift["GiftCardSummary"]
    assert gift["Success"] and g["Exists"] is True and g["Status"] == 2 and isinstance(g["Balance"], float)


def test_reservation_validate_authorize_modify_pickup(c):
    kiosk, card = cc.new_kiosk(), ks_card()
    ref = int(c.sqlval("SELECT nextval('cloud_reference_seq')"))
    gone = int(c.sqlval("SELECT nextval('cloud_reference_seq')"))
    c.sql("INSERT INTO cloud_reservations (reference_number, kiosk_id) VALUES (%s, %s)", ref, kiosk)
    c.sql("INSERT INTO cloud_reservations (reference_number, kiosk_id, status) VALUES (%s, %s, 'cancelled')", gone, kiosk)
    v = ks(c, "PickupValidation", kiosk, CreditCard=card, ReferenceNumbers=[ref, gone])
    assert v["Success"] and v["ValidReservations"] == [ref] and v["CancelledReservations"] == [gone]
    assert v["ReferenceNumbers"][0] == ref and v["AllowRop"] is True
    cart = cc.cart([cc.rental_item(31, cc.new_barcode())])
    resv = ks(c, "ResvAuth", kiosk, CreditCard=card, UseCredits="NotSpecified", ReferenceNumber=ref, ShoppingCart=cart)
    assert resv["Success"] and resv["ReferenceNumber"] == ref
    assert code(ks(c, "PickupAuthorize", kiosk, CreditCard=card, ReferenceNumber=gone, CartWasModified=False,
                   ShoppingCart=cart)) == "(A026)"
    pick = ks(c, "PickupAuthorize", kiosk, CreditCard=card, ReferenceNumber=ref, CartWasModified=True, ShoppingCart=cart)
    assert pick["Success"] and pick["ReferenceNumber"] == ref
    mod = ks(c, "ModResvPickup", kiosk, Email=None, ReferenceNumber=ref, Items={}, ModifiedCart=True,
             ShoppingCart=cart, CreditCard=card)
    assert mod["Success"]
    items = c.sqlval("SELECT items FROM cloud_reservations WHERE reference_number = %s", ref)
    assert items and items[0]["ProductId"] == 31
    rec = ks(c, "PickupReconcile", kiosk, IsOffline=False, Blocklisted=False, ReferenceNumber=ref, CartWasModified=True,
             CreditCard=card, ShoppingCart=cart, PlayPassPromptAccepted=False, PlayPassPointsEarned=0, PropertyBag={})
    assert rec["Success"]
    assert c.sqlval("SELECT status FROM cloud_reservations WHERE reference_number = %s", ref) == "picked_up"


# ------------------------------------------------------------------ inventory services

def test_soft_sync_disc_fraud_disc_action(c):
    from redbox_backend.dotnet import redbox_datetime
    kiosk, b1, b2 = cc.new_kiosk(), cc.new_barcode(), cc.new_barcode()
    r = ks(c, "SoftSync", kiosk, Discs=[{"Barcode": b1, "Deck": 1, "Slot": 2, "Status": 0},
                                        {"Barcode": b2, "Deck": None, "Slot": None, "Status": 1}],
           RemovedQueueItems=[])
    assert r["Success"]
    inv = {x["barcode"]: x for x in c.sql("SELECT * FROM cloud_inventory WHERE kiosk_id = %s", kiosk)}
    assert inv[b1]["status"] == "In" and inv[b1]["slot"] == 2 and inv[b2]["status"] == "Removed"
    assert ks(c, "DiscFraud", kiosk, Barcode=b1, IsFraud=True, TransactionDate=redbox_datetime(kind="Local"))["Success"]
    act = ks(c, "DiscAction", kiosk, UserName="tech", JobName="thin", ActionDate=redbox_datetime(kind="Local"),
             Actions=[{"Barcode": b2, "Action": "Remove"}], UserMessageControl=False)
    assert act["Success"]
    kinds = {r["kind"] for r in c.sql("SELECT kind FROM events WHERE kiosk_id = %s AND source = 'inventory'", kiosk)}
    assert {"sync", "discfraud", "discaction"} <= kinds


def test_unplayable_return_gives_credit(c):
    kiosk, email, card = cc.new_kiosk(), new_email(), ks_card()
    ks(c, "EmailConfirm", kiosk, Email=email, CardId=card["CardId"])
    barcode = cc.new_barcode()
    contents = contents_cart([{"barcode": barcode, "price": 1.99}])
    auth = ks(c, "Authorize", kiosk, CreditCard=card, Contents=contents)
    ks(c, "Reconcile", kiosk, ReferenceNumber=auth["ReferenceNumber"], CreditCard=card, Contents=contents, Email=email)
    r = ks(c, "UnplayableReturn", kiosk, Barcode=barcode, Email=email, ReturnTime="10/27/2026 9:00:00 PM")
    assert r["Success"] and r["IsUnplayable"] is True  # read by InventoryServicesProxy.MarkAsUnplayable (2016)
    assert c.sqlval("SELECT return_type FROM cloud_returns WHERE barcode = %s", barcode) == 9
    assert c.sqlval("SELECT info->>'credits' FROM cloud_customers WHERE email = %s", email) == "1"
    assert ks(c, "UnplayableReturn", kiosk, Barcode="", Email=email)["Success"] is False


# ------------------------------------------------------------------ health services (2014-2016)

def test_ping_alert_audit(c):
    from redbox_backend.dotnet import parse_redbox_datetime, redbox_datetime
    from redbox_backend.cloudapi.common import now
    kiosk = cc.new_kiosk()
    r = ks(c, "KioskPing", kiosk, Versions={"Kiosk Engine": "1.81.0.27", "Hal Service": "Not Found"},
           Statistics={"Uptime": "42"})
    st = r["ServerTime"]
    assert set(st) == {"theDate", "dateTimeKind", "dateString"} and st["dateTimeKind"] == "Utc"
    assert abs((parse_redbox_datetime(st) - now()).total_seconds()) < 60
    info = c.sqlval("SELECT info FROM kiosks WHERE kiosk_id = %s", kiosk)
    assert info["versions"]["Kiosk Engine"] == "1.81.0.27" and info["statistics"]["Uptime"] == "42"
    assert info["engine_version"] == "1.81.0.27"
    alert = ks(c, "KioskAlert", kiosk, SubType="RemovedQueueDeleteFail", Detail="disk", Type="TitleEmpty",
               Time=redbox_datetime(kind="Local"))
    assert alert["Success"]
    audit = ks(c, "AuditEvent", kiosk, EventType="Login", LogDate=redbox_datetime(kind="Local"), UserName="tech",
               Message="service menu", Source="FieldMaintenance")
    assert audit["Success"]
    rows = {r["kind"]: r["payload"] for r in c.sql("SELECT kind, payload FROM events WHERE kiosk_id = %s AND source IN "
                                                   "('kioskdata', 'health')", kiosk)}
    assert rows["kioskalert"]["AlertType"] == 8 and rows["kioskalert"]["SubType"] == "RemovedQueueDeleteFail"
    assert rows["auditevent"]["UserName"] == "tech" and "ping" in rows
