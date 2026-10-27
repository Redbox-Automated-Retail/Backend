"""Proxy Service protocol tests (KCS ProxyApi + Update Client ping failure)."""
import io
import os
import random
import sys
import uuid
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "kcs"))
import cloudclient as cc  # noqa: E402

pytestmark = pytest.mark.skipif(not cc.database_available(), reason="DATABASE_URL not reachable")


@pytest.fixture(scope="module")
def c():
    return cc.client()


def test_pricing(c):
    kiosk = cc.new_kiosk()
    d = c.get(f"/api/pricing/kiosk/{kiosk}/rental/default", kiosk=kiosk).json()
    names = {f["Name"] for f in d["FormatPricing"]}
    assert {2, 3, 4, 6} <= names
    dvd = next(f for f in d["FormatPricing"] if f["Name"] == 2)["Prices"][0]
    assert dvd["InitialNight"] > 0 and dvd["InitialDays"] >= 1 and dvd["NonReturnDays"] > 0 and dvd["NonReturnPrice"] > 0
    r = c.get(f"/api/pricing/kiosk/{kiosk}/rentalbyprice", kiosk=kiosk).json()
    assert isinstance(r["RentalPricing"], list) and r["RentalPricing"][0]["PriceLevel"]
    p = c.get(f"/api/pricing/kiosk/{kiosk}/purchasebyprice", kiosk=kiosk).json()
    assert isinstance(p["PurchasePricing"], list)


def test_subscriptions(c):
    plans = c.get("/api/subscription/subscriptionplans").json()
    assert plans["Success"] is True
    ids = {p["Id"] for p in plans["SubscriptionPlans"]}
    assert {"RBPLUS_OFFER_1", "RBPLUS_OFFER_2"} <= ids
    item = plans["SubscriptionPlans"][0]["PlanItems"][0]
    assert item["BillingPeriod"] == "Monthly" and item["PeriodUnit"] == "Month"
    camp = c.get("/api/subscription/redboxpluspromocampaigns").json()
    assert {x["Tier"] for x in camp["RedboxPlusPromoCampaigns"]} == {1, 2}


def promo(c, kiosk, code):
    return c.post("/api/promotion/promocodevalidation", {"MessageId": str(uuid.uuid4()), "KioskId": kiosk, "Code": code},
                  kiosk=kiosk)


def test_promo_codes(c):
    kiosk = cc.new_kiosk()
    ok = promo(c, kiosk, "welcome")
    assert ok.status_code == 200 and ok.json()["PromotionIntentCode"] == "FirstNightDVD" and ok.json()["Error"] is None
    assert promo(c, kiosk, "NOPE").json()["Error"] == "(D001)"
    assert promo(c, kiosk, "NOPE").status_code == 422
    assert promo(c, kiosk, "EXPIRED").json()["Error"] == "(D001)"
    code = f"K{kiosk}"
    c.sql("INSERT INTO cloud_promo_codes (code, kiosk_ids, max_uses, data) VALUES (%s, %s, 1, '{\"Amount\":2}')",
          code, [kiosk])
    assert promo(c, kiosk, code).status_code == 200
    assert promo(c, kiosk + 1, code).json()["Error"] == "(D002)"
    c.sql("UPDATE cloud_promo_codes SET uses = 1 WHERE code = %s", code)
    assert promo(c, kiosk, code).json()["Error"] == "(D003)"


def test_campaigns_208(c):
    kiosk = cc.new_kiosk()
    r = c.post(f"/api/cms/campaign/kiosk/{kiosk}", {"LastSyncTime": None}, kiosk=kiosk)
    assert r.status_code == 200
    body = r.json()
    assert body["StartScreens"] and body["InCarts"] is not None
    ctrl = body["StartScreens"][0]["Controls"][0]
    assert ctrl["ControlType"] == "StartAsset" and ctrl["Asset"]["Url"].startswith("http://cloud.test/")
    img = c.get(ctrl["Asset"]["Url"].replace("http://cloud.test", ""))
    assert img.status_code == 200 and img.content[:8] == b"\x89PNG\r\n\x1a\n"
    again = c.post(f"/api/cms/campaign/kiosk/{kiosk}", {"LastSyncTime": body["LastSyncTime"]}, kiosk=kiosk)
    assert again.status_code == 208
    # Any change to the campaigns table invalidates the kiosk's sync time.
    c.sql("UPDATE cloud_campaigns SET rank = rank WHERE campaign_id = 1")
    import time
    time.sleep(1.1)
    c.sql("UPDATE cloud_campaigns SET rank = rank WHERE campaign_id = 1")
    assert c.post(f"/api/cms/campaign/kiosk/{kiosk}", {"LastSyncTime": body["LastSyncTime"]}, kiosk=kiosk).status_code == 200


