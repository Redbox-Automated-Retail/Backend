"""Drive the real Kiosk Client Service (http://localhost:9001) against the self-hosted cloud API.

Prerequisites: the cloud API running (run_cloudapi.py), setup_kcs.py install done, KCS started with
`dotnet KioskClientService.API.dll --console` (x86 .NET Core 2.2 runtime).

    python drive_kcs.py [--kcs http://localhost:9001] [--kiosk 99001]

Each step calls a KCS endpoint and checks what KCS made of the cloud answer. Exit status 1 if any check
fails. Afterwards grep the KCS log (%ProgramData%\\Redbox\\KioskClient\\Logs) for ERR/Exception lines.
"""
import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request
import uuid

RESULTS = []


def call(base, method, path, body=None, timeout=90):
    data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
    req = urllib.request.Request(base + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        raw, status = e.read(), e.code
    try:
        js = json.loads(raw) if raw else None
    except ValueError:
        js = raw.decode(errors="replace")
    return status, js


SKIPPED = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (("  -- " + detail) if detail else ""))
    sys.stdout.flush()


def skip(name, why):
    SKIPPED.append(name)
    print("SKIP " + name + "  -- " + why)


def g(obj, *path):
    for p in path:
        if obj is None:
            return None
        if isinstance(obj, list):
            obj = obj[p] if isinstance(p, int) and p < len(obj) else None
            continue
        low = {k.lower(): v for k, v in obj.items()}
        obj = low.get(str(p).lower())
    return obj


