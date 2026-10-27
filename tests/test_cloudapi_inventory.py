"""Inventory API protocol tests (KCS InventoryApiClient) and the Proxy titles count built on it."""
import datetime as dt
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


def sync(c, kiosk, discs):
    mid = str(uuid.uuid4())
    r = c.post("/api/kiosk/sync", {"MessageId": mid, "KioskId": kiosk, "Discs": discs}, kiosk=kiosk, gzip_body=True)
    assert r.status_code == 200
    body = r.json()
    assert body["Success"] is True and body["MessageId"] == mid and body["Errors"] == []
    return body


def test_sync_replaces_snapshot(c):
    kiosk = cc.new_kiosk()
    b = [cc.new_barcode() for _ in range(3)]
    sync(c, kiosk, [{"Barcode": b[0], "Deck": 1, "Slot": 1, "Status": "In"},
                    {"Barcode": b[1], "Deck": 1, "Slot": 2, "Status": "In"},
                    {"Barcode": b[2], "Status": "Removed"}])
    assert c.sqlval("SELECT count(*) FROM cloud_inventory WHERE kiosk_id = %s", kiosk) == 3
    sync(c, kiosk, [{"Barcode": b[0], "Deck": 2, "Slot": 5, "Status": "In"}])
    rows = c.sql("SELECT barcode, deck, slot FROM cloud_inventory WHERE kiosk_id = %s", kiosk)
    assert rows == [{"barcode": b[0], "deck": 2, "slot": 5}]


def test_sync_plain_body_and_missing_kiosk(c):
    r = c.request("POST", "/api/kiosk/sync", json_body={"MessageId": str(uuid.uuid4()), "Discs": []})
    assert r.status_code == 400 and r.json()["Success"] is False


def test_titles_count_from_snapshot_and_learned_barcodes(c):
    kiosk, other = cc.new_kiosk(), cc.new_kiosk()
    b = [cc.new_barcode() for _ in range(3)]
    for barcode, title in zip(b, (11, 11, 12)):
        c.sql("INSERT INTO cloud_barcodes (barcode, product_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", barcode, title)
    sync(c, other, [{"Barcode": b[0], "Deck": 1, "Slot": 1, "Status": "In"},
                    {"Barcode": b[1], "Deck": 1, "Slot": 2, "Status": "In"},
                    {"Barcode": b[2], "Deck": 1, "Slot": 3, "Status": "Removed"}])
    r = c.post("/api/inventory/kiosktitlescount", {"MessageId": str(uuid.uuid4()), "SourceKioskId": kiosk,
                                                   "KioskIds": [other], "TitleIds": [11, 12, 13]}, kiosk=kiosk)
    assert r.status_code == 200
    counts = r.json()["KioskTitlesCount"][0]
    assert counts["KioskId"] == other and counts["TitlesCount"] == {"11": 2, "12": 0, "13": 0}


def test_disc_action_fraud_and_merch_status(c):
    kiosk, barcode = cc.new_kiosk(), cc.new_barcode()
    r = c.post("/api/disc/discaction", {"MessageId": str(uuid.uuid4()), "KioskId": kiosk, "UserName": "tech",
                                        "JobName": "thin", "ActionDate": "2026-10-27T10:00:00",
                                        "Actions": [{"Barcode": barcode, "Action": "Thin"}]}, kiosk=kiosk)
    assert r.status_code == 200 and r.json()["Success"] is True
    r = c.post("/api/disc/discfraud", {"MessageId": str(uuid.uuid4()), "KioskId": kiosk, "Barcode": barcode,
                                       "TransactionDate": "2026-10-27T10:00:00", "Properties": {"Reason": "Paper"}},
               kiosk=kiosk)
    assert r.json()["Success"] is True
    assert c.sqlval("SELECT count(*) FROM events WHERE kiosk_id = %s AND kind IN ('discaction', 'discfraud')", kiosk) == 2
    r = c.post("/api/disc/updatemerchandizingstatus",
               [{"MessageId": str(uuid.uuid4()), "ThinDateUTC": "2026-10-27T10:00:00Z", "ThinReasonCode": 3,
                 "MerchandStatus": 2, "Barcode": barcode, "UpdateDateTimeUTC": "2026-10-27T10:00:00Z"}], kiosk=kiosk)
    assert r.status_code == 200 and r.json()["Data"] == []
    assert c.sqlval("SELECT merch_status FROM cloud_inventory WHERE kiosk_id = %s AND barcode = %s", kiosk, barcode) == 2


def test_merchandizing_orders_and_sort_order(c):
    kiosk = cc.new_kiosk()
    today = dt.datetime.now(dt.timezone.utc).date()
    c.sql("INSERT INTO cloud_merch_orders (kiosk_id, order_date, order_type, title_id, unit_count) VALUES "
          "(%s, %s, 3, 317846, 1), (%s, %s, 2, 317946, 5)", kiosk, today, kiosk, today)
    r = c.get(f"/api/merchandizingorder/{kiosk}/{today:%Y-%m-%d}", kiosk=kiosk)
    assert r.status_code == 200
    assert {o["MerchandizingOrderType"] for o in r.json()} == {"ThinningExtract", "Fill"}
    r = c.get(f"/api/merchandizingorder/{kiosk}/{today:%Y-%m-%d}?orderType=2", kiosk=kiosk)
    assert [o["TitleId"] for o in r.json()] == [317946]
    sort_type = 900 + kiosk % 50
    c.sql("DELETE FROM cloud_product_group_sort WHERE sort_type = %s", sort_type)
    c.sql("INSERT INTO cloud_product_group_sort VALUES (%s, 5, 10), (%s, 6, 30)", sort_type, sort_type)
    body = c.get(f"/api/productgroupsortorder/{sort_type}", kiosk=kiosk).json()
    assert body["ProductGroupSortOrderType"] == sort_type
    assert [s["ProductGroupId"] for s in body["ProductGroupSortOrders"]] == [6, 5]
