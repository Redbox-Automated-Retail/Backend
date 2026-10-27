"""Transaction API protocol tests (shapes as KCS TransactionApiClient sends and parses them)."""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "kcs"))
import cloudclient as cc  # noqa: E402

pytestmark = pytest.mark.skipif(not cc.database_available(), reason="DATABASE_URL not reachable")


@pytest.fixture(scope="module")
def c():
    return cc.client()


def authorize(c, kiosk, card, items, path="/api/transaction/authorize", **extra):
    body = {"MessageId": str(uuid.uuid4()), "KioskId": kiosk, "TransactionDate": "2026-10-27T12:00:00-04:00",
            "CreditCard": card, "ShoppingCart": cc.cart(items), "AuthAcceptFlag": True}
    body.update(extra)
    r = c.post(path, body, kiosk=kiosk)
    assert r.status_code == 200
    return r.json()


def reconcile(c, kiosk, card, items, ref, message_id=None, **extra):
    body = {"MessageId": message_id or str(uuid.uuid4()), "KioskId": kiosk, "ReferenceNumber": ref,
            "CreditCard": card, "ShoppingCart": cc.cart(items), "IsOffline": False, "UtcOffset": "-04:00"}
    body.update(extra)
    r = c.post("/api/transaction/reconcile", body, kiosk=kiosk)
    assert r.status_code == 200
    return r.json()


def test_authorize_reconcile_return_promocheck(c):
    kiosk, card = cc.new_kiosk(), cc.card()
    b1, b2 = cc.new_barcode(), cc.new_barcode()
    items = [cc.rental_item(1001, b1), cc.rental_item(1002, b2, product_type="Blu-ray", initial=2.0)]
    auth = authorize(c, kiosk, card, items)
    assert auth["Success"] is True and auth["KioskTransactionSuccess"] is True
    assert auth["Errors"] == [] and auth["ReferenceNumber"] > 0 and auth["AccountNumber"]
    assert auth["HasProfile"] is False and "HostName" in auth

    rec = reconcile(c, kiosk, card, items, auth["ReferenceNumber"], Email="rental-%s@example.test" % kiosk)
    assert rec["Success"] is True and rec["CustomerProfileNumber"]
    assert c.sqlval("SELECT status FROM cloud_transactions WHERE reference_number = %s", auth["ReferenceNumber"]) == "reconciled"
    assert c.sqlval("SELECT status FROM cloud_inventory WHERE kiosk_id = %s AND barcode = %s", kiosk, b1) == "Rented"
    assert c.sqlval("SELECT product_id FROM cloud_barcodes WHERE barcode = %s", b2) == 1002

    # Second authorize with the same card: the card is now linked to the e-mail customer.
    again = authorize(c, kiosk, card, items[:1])
    assert again["HasProfile"] is True and again["CustomerProfileNumber"] == rec["CustomerProfileNumber"]

    pc = c.get(f"/api/transaction/return/{b1}/promocheck", kiosk=kiosk).json()
    assert {d["Barcode"]: d["IsReturned"] for d in pc["Discs"]} == {b1: False, b2: False}
    assert pc["IsLastDisc"] is False and pc["Promotion"]["PromoCode"]

    for barcode, slot in ((b1, 11), (b2, 12)):
        r = c.post("/api/transaction/return", {"MessageId": str(uuid.uuid4()), "KioskId": kiosk, "ReturnType": 6,
                                               "Barcode": barcode, "Deck": 3, "Slot": slot,
                                               "ReturnDate": "2026-10-27T18:22:10", "FailedSecurityRead": False},
                   kiosk=kiosk)
        assert r.status_code == 200 and r.json()["Success"] is True
    row = c.sql("SELECT status, deck, slot FROM cloud_inventory WHERE kiosk_id = %s AND barcode = %s", kiosk, b2)[0]
    assert row == {"status": "In", "deck": 3, "slot": 12}
    pc2 = c.get(f"/api/transaction/return/{b2}/promocheck", kiosk=kiosk).json()
    assert pc2["IsLastDisc"] is True and all(d["IsReturned"] for d in pc2["Discs"])
    assert pc2["Promotion"]["PromoCode"] == pc["Promotion"]["PromoCode"]  # one offer per transaction
    # The issued return-visit code validates once.
    v = c.post("/api/promotion/promocodevalidation", {"MessageId": str(uuid.uuid4()), "KioskId": kiosk,
                                                       "Code": pc2["Promotion"]["PromoCode"]}, kiosk=kiosk)
    assert v.status_code == 200 and v.json()["PromotionIntentCode"] == "FirstNightFreeItem"


