"""Admin dashboard API: login, session, CSRF header, and edits that land in the shared database."""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "kcs"))
import cloudclient as cc  # noqa: E402

pytestmark = pytest.mark.skipif(not cc.database_available(), reason="DATABASE_URL not reachable")

PASSWORD = "test-" + uuid.uuid4().hex  # random per run; nothing fixed in the repository
H = {"X-Requested-With": "rb-admin"}


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient
    from redbox_backend import db
    from redbox_backend.dashboard import auth, server

    os.environ["ADMIN_USERNAME"] = "admin"
    os.environ["ADMIN_PASSWORD_HASH"] = auth.hash_password(PASSWORD, iterations=1000)
    server.app.state.plain = True
    saved, db._pool = db._pool, None  # other modules may hold a pool bound to another event loop
    try:
        with TestClient(server.app) as c:
            yield c
    finally:
        db._pool = saved
        os.environ.pop("ADMIN_PASSWORD_HASH", None)


def login(client):
    client.cookies.clear()
    r = client.post("/api/login", json={"username": "admin", "password": PASSWORD})
    assert r.status_code == 200
    return r


def test_login_required_and_wrong_password(client):
    client.cookies.clear()
    assert client.get("/api/kiosks").status_code == 401
    assert client.post("/api/login", json={"username": "admin", "password": "nope"}).status_code == 401
    assert client.post("/api/login", json={"username": "root", "password": PASSWORD}).status_code == 401


def test_login_session_and_csrf(client):
    r = client.post("/api/login", json={"username": "admin", "password": PASSWORD})
    assert r.status_code == 200 and "rb_admin" in r.cookies
    assert client.get("/api/me").json() == {"user": "admin"}
    assert client.post("/api/kconfig/push", json={}).status_code == 403  # no X-Requested-With
    client.cookies.clear()
    client.cookies.set("rb_admin", "forged.deadbeef")
    assert client.get("/api/me").status_code == 401


def test_overview_kiosks_and_tables(client):
    login(client)
    assert "counts" in client.get("/api/overview").json()
    assert isinstance(client.get("/api/kiosks").json(), list)
    names = {t["name"] for t in client.get("/api/tables").json()}
    assert {"inventory", "products", "pricing_default", "promo_codes"} <= names


def test_inventory_and_promo_edits_reach_the_database(client):
    login(client)
    kiosk, barcode = 777001, "DASH" + uuid.uuid4().hex[:8]
    r = client.post("/api/tables/inventory", headers=H, json={"kiosk_id": kiosk, "barcode": barcode, "deck": "2",
                                                                "slot": "9", "status": "In"})
    assert r.status_code == 200, r.text
    rows = client.get(f"/api/tables/inventory?kiosk_id={kiosk}").json()["rows"]
    assert any(x["barcode"] == barcode and x["slot"] == 9 for x in rows)
    r = client.put("/api/tables/inventory", headers=H, json={"key": {"kiosk_id": kiosk, "barcode": barcode},
                                                               "values": {"status": "Removed"}})
    assert r.json()["updated"] == 1
    code = "DASH" + uuid.uuid4().hex[:6].upper()
    r = client.post("/api/tables/promo_codes", headers=H, json={"code": code, "data": '{"Amount": 1.5}',
                                                                  "kiosk_ids": "[777001]"})
    assert r.status_code == 200, r.text
    # the Proxy Service validates it straight away
    api = cc.client()
    v = api.post("/api/promotion/promocodevalidation", {"MessageId": str(uuid.uuid4()), "KioskId": kiosk, "Code": code},
                 kiosk=kiosk)
    assert v.status_code == 200, v.text
    assert client.post("/api/tables/inventory/delete", headers=H, json={"kiosk_id": kiosk, "barcode": barcode}).json()["deleted"] == 1
    assert client.post("/api/tables/promo_codes/delete", headers=H, json={"code": code}).json()["deleted"] == 1


def test_secrets_are_masked(client):
    login(client)
    rows = client.get("/api/tables/customers").json()["rows"]
    assert all(r.get("password") in (None, "********") for r in rows)


def test_kiosk_configuration_value(client):
    login(client)
    before = client.get("/api/kconfig").json()["version"]
    r = client.post("/api/kconfig", headers=H, json={"category": "Campaigns", "setting": "JobActive", "value": "true",
                                                       "data_type": "bool", "scope": "kiosk", "target": "777001"})
    assert r.json()["version"] == before + 1
    from redbox_backend.iot import kioskconfig  # noqa: F401  (document is built from the same rows)
    vals = [v for v in client.get("/api/kconfig").json()["values"] if v["segment_key"] == "777001"]
    assert vals and vals[0]["name"] == "JobActive"
    assert client.post("/api/kconfig/delete", headers=H, json={"value_id": vals[0]["value_id"]}).status_code == 200


def test_publish_revision_upload(client):
    login(client)
    repo_name = "dash-test-" + uuid.uuid4().hex[:6]
    files = [("files", ("a.txt", b"hello", "application/octet-stream")),
             ("files", ("sub/b.txt", b"world", "application/octet-stream"))]
    r = client.post(f"/api/updates/repos/{repo_name}/publish", headers=H, files=files,
                    data={"target_path": "C:\\Temp\\dash", "label": "t1"})
    assert r.status_code == 200 and r.json()["files"] == 2, r.text
    d = client.get(f"/api/updates/repos/{repo_name}").json()
    assert {(f["target_path"], f["target_name"]) for f in d["files"]} == {("C:\\Temp\\dash", "a.txt"),
                                                                          ("C:\\Temp\\dash\\sub", "b.txt")}
