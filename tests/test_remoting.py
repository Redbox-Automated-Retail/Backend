""".NET Remoting backends: the cloud API's MessageService endpoint and internal hooks, the Mono host, the dashboard broker.

The binary .NET Remoting paths themselves (GenuineChannels gtcp for the broker, HttpChannel + binary formatter for the
MessageService) are exercised by the real kiosk assemblies in tests/clients (run-broker-harness.ps1,
run-kioskservices-harness.ps1), which need Windows .NET Framework. These tests cover what the container stack can check
on its own: the REST mode end to end, the forwarding of a remoting request to Mono, the internal-token checks, the broker
bookkeeping, and the dashboard's handling of a kiosk that is not connected.
"""
import json
import os
import sys
import urllib.parse
import uuid

import httpx
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "kcs"))
import cloudclient as cc  # noqa: E402

pytestmark = pytest.mark.skipif(not cc.database_available(), reason="DATABASE_URL not reachable")

PATH = "/KioskServices/MessageService.rem"
CONTROL = os.environ.get("REMOTING_CONTROL_URL", "http://rb-remoting:8091")


def remoting_up():
    try:
        return httpx.get(CONTROL + "/health", timeout=3).json().get("ok") is True
    except Exception:
        return False


needs_mono = pytest.mark.skipif(not remoting_up(), reason="rb-remoting not reachable")


@pytest.fixture(scope="module")
def c():
    return cc.client()


def raw(c, path, content, headers=None):
    return c.run(c.http.request("POST", path, content=content, headers=headers or {}))


def token():
    from redbox_backend import certs
    return certs.internal_token()