def test_planograms_208_and_file(c):
    kiosk = cc.new_kiosk()
    r = c.get("/api/planogram/allcurrent?lastReportedTime=null", kiosk=kiosk)
    assert r.status_code == 200
    plans = r.json()["Planograms"]
    assert plans and plans[0]["Filename"].endswith(".xps")
    latest = max(p["LastUpdateUtc"] for p in plans)
    last = latest[:19]  # KCS sends yyyy-MM-ddTHH:mm:ss
    assert c.get(f"/api/planogram/allcurrent?lastReportedTime={last}", kiosk=kiosk).status_code == 208
    f = c.get(plans[0]["PresignedUrl"].replace("http://cloud.test", ""))
    assert f.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(f.content)).namelist()
    assert "[Content_Types].xml" in names and "FixedDocSeq.fdseq" in names


def test_account_login_loyalty_marketing(c):
    kiosk = cc.new_kiosk()
    email, phone = f"login-{kiosk}@example.test", f"(555) {kiosk % 1000:03d}-{kiosk % 10000:04d}"
    acct = c.post("/api/loyalty/updateaccount", {"MessageId": str(uuid.uuid4()), "KioskId": kiosk, "Email": email,
                                                 "MobilePhoneNumber": phone, "Pin": "4321", "TextClubOptin": True,
                                                 "PunchcardOptin": True, "AccountSourceType": 1}, kiosk=kiosk)
    assert acct.status_code == 200
    a = acct.json()
    assert a["CustomerProfileNumber"] and a["TempPassword"] and a["Error"] is None

    def login(**kw):
        body = {"MessageId": str(uuid.uuid4()), "KioskId": kiosk, "IsEncrypted": False, "LanguageCode": "en"}
        body.update(kw)
        return c.post("/api/customerprofile/login", body, kiosk=kiosk).json()

    ok = login(EmailAddress=email, Password=a["TempPassword"])
    assert ok["Success"] is True and ok["Response"]["Customer"]["CPN"] == a["CustomerProfileNumber"]
    assert ok["Response"]["Loyalty"]["IsEnrolled"] is True
    assert "RBPLUS_OFFER_1" in ok["Response"]["Subscriptions"]["EligibleSubscriptionPlanIds"]
    assert login(EmailAddress=email, Password="wrong")["Errors"][0]["Code"] == "(KLI002)"
    assert login(PhoneNumber=phone, Pin="4321")["Success"] is True
    assert login(EmailAddress="nobody-%s@example.test" % kiosk, Password="x")["Errors"][0]["Code"] == "(KLI003)"
    assert login(CustomerProfileNumber=a["CustomerProfileNumber"])["Success"] is True

    items = [cc.rental_item(1, cc.new_barcode()), cc.rental_item(2, cc.new_barcode(), product_type="Blu-ray")]
    payload = {"MessageId": str(uuid.uuid4()), "KioskId": kiosk, "CustomerProfileNumber": a["CustomerProfileNumber"],
               "RentDate": "2026-10-27T10:00:00", "ShoppingCart": cc.cart(items)}
    acc = c.post("/api/loyalty/estimateaccrual", payload, kiosk=kiosk).json()
    assert acc["Items"] == [{"ProductId": 1, "Points": 100}, {"ProductId": 2, "Points": 100}] and acc["Error"] is None
    red = c.post("/api/loyalty/estimateredemption", payload, kiosk=kiosk).json()
    assert [i["Points"] for i in red["Items"]] == [1500, 1800]
    missing = c.post("/api/loyalty/estimateaccrual", dict(payload, CustomerProfileNumber="1"), kiosk=kiosk)
    assert missing.status_code == 404 and missing.json()["Error"] == "Account not found"
    dup = c.post("/api/loyalty/updateaccount", {"MessageId": str(uuid.uuid4()), "KioskId": kiosk,
                                                "Email": f"other-{kiosk}@example.test", "MobilePhoneNumber": phone},
                 kiosk=kiosk)
    assert dup.status_code == 409

    conf = c.post("/api/marketing/emailconfirmation", {"MessageId": str(uuid.uuid4()), "KioskId": kiosk, "Email": email,
                                                       "AccountNumber": "x", "TransactionDate": "2026-10-27T10:00:00"},
                  kiosk=kiosk).json()
    assert conf["Success"] is True and conf["ConfirmationStatus"] == 1
    new = c.post("/api/marketing/emailconfirmation", {"MessageId": str(uuid.uuid4()), "KioskId": kiosk,
                                                      "Email": f"new-{kiosk}@example.test"}, kiosk=kiosk).json()
    assert new["ConfirmationStatus"] == 5 and new["PromptForPerks"] is True
    opt = c.post("/api/marketing/kioskoptin", {"MessageId": str(uuid.uuid4()), "KioskId": kiosk,
                                               "RequestDate": "2026-10-27T10:00:00",
                                               "OptInItems": [{"ItemId": 1, "Item_Type": "Email", "OptInMethodType": "Email",
                                                               "OptInMethodData": email, "CampaignName": "News"}]},
                  kiosk=kiosk).json()
    assert opt["Success"] is True
    assert c.sqlval("SELECT jsonb_array_length(opt_ins) FROM cloud_customers WHERE lower(email) = %s", email) == 1


