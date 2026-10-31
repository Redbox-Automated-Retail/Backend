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
    assert client.get("/api/me").json() == {"user": "admin", "role": "admin", "bootstrap": True}
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


def test_reprovision_settings_download(client):
    import json
    client.cookies.clear()
    assert client.get("/api/reprovision/990123.json").status_code == 401
    login(client)
    r = client.get("/api/reprovision/990123.json", params=[("address", "10.1.2.3"), ("market", "m1"),
                                                           ("rewrite", "https://a=https://b")])
    assert r.status_code == 200
    assert "kiosk-990123-reprovision.json" in r.headers["content-disposition"]
    doc = json.loads(r.text)
    assert doc["KioskId"] == 990123 and doc["Market"] == "m1" and doc["Hosts"]["Address"] == "10.1.2.3"
    assert doc["UrlRewrites"] == {"https://a": "https://b"} and doc["CaCertificate"].startswith("-----BEGIN")


def test_build_seed_job_and_download(client, tmp_path):
    import json
    import time
    import zipfile
    from redbox_backend.updatesvc import seedzip
    login(client)
    name = "dash-seed-" + uuid.uuid4().hex[:6]
    r = client.post(f"/api/updates/repos/{name}/publish", headers=H,
                    data={"target_path": "C:\\Temp\\dashseed", "label": "one"},
                    files=[("files", ("x.txt", b"seed me\n", "text/plain"))])
    assert r.status_code == 200, r.text
    r = client.post("/api/packages/build", headers=H, json={"repos": [{"name": name}], "date": "01022031"})
    assert r.status_code == 200, r.text
    job, file = r.json()["job"], r.json()["file"]
    assert file == "data-01022031.zip"
    for _ in range(100):
        jobs = {j["id"]: j for j in client.get("/api/packages").json()["jobs"]}
        if jobs[job]["status"] != "running":
            break
        time.sleep(0.2)
    assert jobs[job]["status"] == "done", jobs[job]["progress"]
    assert file in [e["file"] for e in client.get("/api/packages/exports").json()]
    z = tmp_path / file
    z.write_bytes(client.get(f"/api/packages/exports/{file}").content)
    assert seedzip.check(z)["ok"]
    assert json.loads(zipfile.ZipFile(z).read(f"{name}/.revlog/0"))["Label"] == "one"
    assert client.delete(f"/api/packages/exports/{file}", headers=H).status_code == 200

def test_catalog_import_refuses_a_seed_zip(client):
    login(client)
    r = client.post("/api/catalog/import", headers=H, json={"folder": "/packages/data-05072024.zip"})
    assert r.status_code == 400 and "leave this field empty" in r.json()["detail"]


def _login_as(client, user, password):
    client.cookies.clear()
    r = client.post("/api/login", json={"username": user, "password": password})
    assert r.status_code == 200, r.text
    return r.json()


def test_accounts_roles_and_audit(client):
    login(client)
    viewer, operator = "v-" + uuid.uuid4().hex[:6], "o-" + uuid.uuid4().hex[:6]
    pw = "pw-" + uuid.uuid4().hex
    assert client.post("/api/users", headers=H, json={"username": viewer, "password": "short", "role": "viewer"}).status_code == 400
    for name, role in ((viewer, "viewer"), (operator, "operator")):
        assert client.post("/api/users", headers=H, json={"username": name, "password": pw, "role": role}).status_code == 200
    try:
        assert _login_as(client, viewer, pw)["role"] == "viewer"
        assert client.get("/api/kiosks").status_code == 200                       # reads work
        assert client.post("/api/kconfig/push", headers=H, json={}).status_code == 403  # changes do not
        assert _login_as(client, operator, pw)["role"] == "operator"
        assert client.post("/api/users", headers=H, json={"username": "x" + viewer, "password": pw, "role": "admin"}).status_code == 403
        assert client.post("/api/releases/tokens", headers=H, json={"name": "t", "scopes": ["publish"]}).status_code == 403
        assert client.post("/api/staff", headers=H, json={"username": operator, "password": pw}).status_code == 200
        assert client.post("/api/staff/delete", headers=H, json={"username": operator}).status_code == 200
        # disabling the account ends its session at once
        login(client)
        assert client.put(f"/api/users/{operator}", headers=H, json={"enabled": False}).status_code == 200
        client.cookies.clear()
        r = client.post("/api/login", json={"username": operator, "password": pw})
        assert r.status_code == 401
        login(client)
        audit = client.get("/api/events?source=audit&q=" + operator).json()
        assert any(e["kind"] == f"PUT /api/users/{operator}" and e["payload"]["user"] == "admin" for e in audit)
    finally:
        login(client)
        for name in (viewer, operator):
            client.delete(f"/api/users/{name}", headers=H)