def rest(c, message):
    body = "payload=" + urllib.parse.quote(json.dumps(message))
    r = raw(c, PATH, body.encode(), headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200
    return json.loads(urllib.parse.unquote(r.text))


def test_rest_mode_round_trip_and_event(c):
    kid = cc.new_kiosk()
    marker = uuid.uuid4().hex
    reply = rest(c, {"MessageType": "Test-" + marker, "KioskId": kid, "Password": "secret"})
    assert "Success" in reply
    ev = c.sql("SELECT payload FROM events WHERE source='kioskservices' AND kind=%s", "Test-" + marker)
    assert ev and ev[0]["payload"]["transport"] == "rest"
    assert ev[0]["payload"]["request"]["Password"] == "***"  # secrets are not stored


def test_rest_mode_malformed_json(c):
    r = raw(c, PATH, b"payload=%7Bnot-json", headers={"content-type": "application/x-www-form-urlencoded"})
    assert json.loads(urllib.parse.unquote(r.text)) == {"Success": False, "Error": "malformed JSON"}


@pytest.mark.parametrize("path", ["/_internal/kioskservices/process", "/_internal/broker/registered",
                                  "/_internal/broker/disconnected"])
def test_internal_endpoints_need_token(c, path):
    assert raw(c, path, b"{}").status_code == 403
    assert raw(c, path, b"{}", {"x-internal-token": "wrong"}).status_code == 403


def test_internal_process(c):
    r = raw(c, "/_internal/kioskservices/process", json.dumps({"MessageType": "X"}).encode(),
                  headers={"x-internal-token": token()})
    assert r.status_code == 200 and "Success" in r.json()


def test_broker_registration_bookkeeping(c):
    kid = cc.new_kiosk()
    h = {"x-internal-token": token(), "content-type": "application/json"}
    version = json.dumps({"Runtime": "Kiosk Engine", "Version": "1.88.0.80", "CancelReservation": True})
    for _ in range(2):
        r = raw(c, "/_internal/broker/registered", headers=h,
                    content=json.dumps({"kioskId": kid, "version": version, "host": "HOST test"}).encode())
        assert r.status_code == 200
    row = c.sql("SELECT * FROM broker_kiosks WHERE kiosk_id=%s", kid)[0]
    assert row["connected"] and row["registrations"] == 2 and row["version"]["Version"] == "1.88.0.80"
    raw(c, "/_internal/broker/disconnected", headers=h,
        content=json.dumps({"kioskId": kid, "reason": "test"}).encode())
    row = c.sql("SELECT * FROM broker_kiosks WHERE kiosk_id=%s", kid)[0]
    assert not row["connected"] and row["disconnected_at"] is not None


@needs_mono
def test_binary_request_is_forwarded_to_mono(c):
    # Not a valid remoting message: Mono's HttpChannel answers it (with an error), which proves the hop; 502 would not.
    r = raw(c, PATH, b"\x00\x01garbage", headers={"content-type": "application/octet-stream"})
    assert r.status_code != 502


@needs_mono
def test_control_api_unknown_kiosk():
    assert isinstance(httpx.get(CONTROL + "/broker/kiosks", timeout=5).json(), list)
    r = httpx.post(CONTROL + "/broker/ping", json={"kioskId": 1}, timeout=5)
    assert r.status_code == 404 and "not connected" in r.json()["error"]


@pytest.fixture(scope="module")
def admin():
    from fastapi.testclient import TestClient
    from redbox_backend import db
    from redbox_backend.dashboard import auth, server

    password = "test-" + uuid.uuid4().hex
    os.environ["ADMIN_USERNAME"] = "admin"
    os.environ["ADMIN_PASSWORD_HASH"] = auth.hash_password(password, iterations=1000)
    server.app.state.plain = True
    saved, db._pool = db._pool, None
    try:
        with TestClient(server.app) as t:
            t.headers["X-Requested-With"] = "rb-admin"
            assert t.post("/api/login", json={"username": "admin", "password": password}).status_code == 200
            yield t
    finally:
        db._pool = saved
        os.environ.pop("ADMIN_PASSWORD_HASH", None)


@needs_mono
def test_dashboard_reserve_to_unconnected_kiosk_is_recorded(admin):
    kid = cc.new_kiosk()
    r = admin.post("/api/broker/reserve", json={"kiosk_id": kid, "email": "t@example.com",
                                                "titles": [{"title_id": 1234, "price": 1.99}]})
    assert r.status_code == 404
    d = admin.get("/api/broker").json()
    assert d["reachable"] is True
    failed = [q for q in d["requests"] if q["kiosk_id"] == kid]
    assert failed and failed[0]["kind"] == "reserve" and failed[0]["success"] is False
    assert "not connected" in failed[0]["error"]


def test_offer_expiry_is_a_dotnet_date_literal(c):
    r"""CustomerOfferProxy reads ExpirationDate with a raw (DateTime) cast: it must be "\/Date(ms)\/" on the wire."""
    import datetime as dt
    from redbox_backend.cloudapi import kioskservices as ks
    assert ks.dumps({"d": ks.ms_date("2026-11-06T17:30:00Z"), "s": "/Date(1)/x"}) == \
        '{"d": "\\/Date(1793986200000)\\/", "s": "/Date(1)/x"}'
    kid = cc.new_kiosk()
    expires = dt.datetime(2031, 5, 4, 12, 0, tzinfo=dt.timezone.utc)
    c.sql("INSERT INTO cloud_customer_offers (campaign_id, data, expires_at) VALUES (%s, '{}', %s)", kid, expires)
    try:
        body = "payload=" + urllib.parse.quote(json.dumps({"MessageType": "GetCustomerOffers", "KioskId": kid,
                                                           "CreditCard": {"CardId": str(uuid.uuid4())}}))
        r = raw(c, PATH, body.encode(), headers={"content-type": "application/x-www-form-urlencoded"})
        text = urllib.parse.unquote(r.text)
        assert '"ExpirationDate": "\\/Date(%d)\\/"' % int(expires.timestamp() * 1000) in text
    finally:
        c.sql("DELETE FROM cloud_customer_offers WHERE campaign_id = %s", kid)
