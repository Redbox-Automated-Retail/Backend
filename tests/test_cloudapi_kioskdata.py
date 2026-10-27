"""Kiosk Data Service, Device Service upstreams, Ad Server, Product Reels and Bluefin protocol tests."""
import base64
import gzip
import json
import os
import struct
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "kcs"))
import cloudclient as cc  # noqa: E402

pytestmark = pytest.mark.skipif(not cc.database_available(), reason="DATABASE_URL not reachable")


@pytest.fixture(scope="module")
def c():
    return cc.client()


@pytest.mark.parametrize("kind,body", [
    ("kioskversion", {"Versions": {"KioskEngine": "1.88.0.0"}}),
    ("kioskstatistics", {"Statistics": {"Rentals": "3"}}),
    ("kioskproperties", {"Properties": {"Banner": "Test"}}),
    ("kioskalert", {"UniqueId": None, "AlertType": 9, "SubAlertType": "HttpQueueDepth", "AlertMessage": "x"}),
    ("auditevent", {"LogDate": "2026-10-27T10:00:00", "EventType": "Login", "Username": "tech", "Message": "m"}),
    ("kioskevents", {"KioskEventsData": "[]"}),
    ("kioskfunctioncheck", {"ReportTime": "2026-10-27T10:00:00", "InitTest": "Pass"}),
])
def test_kcs_kiosk_data(c, kind, body):
    kiosk = cc.new_kiosk()
    mid = str(uuid.uuid4())
    payload = {"MessageId": mid, "KioskId": kiosk, "CreatedOn": "2026-10-27T10:00:00Z",
               "CreatedOnLocal": "2026-10-27 06:00:00"} | body
    if kind == "kioskalert":
        payload["UniqueId"] = mid
    r = c.post(f"/api/kioskdata/{kind}", payload, kiosk=kiosk, headers={"Authorization": "test-key"})
    assert r.status_code == 200
    j = r.json()
    assert j["Success"] is True and j["Errors"] == [] and j["StatusCode"] == 200 and j["MessageId"] == mid
    assert c.sqlval("SELECT count(*) FROM events WHERE kiosk_id = %s AND kind = %s", kiosk, kind) == 1
    if kind == "kioskversion":
        assert c.sqlval("SELECT info->'versions'->>'KioskEngine' FROM kiosks WHERE kiosk_id = %s", kiosk) == "1.88.0.0"


def test_kcs_service_event(c):
    kiosk = cc.new_kiosk()
    r = c.post("/api/KioskData/KioskClientServiceEvent", {"KioskId": kiosk, "AssemblyVersion": "1.0.207.0",
                                                          "EventType": "ServiceStarted",
                                                          "EventTime": "2026-10-27T06:00:00",
                                                          "EventTimeUtc": "2026-10-27T10:00:00Z"}, kiosk=kiosk)
    assert r.status_code == 200 and r.json()["Success"] is True
    assert c.sqlval("SELECT info->'kcs'->>'LastEvent' FROM kiosks WHERE kiosk_id = %s", kiosk) == "ServiceStarted"


def test_device_service_posts(c):
    kiosk = cc.new_kiosk()
    status = {"Id": kiosk, "Serial": None, "MfgSerial": None, "LocalTime": "2026-10-27T04:04:16.8676719-05:00",
              "Assembly": "1.10.13.0", "RBA": None, "RevisionNumber": -1, "SupportsVas": False, "ErrorState": 4}
    for path, body in (("DeviceStatus", status),
                       ("RebootStatus", {"Id": "12345678", "Type": 0, "ConnectTime": "2026-10-27T10:00:00", "KioskId": kiosk}),
                       ("CardStats", {"SessionId": str(uuid.uuid4()), "Id": str(uuid.uuid4()), "KioskId": kiosk,
                                      "CardBrand": "VISA", "SourceType": "EMVContact", "ReadResult": "GoodRead"})):
        r = c.post(f"/api/KioskData/{path}", body, kiosk=kiosk, headers={"Authorization": "Bearer test"})
        assert r.status_code == 200
        assert set(r.json()) >= {"Success", "Errors", "HostName", "StatusCode"} and r.json()["Success"] is True
    assert c.sqlval("SELECT (info->'device_status'->>'ErrorState')::int FROM kiosks WHERE kiosk_id = %s", kiosk) == 4