def test_control_center_pages(client):
    login(client)
    f = client.get("/api/fleet").json()
    assert {"online", "recent", "stale", "offline", "total"} <= set(f["status"]) and len(f["hourly"]) == 24
    assert "amount" not in str(f) and "revenue" not in str(f)       # the kiosks take no real payments
    s = client.get("/api/system").json()
    assert s["services"][0]["name"] == "rb-db" and s["services"][0]["ok"]
    assert {"Payment mode", "Log level"} <= set(s["settings"])
    assert "total" in client.get("/api/rentals?kind=rentals").json()
    assert "total" in client.get("/api/rentals?kind=returns").json()
    b = client.get("/api/catalog/browse?family=2").json()
    assert {"total", "rows", "counts", "by_type"} <= set(b)
    p = client.get("/api/provisioning").json()
    assert "installer_kiosks" in p and "certificates" in p
    r = client.get("/api/provisioning/installer/990123.json?kiosk_type=R630&test_mode=true")
    assert r.status_code == 200 and r.json()["StoreId"] == 990123 and r.json()["TestMode"] is True
    assert client.get("/api/provisioning/ca.crt").text.startswith("-----BEGIN")
    assert "staff" in client.get("/api/access").json()


def test_kiosk_edit_and_activity(client):
    login(client)
    kiosk = 777001
    from redbox_backend import db  # noqa: F401
    if client.get(f"/api/kiosks/{kiosk}").status_code == 404:
        client.post("/api/tables/kiosks", headers=H, json={"kiosk_id": kiosk})
    r = client.put(f"/api/kiosks/{kiosk}", headers=H, json={"kiosk": {"banner": "Test banner"},
                                                             "location": {"label": "Dash test", "latitude": "32.8"}})
    assert r.status_code == 200, r.text
    d = client.get(f"/api/kiosks/{kiosk}").json()
    assert d["kiosk"]["banner"] == "Test banner" and d["location"]["label"] == "Dash test"
    a = client.get(f"/api/kiosks/{kiosk}/activity").json()
    assert {"requests", "reservations", "inventory", "certificates", "uploads", "daily"} <= set(a)


def test_stores_feed_the_kiosk_installer(client):
    login(client)
    sid = 990600 + uuid.uuid4().int % 300
    state = "St-" + uuid.uuid4().hex[:6]
    r = client.post("/api/stores", headers=H, json={"id": sid, "label": "Dash store", "state_name": state, "banner_name": "Dash banner",
                                                     "address": "1 Main St", "city": "Dallas", "zip": "75201", "market_name": "Dash market",
                                                     "pending": True})
    assert r.status_code == 200, r.text
    s = next(x for x in client.get("/api/stores").json()["stores"] if x["id"] == sid)
    assert s["state_name"] == state and s["ops_market"] == "Dash market" and s["label"] == "Dash store"
    try:
        # what the kiosk installer asks the Proxy API during setup: state, then banner, then store, then its ops market
        api = cc.client()
        states = api.get("/api/installer/getpendingstates").json()["States"]
        assert {"Id": s["state_id"], "Description": state} in states
        banners = api.get(f"/api/installer/getpendingbanners?stateid={s['state_id']}").json()["Banners"]
        assert [b["Name"] for b in banners] == ["Dash banner"]
        kiosks = api.get(f"/api/installer/getpendingkiosks?stateid={s['state_id']}&bannerid={banners[0]['Id']}").json()["PendingKiosks"]
        assert [(k["Id"], k["City"], k["MarketName"]) for k in kiosks] == [(sid, "Dallas", "Dash market")]
        assert api.get(f"/api/installer/getopsmarket?kioskid={sid}").json()["OpsMarket"] == "Dash market"
        # no longer offered: gone from the installer's list
        assert client.post("/api/stores", headers=H, json=dict(s, pending=False)).status_code == 200
        assert not api.get(f"/api/installer/getpendingbanners?stateid={s['state_id']}").json()["Banners"]
    finally:
        client.post("/api/stores/delete", headers=H, json={"id": sid})
        cc.client().sql("DELETE FROM cloud_kiosk_locations WHERE kiosk_id = %s", sid)