def test_unknown_barcode_return_and_promocheck(c):
    kiosk, barcode = cc.new_kiosk(), cc.new_barcode()
    r = c.post("/api/transaction/return", {"MessageId": str(uuid.uuid4()), "KioskId": kiosk, "ReturnType": 7,
                                           "Barcode": barcode, "Deck": 1, "Slot": 2, "ReturnDate": "2026-10-27T10:00:00"},
               kiosk=kiosk)
    assert r.status_code == 200 and r.json()["Success"] is True
    assert c.sqlval("SELECT status FROM cloud_inventory WHERE kiosk_id = %s AND barcode = %s", kiosk, barcode) == "In"
    pc = c.get(f"/api/transaction/return/{barcode}/promocheck", kiosk=kiosk)
    assert pc.status_code == 404 and pc.json()["Error"]


def test_reconcile_replay_is_idempotent_and_accrues_points_once(c):
    kiosk = cc.new_kiosk()
    email = f"perks-{kiosk}@example.test"
    acct = c.post("/api/loyalty/updateaccount", {"MessageId": str(uuid.uuid4()), "KioskId": kiosk, "Email": email,
                                                 "MobilePhoneNumber": f"555{kiosk:07d}", "Pin": "1234",
                                                 "PunchcardOptin": True, "AccountSourceType": 1}, kiosk=kiosk).json()
    cpn = acct["CustomerProfileNumber"]
    card, items = cc.card(), [cc.rental_item(2001, cc.new_barcode())]
    auth = authorize(c, kiosk, card, items, CustomerProfileNumber=cpn)
    assert auth["CustomerProfileNumber"] == cpn and auth["Email"] == email
    mid = str(uuid.uuid4())
    r1 = reconcile(c, kiosk, card, items, auth["ReferenceNumber"], message_id=mid, CustomerProfileNumber=cpn)
    r2 = reconcile(c, kiosk, card, items, auth["ReferenceNumber"], message_id=mid, CustomerProfileNumber=cpn)
    assert r1 == r2
    assert c.sqlval("SELECT points FROM cloud_customers WHERE cpn = %s", cpn) == 100
    assert c.sqlval("SELECT count(*) FROM cloud_rentals r JOIN cloud_transactions t ON t.id = r.transaction_id "
                    "WHERE t.reference_number = %s", auth["ReferenceNumber"]) == 1


def test_not_vended_items_stay_in_stock(c):
    kiosk, barcode = cc.new_kiosk(), cc.new_barcode()
    c.post("/api/kiosk/sync", {"MessageId": str(uuid.uuid4()), "KioskId": kiosk,
                               "Discs": [{"Barcode": barcode, "Deck": 1, "Slot": 1, "Status": "In"}]},
           kiosk=kiosk, gzip_body=True)
    card, items = cc.card(), [cc.rental_item(3001, barcode, vend_status=11)]
    auth = authorize(c, kiosk, card, items)
    reconcile(c, kiosk, card, items, auth["ReferenceNumber"])
    assert c.sqlval("SELECT status FROM cloud_inventory WHERE kiosk_id = %s AND barcode = %s", kiosk, barcode) == "In"
    assert c.sqlval("SELECT status FROM cloud_rentals WHERE barcode = %s", barcode) == "not_vended"


def test_payment_mode_rules_and_decline(c, monkeypatch):
    from redbox_backend import config
    kiosk, items = cc.new_kiosk(), [cc.rental_item(4001, cc.new_barcode())]
    monkeypatch.setattr(config, "PAYMENT_MODE", "rules")
    assert authorize(c, kiosk, cc.card("4242"), items)["Success"] is True
    declined = authorize(c, kiosk, cc.card("0002"), items)
    assert declined["Success"] is False and declined["Errors"][0]["Code"] == "(A006)"
    assert authorize(c, kiosk, cc.card("0005"), items)["Errors"][0]["Code"] == "(A005)"
    expired = authorize(c, kiosk, cc.card("4242", ExpirationYear="20"), items)
    assert expired["Errors"][0]["Code"] == "(A004)"
    monkeypatch.setattr(config, "PAYMENT_MODE", "decline")
    assert authorize(c, kiosk, cc.card("4242"), items)["Errors"][0]["Code"] == "(A006)"
    ref = declined["ReferenceNumber"]
    assert c.sqlval("SELECT status FROM cloud_transactions WHERE reference_number = %s", ref) == "declined"


def test_gift_card_balance(c):
    kiosk = cc.new_kiosk()
    gc = cc.card("9999", CardType=5)
    items = [cc.rental_item(5001, cc.new_barcode())]
    auth = authorize(c, kiosk, gc, items, path="/api/transaction/giftcardauthorize")
    assert auth["Success"] is True and auth["GiftCardSummary"]["Exists"] is True
    assert auth["GiftCardSummary"]["Balance"] == 25.0
    reconcile(c, kiosk, gc, items, auth["ReferenceNumber"])
    big = [cc.rental_item(5002, cc.new_barcode(), initial=30.0)]
    auth2 = authorize(c, kiosk, gc, big, path="/api/transaction/giftcardauthorize")
    assert auth2["GiftCardSummary"]["Balance"] == 23.15
    assert auth2["Success"] is False and auth2["Errors"][0]["Code"] == "(A006)"