def short(js, n=300):
    s = json.dumps(js) if not isinstance(js, str) else js
    return s if len(s) <= n else s[:n] + "..."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--kcs", default="http://localhost:9001")
    ap.add_argument("--kiosk", type=int, default=99001)
    a = ap.parse_args()
    base, kiosk = a.kcs.rstrip("/"), a.kiosk

    def mid():
        return str(uuid.uuid4())

    barcodes = [str(random.randint(100000000, 999999999)) for _ in range(3)]
    card = {"FirstName": "Test", "LastName": "Customer", "BIN": "424242", "LastFour": "4242",
            "ExpirationMonth": "12", "ExpirationYear": "39", "CardType": 1, "IsEncrypted": True,
            "CardId": "kcs-test-" + str(uuid.uuid4()), "CardSourceType": "EMVContact", "EmvEnabledTerminal": True}

    def item(pid, barcode, ptype="DVD", price=1.85):
        return {"ProductId": pid, "Barcode": barcode, "VendStatus": "Vended", "ProductFamily": "Movie",
                "ProductType": ptype, "ProductName": f"Title {pid}",
                "Pricing": {"InitialNight": price, "ExtraNight": price, "NonReturn": 29.6, "Purchase": 0, "Expiration": 0,
                            "NonReturnDays": 16, "PriceSetId": 1, "InitialDays": 1}}

    items = [item(1001, barcodes[0]), item(1002, barcodes[1], "Blu-ray", 2.0)]
    cart = {"Groups": [{"GroupType": "Rental", "Items": items,
                        "Totals": {"TaxRate": 0, "Subtotal": 3.85, "DiscountedSubtotal": 3.85, "TaxAmount": 0,
                                   "GrandTotal": 3.85}}], "Discounts": []}

    # ---- inventory sync (gzip upstream)
    discs = [{"Barcode": b, "Deck": 1, "Slot": i + 1, "Status": "In"} for i, b in enumerate(barcodes)]
    s, js = call(base, "POST", "/api/inventory/sync", {"MessageId": mid(), "KioskId": kiosk, "Discs": discs})
    check("inventory/sync", s == 200 and g(js, "success") is True, short(js))

    # ---- authorize -> reconcile -> return -> promo check
    s, js = call(base, "POST", "/api/transaction/Authorize",
                 {"MessageId": mid(), "KioskId": kiosk, "TransactionDate": "2026-10-27T12:00:00", "CreditCard": card,
                  "ShoppingCart": cart, "AuthAcceptFlag": True, "SkipAuthRule": True})
    ref = g(js, "referenceNumber")
    check("transaction/Authorize", s == 200 and g(js, "success") is True and ref and g(js, "kioskTransactionSuccess"), short(js))
    email = f"kcs-{kiosk}-{random.randint(1000, 9999)}@example.test"
    s, js = call(base, "POST", "/api/transaction/Reconcile",
                 {"MessageId": mid(), "KioskId": kiosk, "ReferenceNumber": ref, "Email": email, "CreditCard": card,
                  "ShoppingCart": cart, "IsOffline": False, "UtcOffset": "-04:00",
                  "TransactionDate": "2026-10-27T12:01:00-04:00"})
    check("transaction/Reconcile", s == 200 and g(js, "success") is True and g(js, "customerProfileNumber"), short(js))
    for i, b in enumerate(barcodes[:2]):
        s, js = call(base, "POST", "/api/transaction/Return",
                     {"MessageId": mid(), "KioskId": kiosk, "ReturnType": 6, "Barcode": b, "Deck": 2, "Slot": 10 + i,
                      "ReturnDate": time.strftime("%Y-%m-%dT%H:%M:%S"), "FailedSecurityRead": False})
        check(f"transaction/Return {i + 1}", s == 200 and g(js, "success") is True, short(js))
    s, js = call(base, "GET", f"/api/transaction/return/{barcodes[1]}/promocheck")
    check("transaction/return/promocheck (last disc)", s == 200 and g(js, "isLastDisc") is True and g(js, "promotion", "promoCode"),
          short(js))

    # ---- gift card, offers, reservations
    s, js = call(base, "POST", "/api/transaction/GiftCardAuthorize",
                 {"MessageId": mid(), "KioskId": kiosk, "CreditCard": dict(card, CardId="gc-" + mid(), CardType=5),
                  "ShoppingCart": cart})
    check("transaction/GiftCardAuthorize", s == 200 and g(js, "giftCardSummary", "balance") == 25.0, short(js))
    s, js = call(base, "POST", "/api/transaction/GetCustomerOffers",
                 {"MessageId": mid(), "KioskId": kiosk, "CreditCard": card, "ShoppingCart": cart, "UtcOffset": "-04:00",
                  "Lang": "en", "StorefrontEventType": "AfterSwipe"})
    check("transaction/GetCustomerOffers", s == 200 and g(js, "success") is True and g(js, "customerProfileNumber"), short(js))
    r1 = random.randint(500000000, 599999999)
    s, js = call(base, "POST", "/api/transaction/ValidatePickup",
                 {"MessageId": mid(), "KioskId": kiosk, "CreditCard": card, "ReferenceNumbers": [r1]})
    check("transaction/ValidatePickup", s == 200 and g(js, "validReservations") == [r1], short(js))
    s, js = call(base, "POST", "/api/transaction/PickupAuthorize",
                 {"MessageId": mid(), "KioskId": kiosk, "CreditCard": card, "ShoppingCart": cart, "ReferenceNumber": r1,
                  "CartWasModified": False})
    check("transaction/PickupAuthorize", s == 200 and g(js, "success") is True and g(js, "referenceNumber") == r1, short(js))
    s, js = call(base, "POST", "/api/transaction/ReservationPickup",
                 {"MessageId": mid(), "KioskId": kiosk, "ReferenceNumber": r1, "CreditCard": card, "ShoppingCart": cart,
                  "CartWasModified": False, "PickupDateTime": time.strftime("%Y-%m-%dT%H:%M:%S")})
    check("transaction/ReservationPickup", s == 200 and g(js, "success") is True, short(js))
    r2 = r1 + 1
    s, js = call(base, "POST", "/api/transaction/CancelReservation",
                 {"MessageId": mid(), "KioskId": kiosk, "ReferenceNumber": r2, "ReasonCode": "Customer"})
    check("transaction/CancelReservation", s == 200 and g(js, "success") is True, short(js))
    s, js = call(base, "POST", "/api/transaction/ExpireReservation", {"MessageId": mid(), "KioskId": kiosk, "ReferenceNumber": r2 + 1})
    check("transaction/ExpireReservation", s == 200 and g(js, "success") is True, short(js))

    # ---- subscriptions, sort order
    s, js = call(base, "POST", "/api/subscription/plans/refresh")
    check("subscription/plans/refresh", s == 200, short(js))
    s, js = call(base, "GET", "/api/subscription/plans")
    check("subscription/plans", s == 200 and len(g(js, "subscriptionPlans") or []) >= 2 and g(js, "redboxPlusPromoCampaigns"),
          short(js, 200))
    s, js = call(base, "POST", "/api/productgroups/sortorder/refresh")
    check("productgroups/sortorder/refresh", s == 200, short(js))

    # ---- campaigns, planograms. KCS reads the kiosk id from HKLM\SOFTWARE\Redbox\REDS\Kiosk Engine\Store\ID
    # (32-bit view under an x86 runtime); pricing and campaign refreshes refuse to run with kiosk id 0.
    s, js = call(base, "POST", "/api/campaign/refresh")
    no_kiosk_id = g(js, "error") == "KioskId is 0"
    if no_kiosk_id:
        why = "KCS has no kiosk id (registry value Store/ID missing in the view KCS reads)"
        for name in ("campaign/refresh", "campaign/startscreen", "campaign/incart", "campaign/refresh again -> 208",
                     "pricing/all/refresh", "pricing/rentaldefault", "pricing/product", "pricing/product (MD)"):
            skip(name, why)
    else:
        check("campaign/refresh", s in (200, 208), short(js))
        time.sleep(3)
        s, js = call(base, "GET", "/api/campaign/startscreen")
        check("campaign/startscreen", s == 200 and g(js, "startScreen", "controls"), short(js))
        s, js = call(base, "GET", "/api/campaign/incart")
        check("campaign/incart", s == 200 and g(js, "inCart", "promoCode") == "SAVE1", short(js))
        s, js = call(base, "POST", "/api/campaign/refresh")
        check("campaign/refresh again -> 208", s == 208, short(js))
        # pricing
        s, js = call(base, "POST", "/api/pricing/all/refresh")
        check("pricing/all/refresh", s == 200, short(js))
        s, js = call(base, "GET", "/api/pricing/rentaldefault")
        check("pricing/rentaldefault", s == 200 and g(js, "formatPricing"), short(js, 200))
        s, js = call(base, "POST", "/api/pricing/product",
                     {"ProductId": 1001, "PriceLevelType": "Dvd", "RentalTaxRate": 7.25, "PurchaseTaxRate": 7.25, "State": "NC"})
        check("pricing/product", s == 200 and g(js, "productPricing", "rentalPriceDefault", "initialNight") == 1.85, short(js))
        s, js = call(base, "POST", "/api/pricing/product",
                     {"ProductId": 1001, "PriceLevelType": "Dvd", "RentalTaxRate": 6.0, "PurchaseTaxRate": 6.0, "State": "MD"})
        check("pricing/product (MD tax-inclusive)", s == 200 and g(js, "productPricing", "rentalPriceDefault", "initialNight") == 1.96,
              short(js))

    s, js = call(base, "POST", "/api/planogram/sync?forceOverwrite=true")
    check("planogram/sync", s == 200, short(js))
    time.sleep(3)
    s, js = call(base, "GET", "/api/planogram")
    check("planogram (list)", s == 200 and isinstance(js, list) and js and g(js, 0, "name") == "Front", short(js))
    s, js = call(base, "POST", "/api/planogram/sync?forceOverwrite=false")
    check("planogram/sync again (208 upstream)", s == 200, short(js))

    # ---- kiosk data
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for path, extra in (("kioskversions", {"Versions": {"KioskEngine": "1.88.0.0"}}),
                        ("kioskstatistics", {"Statistics": {"Rentals": "2"}}),
                        ("kioskproperties", {"Properties": {"Banner": "Test"}}),
                        ("kioskalert", {"UniqueId": mid(), "AlertType": 9, "SubAlertType": "Test", "AlertMessage": "test"}),
                        ("auditevent", {"LogDate": now, "EventType": "Login", "Username": "tech", "Message": "m", "Source": "t"}),
                        ("kioskevents", {"KioskEventsData": json.dumps([{"Event": "Test", "Index": i} for i in range(40)])}),
                        ("kioskfunctioncheck", {"ReportTime": now, "UserName": "tech", "InitTest": "Pass"})):
        body = {"MessageId": extra.get("UniqueId") or mid(), "KioskId": kiosk, "CreatedOn": now, "CreatedOnLocal": now}
        body.update(extra)
        s, js = call(base, "POST", "/api/data/" + path, body)
        check("data/" + path, s == 200 and g(js, "success") is True, short(js, 150))

    # ---- promotions, customer profile, loyalty, marketing
    s, js = call(base, "POST", "/api/promotion/promocodevalidation", {"MessageId": mid(), "KioskId": kiosk, "Code": "WELCOME"})
    check("promotion/promocodevalidation valid", s == 200 and g(js, "promotionIntentCode") == "FirstNightDVD", short(js))
    s, js = call(base, "POST", "/api/promotion/promocodevalidation", {"MessageId": mid(), "KioskId": kiosk, "Code": "BOGUS"})
    check("promotion/promocodevalidation invalid -> 422 (D001)", s == 422 and g(js, "error") == "(D001)", short(js))
    s, js = call(base, "POST", "/api/loyalty/updateaccount",
                 {"MessageId": mid(), "KioskId": kiosk, "Email": email, "MobilePhoneNumber": "", "Pin": "1234",
                  "PunchcardOptin": True, "TextClubOptin": False, "AccountSourceType": "General"})
    temp = g(js, "tempPassword")
    check("loyalty/updateaccount", s == 200 and g(js, "customerProfileNumber") and temp, short(js))
    s, js = call(base, "POST", "/api/customerprofile/login",
                 {"MessageId": mid(), "KioskId": kiosk, "EmailAddress": email, "Password": temp, "IsEncrypted": False})
    cpn = g(js, "response", "customer", "cpn")
    check("customerprofile/login", s == 200 and g(js, "success") is True and cpn and g(js, "response", "loyalty", "isEnrolled"),
          short(js))
    s, js = call(base, "POST", "/api/customerprofile/login",
                 {"MessageId": mid(), "KioskId": kiosk, "EmailAddress": email, "Password": "wrong", "IsEncrypted": False})
    check("customerprofile/login wrong password", g(js, "success") is False and g(js, "errors", 0, "code") == "(KLI002)", short(js))
    s, js = call(base, "POST", "/api/loyalty/estimateaccrual",
                 {"MessageId": mid(), "KioskId": kiosk, "CustomerProfileNumber": cpn, "ShoppingCart": cart})
    check("loyalty/estimateaccrual", s == 200 and g(js, "items", 0, "points") == 100, short(js))
    s, js = call(base, "POST", "/api/loyalty/estimateredemption",
                 {"MessageId": mid(), "KioskId": kiosk, "CustomerProfileNumber": cpn, "ShoppingCart": cart})
    check("loyalty/estimateredemption", s == 200 and g(js, "items", 1, "points") == 1800, short(js))
    s, js = call(base, "POST", "/api/marketing/emailconfirmation",
                 {"MessageId": mid(), "KioskId": kiosk, "Email": email, "AccountNumber": "1", "TransactionDate": now})
    check("marketing/emailconfirmation", s == 200 and g(js, "customerProfileNumber") == cpn, short(js))
    s, js = call(base, "POST", "/api/marketing/kioskoptin",
                 {"MessageId": mid(), "KioskId": kiosk, "RequestDate": now,
                  "OptInItems": [{"ItemId": 1, "Item_Type": 1, "OptInMethodType": 1, "OptInMethodData": email}]})
    check("marketing/kioskoptin", s == 200 and g(js, "success") is True, short(js))

    # ---- inventory extras, products, kiosk
    s, js = call(base, "POST", "/api/inventory/kiosktitlescount",
                 {"MessageId": mid(), "SourceKioskId": kiosk, "KioskIds": [kiosk], "TitleIds": [1001, 1002]})
    check("inventory/kiosktitlescount", s == 200 and g(js, "kioskTitlesCount", 0, "titlesCount"), short(js))
    s, js = call(base, "POST", "/api/inventory/discaction",
                 {"MessageId": mid(), "KioskId": kiosk, "UserName": "tech", "JobName": "test", "ActionDate": now,
                  "Actions": [{"Barcode": barcodes[2], "Action": "Thin"}]})
    check("inventory/discaction", s == 200 and g(js, "success") is True, short(js))
    s, js = call(base, "POST", "/api/inventory/discfraud",
                 {"MessageId": mid(), "KioskId": kiosk, "Barcode": barcodes[2], "TransactionDate": now, "Properties": {"a": "b"}})
    check("inventory/discfraud", s == 200 and g(js, "success") is True, short(js))
    s, js = call(base, "POST", "/api/inventory/UpdateMerchandizingStatus",
                 [{"ThinDateUTC": now, "ThinReasonCode": 1, "MerchandStatus": 2, "Barcode": barcodes[2], "UpdateDateTimeUTC": now}])
    check("inventory/UpdateMerchandizingStatus", s == 200, short(js))
    s, js = call(base, "GET", "/api/inventory/MerchandizingOrders")
    check("inventory/MerchandizingOrders", s in (200, 404), short(js))
    s, js = call(base, "POST", "/api/product/bytitleids", {"TitleIds": [1001]})
    check("product/bytitleids", s in (200, 404), short(js))
    s, js = call(base, "POST", "/api/kiosk/nearby", {"MessageId": mid(), "KioskId": kiosk})
    check("kiosk/nearby", s == 200, short(js))
    s, js = call(base, "POST", "/api/kiosk/authenticate", {"Username": "tech", "Password": "pw", "KioskId": kiosk})
    check("kiosk/authenticate", s == 200 and g(js, "success") is True, short(js))
    s, js = call(base, "POST", "/api/productgroups/morelikethis/refresh", [1001, 1002])
    check("productgroups/morelikethis/refresh", s == 200, short(js))

    # ---- ads
    s, js = call(base, "POST", "/api/application/start")
    check("application/start (ad cache)", s == 200, short(js))
    time.sleep(5)
    s, js = call(base, "GET", "/api/ads/cache")
    check("ads/cache", s == 200 and (g(js, "assetCacheCount") or 0) > 0, short(js))
    s, js = call(base, "GET", "/api/ads?location=Welcome")
    ad_id = g(js, "advertisementId")
    check("ads?location=Welcome", s == 200 and ad_id and g(js, "assetPath"), short(js))
    if ad_id:
        s, js = call(base, "POST", f"/api/ads/{ad_id}/proofofplay", {"DisplayedTime": now, "DisplayedMilliseconds": 5000})
        check("ads/{id}/proofofplay", s == 200 and g(js, "success") is True, short(js))
    s, js = call(base, "POST", "/api/application/end")
    check("application/end (expire ads)", s == 200, short(js))

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("failed: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