def compress_session(obj):
    raw = json.dumps(obj).encode()
    return base64.b64encode(struct.pack("<i", len(raw)) + gzip.compress(raw)).decode()


def test_add_kiosk_session_json(c):
    kiosk, sid = cc.new_kiosk(), str(uuid.uuid4())
    session = {"Events": [{"Type": "Start"}, {"Type": "Vend", "Barcode": "123456789"}], "KioskId": kiosk}
    r = c.post("/api/KioskData/AddKioskSessionJson", {"KioskId": kiosk, "SessionId": sid,
                                                      "StartTime": "2026/10/27 17:00:01", "EndTime": "2026/10/27 17:01:50",
                                                      "IsUnCompressed": False, "SessionData": compress_session(session)},
               kiosk=kiosk, headers={"Authorization": "Bearer test"})
    assert r.status_code == 200 and r.json()["success"] is True
    assert c.sqlval("SELECT data->'Events'->1->>'Barcode' FROM cloud_kiosk_sessions WHERE session_id = %s", sid) == "123456789"


def test_bluefin_activation(c):
    kiosk = cc.new_kiosk()
    body = {"KioskId": kiosk, "ReaderSerialNumber": "MFG123", "InjectedSerialNumber": "UNIT456",
            "LocalDateTime": "2026-10-27T04:04:16.86-05:00"}
    for prefix in ("", "/bluefinservice"):
        r = c.post(prefix + "/device/activate", body, headers={"x-api-key": "k"})
        assert r.status_code == 200 and r.json()["Success"] is True
    assert c.sqlval("SELECT status FROM cloud_bluefin_devices WHERE kiosk_id = %s", kiosk) == "active"
    r = c.post("/bluefinservice/device/deactivate", {"KioskId": kiosk, "ReaderSerialNumber": "MFG123"})
    assert r.json()["Success"] is True
    assert c.post("/device/activate", {"KioskId": kiosk}).status_code == 400


def test_ads(c):
    kiosk = cc.new_kiosk()
    for prefix in ("/api", "/api/api"):
        one = c.get(prefix + "/ads/Welcome", kiosk=kiosk)
        assert one.status_code == 200
        ad = one.json()["Data"]
        assert ad["Location"] == "Welcome" and ad["MimeType"] == "image/png" and ad["Expirable"] is True
        img = c.get(ad["AssetUrl"].replace("http://cloud.test", ""))
        assert img.status_code == 200 and img.content[:4] == b"\x89PNG"
        pop = c.post(prefix + "/ads/proofOfPlay", {"AdvertisementId": ad["Id"], "DisplayedTime": "2026-10-27T10:00:00",
                                                   "DisplayedMilliseconds": 8000}, kiosk=kiosk)
        assert pop.status_code == 200
        assert c.sqlval("SELECT displayed_ms FROM cloud_ad_impressions WHERE id = %s", uuid.UUID(ad["Id"])) == 8000
    cache = c.get("/api/api/ads/cache?locations=Welcome&locations=TitleDetail&locations=VSM", kiosk=kiosk).json()
    assert [a["Location"] for a in cache["Data"]] == ["Welcome", "TitleDetail", "VSM"]
    exp = c.post(f"/api/api/ads/{cache['Data'][0]['Id']}/expire", kiosk=kiosk)
    assert exp.status_code == 200
    assert c.get("/api/api/ads/Multiple", kiosk=kiosk).status_code == 404
    an = c.post("/api/api/analytics/get_ad", {"KioskId": kiosk, "Count": 3}, kiosk=kiosk)
    assert an.status_code == 200 and an.json()["Success"] is True


def test_reels(c):
    gid = cc.new_kiosk()
    c.sql("INSERT INTO cloud_more_like_this VALUES (%s, %s) ON CONFLICT DO NOTHING", gid, [gid + 1, gid + 2])
    r = c.post("/products/reels/bulkmorelikethis", {"productIds": [str(gid), "1"]})
    assert r.status_code == 200
    assert r.json() == [{"id": str(gid), "productIds": [str(gid + 1), str(gid + 2)]}]