def test_reservations(c):
    kiosk, card = cc.new_kiosk(), cc.card()
    r1, r2 = 880000000 + kiosk, 890000000 + kiosk
    v = c.post("/api/reservation/validatepickup", {"MessageId": str(uuid.uuid4()), "KioskId": kiosk,
                                                   "CreditCard": card, "ReferenceNumbers": [r1, r2]}, kiosk=kiosk).json()
    assert v["Success"] is True and sorted(v["ValidReservations"]) == [r1, r2] and v["CancelledReservations"] == []

    cancel = c.post("/api/reservation/cancel", {"MessageId": str(uuid.uuid4()), "KioskId": kiosk,
                                                "ReferenceNumber": r2, "ReasonCode": "Customer"}, kiosk=kiosk)
    assert cancel.status_code == 200 and cancel.json()["Success"] is True
    v2 = c.post("/api/reservation/validatepickup", {"MessageId": str(uuid.uuid4()), "KioskId": kiosk,
                                                    "CreditCard": card, "ReferenceNumbers": [r1, r2]}, kiosk=kiosk).json()
    assert v2["ValidReservations"] == [r1] and v2["CancelledReservations"] == [r2]

    items = [cc.rental_item(6001, cc.new_barcode())]
    bad = authorize(c, kiosk, card, items, path="/api/transaction/pickupauthorize", ReferenceNumber=r2, CartWasModified=False)
    assert bad["Success"] is False and bad["Errors"][0]["Code"] == "(A026)"
    ok = authorize(c, kiosk, card, items, path="/api/transaction/pickupauthorize", ReferenceNumber=r1, CartWasModified=False)
    assert ok["Success"] is True and ok["ReferenceNumber"] == r1
    pick = c.post("/api/transaction/reservationPickup", {"MessageId": str(uuid.uuid4()), "KioskId": kiosk,
                                                         "ReferenceNumber": r1, "CreditCard": card,
                                                         "ShoppingCart": cc.cart(items), "CartWasModified": False,
                                                         "PickupDateTime": "2026-10-27T12:00:00"}, kiosk=kiosk)
    assert pick.status_code == 200 and pick.json()["Success"] is True
    assert c.sqlval("SELECT status FROM cloud_reservations WHERE reference_number = %s", r1) == "picked_up"

    r3 = 870000000 + kiosk
    exp = c.post("/api/reservation/expire", {"MessageId": str(uuid.uuid4()), "KioskId": kiosk, "ReferenceNumber": r3},
                 kiosk=kiosk)
    assert exp.json()["Success"] is True
    assert c.sqlval("SELECT status FROM cloud_reservations WHERE reference_number = %s", r3) == "expired"
    resv = authorize(c, kiosk, card, items, path="/api/transaction/reservationauthorize", ReferenceNumber=str(r3))
    assert resv["Errors"][0]["Code"] == "(A026)"


def test_offline_reconcile_and_customer_offers(c):
    kiosk, card = cc.new_kiosk(), cc.card()
    items = [cc.rental_item(7001, cc.new_barcode())]
    rec = reconcile(c, kiosk, card, items, None, IsOffline=True, Email=f"offline-{kiosk}@example.test")
    assert rec["Success"] is True
    assert c.sqlval("SELECT kind FROM cloud_transactions t JOIN cloud_rentals r ON r.transaction_id = t.id "
                    "WHERE r.product_id = 7001 ORDER BY t.id DESC LIMIT 1") == "offline"
    cust = c.sqlval("SELECT customer_id FROM cloud_customers WHERE cpn = %s", rec["CustomerProfileNumber"])
    c.sql("INSERT INTO cloud_customer_offers (customer_id, campaign_id, data, expires_at) VALUES "
          "(%s, 77, '{\"PromotionCode\":\"OFFER1\",\"DiscountAmount\":1.5,\"DiscountType\":\"AmountOff\"}', now() + interval '1 day')",
          cust)
    offers = c.post("/api/customeroffers", {"MessageId": str(uuid.uuid4()), "KioskId": kiosk, "CreditCard": card,
                                            "ShoppingCart": cc.cart(items), "UtcOffset": "-04:00", "Lang": "en"},
                    kiosk=kiosk).json()
    assert offers["Success"] is True and offers["CustomerProfileNumber"] == rec["CustomerProfileNumber"]
    assert offers["StoredOffers"][0]["PromotionCode"] == "OFFER1" and offers["StoredOffers"][0]["CampaignId"] == 77