def test_products_nearby_authenticate_installer_pingfailure(c):
    kiosk = cc.new_kiosk()
    c.sql("INSERT INTO cloud_products (title_id, product_group_id, product_ids, product_page) VALUES "
          "(%s, %s, '{\"DVD\": 1, \"Bluray\": 2}', 'https://example.test/p') ON CONFLICT DO NOTHING", kiosk, kiosk + 1)
    p = c.post("/api/product/bytitleids", {"TitleIds": [kiosk, 1]}, kiosk=kiosk).json()
    assert p["Products"] == [{"TitleId": kiosk, "ProductGroup": {"ProductGroupId": kiosk + 1,
                                                                 "ProductIds": {"DVD": 1, "Bluray": 2}},
                              "ProductPage": "https://example.test/p"}]

    other = kiosk + 1
    lat, lon = random.uniform(-60, 60), random.uniform(-170, 170)  # away from kiosks of earlier runs
    c.sql("INSERT INTO cloud_kiosk_locations (kiosk_id, label, latitude, longitude, dual_kiosk_id, ops_market) VALUES "
          "(%s, 'A', %s, %s, %s, 'charlotte'), (%s, 'B', %s, %s, NULL, NULL) ON CONFLICT (kiosk_id) DO UPDATE SET "
          "latitude = EXCLUDED.latitude, longitude = EXCLUDED.longitude, dual_kiosk_id = EXCLUDED.dual_kiosk_id, "
          "ops_market = EXCLUDED.ops_market", kiosk, lat, lon, other, other, lat + 0.01, lon)
    n = c.get(f"/api/kiosk/{kiosk}/nearby", kiosk=kiosk).json()
    assert n["Kiosks"][0]["KioskId"] == other and n["Kiosks"][0]["IsDual"] is True
    assert 0.5 < n["Kiosks"][0]["DistanceMiles"] < 1

    assert c.post("/api/kiosk/authenticate", {"Username": "tech", "Password": "pw", "KioskId": kiosk}).json()["Success"]
    assert c.post("/api/kiosk/authenticate", {"Username": "", "Password": "", "KioskId": kiosk}).status_code == 401

    assert c.get(f"/api/installer/getopsmarket?kioskid={kiosk}").json()["OpsMarket"] == "charlotte"
    states = c.get("/api/installer/getpendingstates").json()
    assert states["Success"] is True and isinstance(states["States"], list)

    r = c.post(f"/api/kiosk/{kiosk}/pingfailure", {"KioskId": kiosk, "LastSuccessfulPingUTC": "2026-10-27T10:00:00Z",
                                                   "LastUpdateUTC": "2026-10-27T11:00:00Z"}, kiosk=kiosk)
    assert r.status_code == 200 and r.json()["Success"] is True
    assert c.sqlval("SELECT count(*) FROM events WHERE kiosk_id = %s AND kind = 'pingfailure'", kiosk) == 1
