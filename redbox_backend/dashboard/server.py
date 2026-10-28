"""Admin dashboard: `python -m redbox_backend dashboard [--port 8443] [--no-tls]`.

A single-page web UI (static/) over a JSON API on the same database the services use, so every edit is what
the kiosks get on their next call. Actions that have to reach a kiosk now (push configuration, deploy a file
set, remote jobs) go through rb-iot's operator API (IOT_ADMIN_URL). Every route under /api except /api/login
needs the admin session; changing requests also need the header `X-Requested-With: rb-admin`.
"""
import argparse
import asyncio
import base64
import datetime as dt
import hashlib
import json
import logging
import os
import pathlib
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .. import blobs, certs, config, db, filesets
from ..iot import kioskconfig
from ..updatesvc import repo
from . import auth

log = logging.getLogger("dashboard")
STATIC = pathlib.Path(__file__).parent / "static"
IOT_ADMIN_URL = os.environ.get("IOT_ADMIN_URL", f"http://rb-iot:{config.IOT_ADMIN_PORT}")
ONLINE_MINUTES = 20

app = FastAPI(title="Redbox admin", docs_url=None, redoc_url=None, openapi_url=None)


# ------------------------------------------------------------------------------------------ plumbing

def _json_default(o):
    if isinstance(o, (dt.datetime, dt.date)):
        return o.isoformat()
    if isinstance(o, (bytes, memoryview)):
        return f"<{len(bytes(o))} bytes>"
    return str(o)


def ok(data, status=200):
    return Response(json.dumps(data, default=_json_default), status_code=status, media_type="application/json")


@app.on_event("startup")
async def _startup():
    await db.init()
    certs.ensure_all()
    if not auth.configured():
        log.warning("ADMIN_PASSWORD_HASH is not set: nobody can log in (run scripts/rb.ps1 set-admin-password)")


@app.middleware("http")
async def _guard(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/") and path not in ("/api/login", "/api/status"):
        if not auth.read_session(request.cookies.get(auth.COOKIE)):
            return JSONResponse({"error": "login required"}, status_code=401)
        if request.method not in ("GET", "HEAD") and request.headers.get("x-requested-with") != "rb-admin":
            return JSONResponse({"error": "missing X-Requested-With"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


async def iot(path, body=None, method="POST", timeout=70):
    def call():
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(IOT_ADMIN_URL + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            raise HTTPException(e.code, f"iot service: {e.read().decode(errors='replace')[:300]}")
        except Exception as e:
            raise HTTPException(502, f"iot service unreachable: {e}")
    return await asyncio.to_thread(call)


async def online_clients():
    try:
        return {c["clientId"]: c for c in await iot("/clients", method="GET", timeout=5)}
    except HTTPException:
        return {}


# -------------------------------------------------------------------------------------------- login

@app.get("/api/status")
async def status():
    return {"configured": auth.configured()}


@app.post("/api/login")
async def login(body: dict):
    if not auth.check_login(body.get("username"), body.get("password")):
        await asyncio.sleep(1.0)  # slow down guessing
        await db.record_event(None, "dashboard", "login-failed", {"username": body.get("username")})
        return JSONResponse({"error": "wrong user name or password" if auth.configured()
                             else "no admin password is configured"}, status_code=401)
    await db.record_event(None, "dashboard", "login", {"username": body.get("username")})
    resp = JSONResponse({"user": auth.username()})
    resp.set_cookie(auth.COOKIE, auth.make_session(auth.username()), httponly=True, samesite="strict",
                    secure=not app.state.plain, max_age=int(float(os.environ.get("ADMIN_SESSION_HOURS") or 8) * 3600))
    return resp


@app.post("/api/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.get("/api/me")
async def me():
    return {"user": auth.username()}


# ----------------------------------------------------------------------------------------- overview

@app.get("/api/overview")
async def overview():
    clients = await online_clients()
    counts = await db.fetchrow(
        "SELECT (SELECT count(*) FROM kiosks) AS kiosks,"
        " (SELECT count(*) FROM kiosks WHERE last_seen_at > now() - make_interval(mins => %s)) AS recent,"
        " (SELECT count(*) FROM cloud_transactions WHERE created_at > now() - interval '24 hours') AS transactions_24h,"
        " (SELECT count(*) FROM cloud_rentals WHERE status = 'out') AS rentals_out,"
        " (SELECT count(*) FROM cloud_inventory) AS discs,"
        " (SELECT count(*) FROM upd_repositories) AS repositories,"
        " (SELECT count(*) FROM fs_filesets) AS filesets,"
        " (SELECT count(*) FROM events WHERE created_at > now() - interval '1 hour') AS events_1h", ONLINE_MINUTES)
    recent = await db.fetch("SELECT * FROM events ORDER BY id DESC LIMIT 15")
    return ok({"counts": dict(counts, mqtt_online=len(clients)), "events": recent})


# ------------------------------------------------------------------------------------------- kiosks

@app.get("/api/kiosks")
async def kiosks():
    clients = await online_clients()
    rows = await db.fetch(
        "SELECT k.*, r.ucs_version AS iot_version, r.assembly_version, r.last_ping_at, r.broker_registered,"
        " s.last_check_in, s.enabled AS store_enabled, c.reported_version AS config_version, c.activated_version,"
        " (SELECT count(*) FROM cloud_inventory i WHERE i.kiosk_id = k.kiosk_id) AS discs,"
        " (SELECT max(created_at) FROM cloud_transactions t WHERE t.kiosk_id = k.kiosk_id) AS last_transaction,"
        " l.label, l.city, l.state AS location_state"
        " FROM kiosks k LEFT JOIN iot_registrations r ON r.kiosk_id = k.kiosk_id"
        " LEFT JOIN upd_stores s ON s.number = k.kiosk_id::text LEFT JOIN cfg_kiosk_state c ON c.kiosk_id = k.kiosk_id"
        " LEFT JOIN cloud_kiosk_locations l ON l.kiosk_id = k.kiosk_id ORDER BY k.kiosk_id")
    out = []
    for r in rows:
        kid = str(r["kiosk_id"])
        info = r.get("info") or {}
        recent = r["last_seen_at"] and r["last_seen_at"] > dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=ONLINE_MINUTES)
        out.append(dict(r, mqtt_connected=kid in clients, online=kid in clients or bool(recent),
                        versions=info.get("Versions"), info=None))
    return ok(out)


@app.get("/api/kiosks/{kiosk_id}")
async def kiosk_detail(kiosk_id: int):
    k = await db.fetchrow("SELECT * FROM kiosks WHERE kiosk_id=%s", kiosk_id)
    if k is None:
        raise HTTPException(404)
    clients = await online_clients()
    number = str(kiosk_id)
    heads = await db.fetch(
        "SELECT r.name AS repo, h.head AS reported, h.reported_at,"
        " (SELECT hash FROM upd_revisions v WHERE v.repo = r.name ORDER BY seq DESC LIMIT 1) AS server_head,"
        " (SELECT label FROM upd_revisions v WHERE v.repo = r.name AND v.hash = h.head LIMIT 1) AS reported_label"
        " FROM upd_repositories r LEFT JOIN upd_store_heads h ON h.repo = r.name AND h.number = %s ORDER BY r.name", number)
    subscribed = set(await repo.repositories_for_store(number))
    fs_state = await db.fetch("SELECT s.*, f.name, v.version FROM fs_kiosk_state s LEFT JOIN fs_filesets f ON f.id = s.fileset_id"
                              " LEFT JOIN fs_revisions v ON v.id = s.revision_id WHERE s.kiosk_id=%s ORDER BY s.fileset_id", kiosk_id)
    desired = {}
    for channel in ("ucs", "legacy"):
        for fs_id, d in (await filesets.desired_deployments(kiosk_id, k["market"], channel)).items():
            desired[f"{channel}:{fs_id}"] = d
    return ok({
        "kiosk": k, "mqtt": clients.get(number),
        "registration": await db.fetchrow("SELECT * FROM iot_registrations WHERE kiosk_id=%s", kiosk_id),
        "store": await db.fetchrow("SELECT * FROM upd_stores WHERE number=%s", number),
        "location": await db.fetchrow("SELECT * FROM cloud_kiosk_locations WHERE kiosk_id=%s", kiosk_id),
        "config": await db.fetchrow("SELECT * FROM cfg_kiosk_state WHERE kiosk_id=%s", kiosk_id),
        "config_current": await kioskconfig.current_version(),
        "repositories": [dict(h, subscribed=h["repo"] in subscribed,
                              current=bool(h["reported"]) and h["reported"] == h["server_head"]) for h in heads],
        "filesets": fs_state, "deployments": list(desired.values()),
        "device": await db.fetchrow("SELECT payload, created_at FROM events WHERE kiosk_id=%s AND source='deviceservice'"
                                    " AND kind='devicestatus' ORDER BY id DESC LIMIT 1", kiosk_id),
        "bluefin": await db.fetch("SELECT * FROM cloud_bluefin_devices WHERE kiosk_id=%s", kiosk_id),
        "transactions": await db.fetch("SELECT id, reference_number, kind, status, amount, created_at FROM cloud_transactions"
                                       " WHERE kiosk_id=%s ORDER BY id DESC LIMIT 10", kiosk_id),
        "events": await db.fetch("SELECT * FROM events WHERE kiosk_id=%s ORDER BY id DESC LIMIT 30", kiosk_id),
    })


# ------------------------------------------------------------------------------- generic table editor

TABLES = {
    "kiosks": ("kiosks", ["kiosk_id"], "Kiosks (market, banner, time zone)"),
    "locations": ("cloud_kiosk_locations", ["kiosk_id"], "Kiosk locations"),
    "stores": ("upd_stores", ["number"], "Update Service stores (BITS policy, time)"),
    "inventory": ("cloud_inventory", ["kiosk_id", "barcode"], "Inventory (discs per kiosk)"),
    "barcodes": ("cloud_barcodes", ["barcode"], "Barcode -> product"),
    "products": ("cloud_products", ["title_id"], "Catalog titles"),
    "profile_products": ("profile_products", ["product_id"], "Profile product data"),
    "merch_orders": ("cloud_merch_orders", ["id"], "Merchandizing orders"),
    "sort_order": ("cloud_product_group_sort", ["sort_type", "product_group_id"], "Product group sort order"),
    "pricing_default": ("cloud_pricing_default", ["id"], "Default rental pricing"),
    "pricing_rental": ("cloud_pricing_rental", ["id"], "Rental pricing by price level"),
    "pricing_purchase": ("cloud_pricing_purchase", ["id"], "Purchase pricing"),
    "promo_codes": ("cloud_promo_codes", ["code"], "Promo codes"),
    "return_promo": ("cloud_return_promo", ["id"], "Return-visit promotion"),
    "campaigns": ("cloud_campaigns", ["campaign_id"], "Campaigns (start screens, in-cart)"),
    "subscription_plans": ("cloud_subscription_plans", ["id"], "Subscription plans"),
    "ads": ("cloud_ads", ["id"], "Ads"),
    "customers": ("cloud_customers", ["customer_id"], "Customers"),
    "cards": ("cloud_cards", ["card_key"], "Cards and gift cards"),
    "reservations": ("cloud_reservations", ["reference_number"], "Reservations"),
    "rentals": ("cloud_rentals", ["id"], "Rentals"),
    "returns": ("cloud_returns", ["id"], "Returns"),
}
_columns_cache = {}
# Shown masked; an unchanged mask is not written back.
SECRET_COLUMNS = {"password", "pin", "password_sha256"}
MASK = "********"


async def columns(table):
    if table not in _columns_cache:
        _columns_cache[table] = await db.fetch(
            "SELECT column_name, data_type, udt_name, is_nullable = 'YES' AS nullable, column_default"
            " FROM information_schema.columns WHERE table_schema = 'public' AND table_name = %s ORDER BY ordinal_position",
            table)
    return _columns_cache[table]


def _sql_type(col):
    if col["data_type"] == "ARRAY":
        return {"_int8": "bigint[]", "_int4": "integer[]", "_text": "text[]"}.get(col["udt_name"], "text[]")
    if col["data_type"] == "USER-DEFINED":
        return col["udt_name"]
    return col["data_type"]


def _value(col, v):
    """Convert a value from the UI (usually a string) to a parameter for this column."""
    if v is None or (isinstance(v, str) and v.strip() == "" and col["nullable"]):
        return None
    t = col["data_type"]
    if t == "jsonb" or t == "json":
        return db.jsonb(json.loads(v) if isinstance(v, str) else v)
    if t == "ARRAY":
        if isinstance(v, str):
            v = json.loads(v) if v.strip().startswith("[") else [x.strip() for x in v.split(",") if x.strip()]
        return [int(x) for x in v] if col["udt_name"] in ("_int8", "_int4") else [str(x) for x in v]
    if t == "boolean":
        return v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true", "yes", "on", "t")
    return str(v) if not isinstance(v, (int, float)) else v


def _table(name):
    if name not in TABLES:
        raise HTTPException(404, "unknown table")
    return TABLES[name]


@app.get("/api/tables")
async def table_list():
    return ok([{"name": n, "table": t, "key": k, "title": title} for n, (t, k, title) in TABLES.items()])


@app.get("/api/tables/{name}")
async def table_rows(name: str, kiosk_id: Optional[int] = None, q: Optional[str] = None, limit: int = 100,
                     offset: int = 0):
    table, key, title = _table(name)
    cols = await columns(table)
    names = [c["column_name"] for c in cols]
    select = ", ".join(f"octet_length({c['column_name']}) AS {c['column_name']}" if c["data_type"] == "bytea"
                       else c["column_name"] for c in cols)
    where, args = [], []
    if kiosk_id is not None and "kiosk_id" in names:
        where.append("kiosk_id = %s")
        args.append(kiosk_id)
    if q:
        text_cols = [c["column_name"] for c in cols if c["data_type"] in ("text", "character varying")]
        where.append("(" + " OR ".join([f"{c} ILIKE %s" for c in text_cols] +
                                       [f"{c}::text = %s" for c in key]) + ")")
        args += [f"%{q}%"] * len(text_cols) + [q] * len(key)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = await db.fetchval(f"SELECT count(*) FROM {table}{clause}", *args)
    rows = await db.fetch(f"SELECT {select} FROM {table}{clause} ORDER BY {', '.join(key)} LIMIT %s OFFSET %s",
                          *args, min(limit, 1000), offset)
    for r in rows:
        for c in SECRET_COLUMNS & set(r):
            if r[c] is not None:
                r[c] = MASK
    return ok({"title": title, "key": key, "total": total, "rows": rows,
               "columns": [dict(c, type=_sql_type(c)) for c in cols]})


@app.post("/api/tables/{name}")
async def table_insert(name: str, body: dict):
    table, key, _ = _table(name)
    cols = {c["column_name"]: c for c in await columns(table)}
    values = {k: v for k, v in body.items() if k in cols and cols[k]["data_type"] != "bytea"
              and not (v in (None, "") and cols[k]["column_default"])}
    if not values:
        raise HTTPException(400, "no values")
    names = list(values)
    sql = (f"INSERT INTO {table} ({', '.join(names)}) VALUES ("
           + ", ".join(f"CAST(%s AS {_sql_type(cols[n])})" if cols[n]["data_type"] not in ("jsonb", "json") else "%s"
                       for n in names) + f") RETURNING {', '.join(key)}")
    try:
        row = await db.fetchrow(sql, *[_value(cols[n], values[n]) for n in names])
    except Exception as e:
        raise HTTPException(400, str(e).splitlines()[0])
    await db.record_event(None, "dashboard", "insert", {"table": table, "key": row})
    return ok(row)


def _key_args(key, cols, body):
    if not all(k in body for k in key):
        raise HTTPException(400, "key columns missing")
    return " AND ".join(f"{k} = CAST(%s AS {_sql_type(cols[k])})" for k in key), [body[k] for k in key]


@app.put("/api/tables/{name}")
async def table_update(name: str, body: dict):
    table, key, _ = _table(name)
    cols = {c["column_name"]: c for c in await columns(table)}
    where, kargs = _key_args(key, cols, body.get("key") or {})
    values = {k: v for k, v in (body.get("values") or {}).items() if k in cols and cols[k]["data_type"] != "bytea"
              and not (k in SECRET_COLUMNS and v == MASK)}
    if not values:
        raise HTTPException(400, "no values")
    sets = ", ".join(f"{n} = " + (f"CAST(%s AS {_sql_type(cols[n])})" if cols[n]["data_type"] not in ("jsonb", "json")
                                  else "%s") for n in values)
    try:
        n = await db.execute(f"UPDATE {table} SET {sets} WHERE {where}", *[_value(cols[k], v) for k, v in values.items()],
                             *kargs)
    except Exception as e:
        raise HTTPException(400, str(e).splitlines()[0])
    await db.record_event(None, "dashboard", "update", {"table": table, "key": body.get("key"), "columns": list(values)})
    return ok({"updated": n})


@app.post("/api/tables/{name}/delete")
async def table_delete(name: str, body: dict):
    table, key, _ = _table(name)
    cols = {c["column_name"]: c for c in await columns(table)}
    where, kargs = _key_args(key, cols, body)
    try:
        n = await db.execute(f"DELETE FROM {table} WHERE {where}", *kargs)
    except Exception as e:
        raise HTTPException(400, str(e).splitlines()[0])
    await db.record_event(None, "dashboard", "delete", {"table": table, "key": body})
    return ok({"deleted": n})


# ------------------------------------------------------------------------------------- transactions

@app.get("/api/transactions")
async def transactions(kiosk_id: Optional[int] = None, kind: Optional[str] = None, status: Optional[str] = None,
                       q: Optional[str] = None, limit: int = 100, offset: int = 0):
    where, args = [], []
    for col, v in (("t.kiosk_id", kiosk_id), ("t.kind", kind), ("t.status", status)):
        if v not in (None, ""):
            where.append(f"{col} = %s")
            args.append(v)
    if q:
        where.append("(t.reference_number::text = %s OR c.email ILIKE %s OR c.cpn = %s OR k.last_four = %s)")
        args += [q, f"%{q}%", q, q]
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    base = (" FROM cloud_transactions t LEFT JOIN cloud_customers c ON c.customer_id = t.customer_id"
            " LEFT JOIN cloud_cards k ON k.card_key = t.card_key" + clause)
    rows = await db.fetch("SELECT t.id, t.reference_number, t.kiosk_id, t.kind, t.status, t.amount, t.errors, t.created_at,"
                          " t.reconciled_at, c.email, c.cpn, k.last_four, k.is_gift_card,"
                          " (SELECT count(*) FROM cloud_rentals r WHERE r.transaction_id = t.id) AS items" + base +
                          " ORDER BY t.id DESC LIMIT %s OFFSET %s", *args, min(limit, 500), offset)
    return ok({"total": await db.fetchval("SELECT count(*)" + base, *args), "rows": rows})


@app.get("/api/transactions/{tid}")
async def transaction(tid: int):
    t = await db.fetchrow("SELECT * FROM cloud_transactions WHERE id=%s", tid)
    if t is None:
        raise HTTPException(404)
    customer = await db.fetchrow("SELECT * FROM cloud_customers WHERE customer_id=%s", t["customer_id"])
    for c in SECRET_COLUMNS & set(customer or {}):
        customer[c] = MASK if customer[c] is not None else None
    return ok({"transaction": t, "customer": customer,
               "card": await db.fetchrow("SELECT card_key, account_number, bin, last_four, is_gift_card, gift_balance"
                                         " FROM cloud_cards WHERE card_key=%s", t["card_key"]),
               "rentals": await db.fetch("SELECT * FROM cloud_rentals WHERE transaction_id=%s ORDER BY id", tid),
               "returns": await db.fetch("SELECT r.* FROM cloud_returns r JOIN cloud_rentals x ON x.id = r.rental_id"
                                         " WHERE x.transaction_id=%s ORDER BY r.id", tid),
               "promos": await db.fetch("SELECT * FROM cloud_promo_redemptions WHERE transaction_id=%s", tid)})


# ------------------------------------------------------------------------------------------ updates

@app.get("/api/updates/repos")
async def repos():
    rows = await db.fetch(
        "SELECT r.name, r.auto_subscribe, r.serve_head, r.canonical_edition,"
        " (SELECT count(*) FROM upd_revisions v WHERE v.repo = r.name) AS revisions,"
        " h.hash AS head, h.label, h.created_at AS head_created,"
        " (SELECT count(*) FROM upd_snapshots s WHERE s.repo = r.name AND s.head_hash = h.hash) AS snapshot_files,"
        " (SELECT count(*) FROM upd_store_heads x WHERE x.repo = r.name AND x.head = h.hash) AS stores_current,"
        " (SELECT count(*) FROM upd_store_heads x WHERE x.repo = r.name) AS stores_reporting"
        " FROM upd_repositories r LEFT JOIN LATERAL (SELECT * FROM upd_revisions v WHERE v.repo = r.name"
        " ORDER BY seq DESC LIMIT 1) h ON true ORDER BY r.name")
    return ok(rows)


@app.get("/api/updates/repos/{name}")
async def repo_detail(name: str):
    revs = await db.fetch(
        "SELECT v.seq, v.hash, v.label, v.created_at, count(c.*) AS changes,"
        " count(c.*) FILTER (WHERE c.is_seed AND NOT c.composite) AS seeds,"
        " count(c.*) FILTER (WHERE NOT c.is_seed AND NOT c.composite) AS deltas"
        " FROM upd_revisions v LEFT JOIN upd_changes c ON c.repo = v.repo AND c.rev_hash = v.hash WHERE v.repo = %s"
        " GROUP BY v.seq, v.hash, v.label, v.created_at ORDER BY v.seq DESC LIMIT 60", name)
    files = await db.fetch("SELECT target_path, target_name, version_hash FROM upd_snapshots s WHERE repo=%s"
                           " AND head_hash = (SELECT hash FROM upd_revisions WHERE repo=%s ORDER BY seq DESC LIMIT 1)"
                           " ORDER BY target_path, target_name", name, name)
    return ok({"revisions": revs, "files": files})


@app.put("/api/updates/repos/{name}")
async def repo_settings(name: str, body: dict):
    await db.execute("UPDATE upd_repositories SET auto_subscribe=%s WHERE name=%s", bool(body.get("auto_subscribe")), name)
    return ok({"ok": True})


@app.post("/api/updates/repos/{name}/publish")
async def publish(name: str, target_path: str = Form(...), label: str = Form(""),
                  files: List[UploadFile] = File(...)):
    with tempfile.TemporaryDirectory() as td:
        items = []
        for f in files:
            rel = f.filename.replace("\\", "/").lstrip("/")
            parts = [p for p in rel.split("/") if p not in ("", ".", "..")]
            local = pathlib.Path(td, *parts)
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(await f.read())
            sub = "\\".join(parts[:-1])
            items.append((target_path.rstrip("\\") + ("\\" + sub if sub else ""), parts[-1], local))
        rev = await repo.publish_revision(name, items, label=label or None)
    await db.record_event(None, "dashboard", "publish", {"repo": name, "revision": rev, "files": len(items)})
    return ok({"revision": rev, "files": len(items)})


@app.post("/api/updates/repos/{name}/snapshot")
async def snapshot(name: str):
    res = await repo.build_snapshot(name)
    return ok({k: v for k, v in res.items() if k != "problems"} | {"problems": res["problems"][:20]})


@app.get("/api/updates/stores")
async def stores():
    repos_ = [r["name"] for r in await db.fetch("SELECT name FROM upd_repositories ORDER BY name")]
    heads = {r["name"]: r["hash"] for r in await db.fetch(
        "SELECT DISTINCT ON (repo) repo AS name, hash FROM upd_revisions ORDER BY repo, seq DESC")}
    out = []
    for s in await db.fetch("SELECT * FROM upd_stores ORDER BY number"):
        reported = {h["repo"]: h for h in await db.fetch("SELECT * FROM upd_store_heads WHERE number=%s", s["number"])}
        links = {l["repo"]: l["linked"] for l in await db.fetch("SELECT * FROM upd_store_repos WHERE number=%s", s["number"])}
        subscribed = set(await repo.repositories_for_store(s["number"]))
        cells = {}
        for r in repos_:
            h = reported.get(r)
            state = "current" if h and h["head"] == heads.get(r) else ("behind" if h else "missing")
            cells[r] = {"state": state if r in subscribed else "not subscribed", "reported": h and h["head"],
                        "link": {True: "linked", False: "unlinked"}.get(links.get(r), "auto")}
        out.append({"store": s, "repos": cells})
    return ok({"repositories": repos_, "stores": out})


@app.post("/api/updates/links")
async def links(body: dict):
    number, name, mode = str(body["number"]), body["repo"], body.get("mode", "auto")
    await db.execute("INSERT INTO upd_stores (number) VALUES (%s) ON CONFLICT DO NOTHING", number)
    if mode == "auto":
        await db.execute("DELETE FROM upd_store_repos WHERE number=%s AND repo=%s", number, name)
    else:
        await db.execute("INSERT INTO upd_store_repos (number, repo, linked) VALUES (%s,%s,%s) ON CONFLICT (number, repo)"
                         " DO UPDATE SET linked = EXCLUDED.linked", number, name, mode == "linked")
    return ok({"subscribed": await repo.repositories_for_store(number)})


@app.post("/api/updates/repos/{name}/pin")
async def repo_pin(name: str, body: dict):
    """Serve an older revision (or the newest with revision = null); fresh kiosks then get its snapshot."""
    rev = (body.get("revision") or "").upper() or None
    if rev and not await db.fetchval("SELECT 1 FROM upd_revisions WHERE repo=%s AND hash=%s", name, rev):
        raise HTTPException(404, "unknown revision")
    await db.execute("UPDATE upd_repositories SET serve_head=%s WHERE name=%s", rev, name)
    res = await repo.build_snapshot(name, rev)
    await db.record_event(None, "dashboard", "pin", {"repo": name, "revision": rev, "snapshot_files": res["files"]})
    return ok({k: v for k, v in res.items() if k != "problems"} | {"problems": res["problems"][:20]})


# ------------------------------------------------------------------------ reservation broker (legacy)

REMOTING_CONTROL_URL = os.environ.get("REMOTING_CONTROL_URL", "http://rb-remoting:8091")


async def remoting(path, body=None, method="POST", timeout=150):
    def call():
        data = None if body is None else json.dumps(body, default=str).encode()
        req = urllib.request.Request(REMOTING_CONTROL_URL + path, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")
            try:
                detail = json.loads(detail).get("error", detail)
            except Exception:
                pass
            raise HTTPException(e.code if e.code != 500 else 502, f"broker: {detail}")
        except Exception as e:
            raise HTTPException(502, f"remoting host unreachable: {e}")
    return await asyncio.to_thread(call)


def _net_date(value):
    """JavaScriptSerializer's DateTime format, which the Mono host deserializes into ReservationCart3.ReserveDate."""
    return f"/Date({int(value.timestamp() * 1000)})/"


@app.get("/api/broker")
async def broker():
    try:
        live = {k["kioskId"]: k for k in await remoting("/broker/kiosks", method="GET", timeout=5)}
    except HTTPException:
        live = None
    rows = await db.fetch("SELECT * FROM broker_kiosks ORDER BY kiosk_id")
    for r in rows:
        k = None if live is None else live.get(r["kiosk_id"])
        r["live"] = None if live is None else k is not None
        # connected / lost (GenuineChannels is re-establishing it; calls are refused at once) / not answering (a call
        # timed out; calls are still made, each bounded) / not connected / unknown (rb-remoting unreachable)
        r["connection"] = "unknown" if live is None else "not connected" if k is None else             "lost" if k.get("lostUtc") else "not answering" if k.get("noAnswerUtc") else "connected"
        k = k or {}
        r["lost_at"], r["lost_reason"] = k.get("lostUtc") or k.get("noAnswerUtc"), k.get("lostReason") or k.get("noAnswerReason")
    return ok({"reachable": live is not None, "kiosks": rows,
               "requests": await db.fetch("SELECT * FROM broker_requests ORDER BY id DESC LIMIT 50")})


async def _broker_record(kiosk_id, kind, ref, request, result, success, error):
    await db.execute("INSERT INTO broker_requests (kiosk_id, kind, reference_number, request, result, success, error)"
                     " VALUES (%s,%s,%s,%s,%s,%s,%s)", kiosk_id, kind, ref, db.jsonb(request), db.jsonb(result),
                     success, error)
    await db.record_event(kiosk_id, "broker", kind, {"reference": ref, "success": success, "error": error})


@app.post("/api/broker/reserve")
async def broker_reserve(body: dict):
    """Online reservation pushed to a kiosk: the broker calls Reserve3 on it and the kiosk holds the discs.

    The reservation is stored in cloud_reservations (shared with the Transaction API), so the kiosk's later
    ValidatePickup / PickupAuthorize / ReservationPickup calls find it.
    """
    kiosk_id = int(body["kiosk_id"])
    titles = body.get("titles") or []
    if not titles:
        raise HTTPException(400, "at least one title")
    ref = int(await db.fetchval("SELECT nextval('cloud_reference_seq')"))
    now = dt.datetime.now(dt.timezone.utc)
    items, subtotal = [], 0.0
    for i, t in enumerate(titles, 1):
        price = float(t.get("price") or 1.99)
        subtotal += price
        items.append({"ItemID": i, "TitleID": int(t["title_id"]), "ReservationType": 2 if t.get("type") == "purchase" else 1,
                      "Price": price, "Discount": 0, "DiscountType": 0, "DiscountedPrice": price, "NumberOfCredits": 0,
                      "LoyaltyPoints": 0})
    tax_rate = float(body.get("tax_rate") or 0)
    tax = round(subtotal * tax_rate / 100, 2)
    last4 = str(body.get("last4") or "")[-4:]
    card_hash = body.get("card_hash") or hashlib.sha1(f"{body.get('email')}|{last4}".encode()).hexdigest().upper()
    cart = {"KioskID": kiosk_id, "ReferenceNumber": ref, "CardID": card_hash, "IsGc": False, "GcIds": [], "CcIds": [],
            "Email": body.get("email"), "ReserveDate": _net_date(now), "Titles": items, "DiscountedSubTotal": subtotal,
            "SubTotal": subtotal, "Tax": tax, "GrandTotal": round(subtotal + tax, 2), "HiveOnlinePromotionDiscount": 0,
            "NumberOfCreditsRemaining": 0, "PromoCode": body.get("promo_code"), "CustomerNumber": body.get("customer_number"),
            "AppliedCredit": False, "AppliedHiveOnlinePromotion": False, "AppliedTitleMarketing": False,
            "HistoryTitleIDs": [], "TaxRate": tax_rate, "Zip": body.get("zip"), "IsMnp": False, "IsMdv": False,
            "LoyaltyRedemption": False, "DefaultServiceFeeAmount": 0, "ActualServiceFeeAmount": 0,
            "AuthorizeAtPickup": bool(body.get("authorize_at_pickup", True)),
            "ReservationCardHash": {"CardBinHash": card_hash, "Last4": last4}, "AlternateCardHashes": []}
    try:
        result = await remoting("/broker/reserve", {"kioskId": kiosk_id, "cart": cart})
    except HTTPException as e:
        await _broker_record(kiosk_id, "reserve", ref, cart, None, False, e.detail)
        raise
    success = bool(result.get("ReservationSucceeded"))
    reserved = {r.get("ItemID"): r for r in result.get("TitleResults") or []}
    stored_items = [dict(it, ReservedBarcode=(reserved.get(it["ItemID"]) or {}).get("ReservedBarcode"),
                         Reservable=(reserved.get(it["ItemID"]) or {}).get("Reservable")) for it in items]
    if success:
        customer = await db.fetchval("SELECT customer_id FROM cloud_customers WHERE lower(email) = lower(%s)",
                                     body.get("email")) if body.get("email") else None
        await db.execute("INSERT INTO cloud_reservations (reference_number, kiosk_id, customer_id, card_key, status,"
                         " authorize_at_pickup, items, expires_at) VALUES (%s,%s,%s,%s,'reserved',%s,%s, now() + interval"
                         " '1 day')", ref, kiosk_id, customer, card_hash, cart["AuthorizeAtPickup"], db.jsonb(stored_items))
    await _broker_record(kiosk_id, "reserve", ref, cart, result, success, result.get("ErrorMessage"))
    return ok({"reference_number": ref, "result": result})


@app.post("/api/broker/cancel")
async def broker_cancel(body: dict):
    kiosk_id, ref = int(body["kiosk_id"]), int(body["reference_number"])
    # Engines before CancelReservation (2014-2016, broker client 1.7) have no such method on IKioskOperations and
    # say so by leaving "CancelReservation" out of the version JSON they register with.
    version = await db.fetchval("SELECT version FROM broker_kiosks WHERE kiosk_id = %s", kiosk_id) or {}
    if isinstance(version, dict) and not version.get("CancelReservation"):
        detail = (f"kiosk {kiosk_id} (engine {version.get('Version', '?')}) cannot cancel a reservation through the "
                  "broker: its IKioskOperations has no CancelReservation; the hold ends when the reservation expires")
        await _broker_record(kiosk_id, "cancel", ref, body, None, False, detail)
        raise HTTPException(409, detail)
    try:
        result = await remoting("/broker/cancel", {"kioskId": kiosk_id, "referenceNumber": ref})
    except HTTPException as e:
        await _broker_record(kiosk_id, "cancel", ref, body, None, False, e.detail)
        raise
    success = bool(result.get("CancellationSucceeded"))
    if success:
        await db.execute("UPDATE cloud_reservations SET status='cancelled', reason_code='broker', updated_at=now()"
                         " WHERE reference_number=%s AND status='reserved'", ref)
    await _broker_record(kiosk_id, "cancel", ref, body, result, success, result.get("ErrorMessage"))
    return ok({"result": result})


@app.post("/api/broker/ping")
async def broker_ping(body: dict):
    kiosk_id = int(body["kiosk_id"])
    try:
        result = await remoting("/broker/ping", {"kioskId": kiosk_id}, timeout=40)
    except HTTPException as e:
        await _broker_record(kiosk_id, "ping", None, None, None, False, e.detail)
        raise
    await _broker_record(kiosk_id, "ping", None, None, result, True, None)
    return ok(result)


# ----------------------------------------------------------------------------------------- packages

PACKAGES_DIR = os.environ.get("RB_PACKAGES_DIR")
_import_tasks = set()


@app.get("/api/packages")
async def packages():
    from ..updatesvc import editions as ed
    files = []
    if PACKAGES_DIR and os.path.isdir(PACKAGES_DIR):
        known = {e["source"]: e for e in await db.fetch("SELECT name, source, imported_at FROM upd_editions")}
        for p in sorted(pathlib.Path(PACKAGES_DIR).glob("*.zip")):
            src = f"{PACKAGES_DIR.rstrip('/')}/{p.name}"
            files.append({"file": p.name, "size": p.stat().st_size, "date": ed.edition_date(p.stem),
                          "edition": known.get(src, {}).get("name"), "imported_at": known.get(src, {}).get("imported_at")})
    return ok({"dir": PACKAGES_DIR, "files": sorted(files, key=lambda f: str(f["date"])),
               "editions": await db.fetch("SELECT name, source, edition_date, heads, revisions, imported_at"
                                          " FROM upd_editions ORDER BY edition_date"),
               "jobs": await db.fetch("SELECT * FROM upd_import_jobs ORDER BY id DESC LIMIT 10")})


@app.post("/api/packages/import")
async def packages_import(body: dict):
    from ..updatesvc import editions as ed
    if not PACKAGES_DIR:
        raise HTTPException(400, "no packages folder is mounted (set PACKAGES_DIR and run rb.ps1 up)")
    names = body.get("files") or [p.name for p in pathlib.Path(PACKAGES_DIR).glob("*.zip")]
    paths = []
    for n in names:
        p = pathlib.Path(PACKAGES_DIR) / pathlib.PurePath(n).name
        if not p.is_file():
            raise HTTPException(404, f"{n} not found")
        paths.append(str(p))
    dvd = body.get("dvd_data", "latest")
    job = await db.fetchval("INSERT INTO upd_import_jobs (source, options) VALUES (%s,%s) RETURNING id",
                            ", ".join(pathlib.Path(p).name for p in paths), db.jsonb({"dvd_data": dvd}))

    async def progress(msg):
        await db.execute("UPDATE upd_import_jobs SET progress = progress || %s WHERE id=%s",
                         db.jsonb([{"t": dt.datetime.now(dt.timezone.utc).isoformat(), "m": msg}]), job)

    async def run():
        try:
            await ed.import_sources(paths, dvd_data=dvd, progress=progress)
            await db.execute("UPDATE upd_import_jobs SET status='done', finished_at=now() WHERE id=%s", job)
        except Exception as e:
            log.exception("import failed")
            await progress(f"failed: {e}")
            await db.execute("UPDATE upd_import_jobs SET status='failed', finished_at=now() WHERE id=%s", job)
    task = asyncio.create_task(run())
    _import_tasks.add(task)
    task.add_done_callback(_import_tasks.discard)
    await db.record_event(None, "dashboard", "package-import", {"job": job, "files": names, "dvd_data": dvd})
    return ok({"job": job})


@app.get("/api/filesets")
async def fileset_list():
    sets = await db.fetch("SELECT * FROM fs_filesets ORDER BY id")
    for s in sets:
        s["revisions"] = await db.fetch(
            "SELECT r.id, r.name, r.version, r.created_at, count(f.*) AS files FROM fs_revisions r"
            " LEFT JOIN fs_revision_files f ON f.revision_id = r.id WHERE r.fileset_id=%s GROUP BY r.id ORDER BY r.id DESC", s["id"])
        s["deployments"] = await db.fetch("SELECT * FROM fs_deployments WHERE fileset_id=%s ORDER BY id DESC LIMIT 20", s["id"])
        s["states"] = await db.fetch("SELECT * FROM fs_kiosk_state WHERE fileset_id=%s ORDER BY kiosk_id", s["id"])
    return ok(sets)


@app.post("/api/filesets")
async def fileset_create(body: dict):
    await filesets.ensure_fileset(int(body["id"]), body["name"], int(body.get("retention_days") or 30),
                                  int(body.get("retention_revisions") or 3))
    return ok({"ok": True})


@app.post("/api/filesets/{fs_id}/revisions")
async def fileset_revision(fs_id: int, version: str = Form(...), name: str = Form(""), destination: str = Form(...),
                           dependencies: str = Form(""), files: List[UploadFile] = File(...)):
    items = []
    for f in files:
        parts = [p for p in f.filename.replace("\\", "/").split("/") if p not in ("", ".", "..")]
        items.append((destination.rstrip("\\") + "\\" + "\\".join(parts), await f.read()))
    deps = json.loads(dependencies) if dependencies.strip() else []
    rev = await filesets.create_revision(fs_id, name or version, version, items, deps)
    await db.record_event(None, "dashboard", "fileset-revision", {"fileset": fs_id, "revision": rev, "files": len(items)})
    return ok({"revision": rev})


@app.post("/api/filesets/{fs_id}/deploy")
async def fileset_deploy(fs_id: int, body: dict):
    ttype = body.get("target_type", "all")
    dep = await filesets.deploy(fs_id, int(body["revision_id"]) if body.get("revision_id") else None, ttype,
                                str(body.get("target") or "") or None, body.get("channel", "ucs"),
                                activate_start=body.get("activate_start") or None,
                                activate_end=body.get("activate_end") or None,
                                priority=int(body.get("priority") or 1))
    pushed = None
    if dep["channel"] == "ucs":
        pushed = await iot("/push/deploy", {"deploymentId": dep["id"]})
    await db.record_event(None, "dashboard", "fileset-deploy", {"deployment": dep["id"], "pushed": pushed})
    return ok({"deployment": dep, "pushed": pushed})


# ------------------------------------------------------------------------------------ configuration

@app.get("/api/kconfig")
async def kconfig():
    values = await db.fetch(
        "SELECT v.value_id, s.category, s.name, s.data_type, v.value, v.encryption_type, v.effective_at, v.expires_at,"
        " g.segmentation_name, g.key AS segment_key, g.rank FROM cfg_values v JOIN cfg_settings s USING (setting_id)"
        " JOIN cfg_segments g ON g.id = v.segment_id ORDER BY s.category, s.name, g.rank")
    return ok({"version": await kioskconfig.current_version(), "values": values,
               "kiosks": await db.fetch("SELECT * FROM cfg_kiosk_state ORDER BY kiosk_id")})


@app.post("/api/kconfig")
async def kconfig_set(body: dict):
    scope, target = body.get("scope", "all"), body.get("target")
    vid = await kioskconfig.set_value(body["category"], body["setting"], body.get("value"), body.get("data_type") or "string",
                                      kiosk_id=int(target) if scope == "kiosk" else None,
                                      market=target if scope == "market" else None)
    pushed = await _push_config(scope, target) if body.get("push") else None
    return ok({"value_id": vid, "version": await kioskconfig.current_version(), "pushed": pushed})


@app.post("/api/kconfig/delete")
async def kconfig_delete(body: dict):
    await db.execute("DELETE FROM cfg_values WHERE value_id=%s", int(body["value_id"]))
    await kioskconfig.bump_version()
    return ok({"version": await kioskconfig.current_version()})


async def _push_config(scope, target):
    if scope == "kiosk":
        return await iot("/push/config", {"kioskId": int(target)})
    if scope == "market":
        return await iot("/push/config", {"market": target})
    return await iot("/push/config", {})


@app.post("/api/kconfig/push")
async def kconfig_push(body: dict):
    return ok(await _push_config(body.get("scope", "all"), body.get("target")))


@app.get("/api/configfiles")
async def configfiles():
    return ok(await db.fetch("SELECT oid, name, path, generation, jsonb_array_length(data) AS settings, updated_at,"
                             " (SELECT count(*) FROM upd_store_config s WHERE s.oid = f.oid AND"
                             "  (s.active_gen = f.generation OR s.staged_gen = f.generation)) AS stores_current"
                             " FROM upd_config_files f ORDER BY oid"))


@app.get("/api/configfiles/{oid}")
async def configfile(oid: int):
    row = await db.fetchrow("SELECT * FROM upd_config_files WHERE oid=%s", oid)
    if row is None:
        raise HTTPException(404)
    row["stores"] = await db.fetch("SELECT * FROM upd_store_config WHERE oid=%s ORDER BY number", oid)
    return ok(row)


@app.put("/api/configfiles/{oid}")
async def configfile_edit(oid: int, body: dict):
    """Change setting values (by index in ConfigurationData) and publish a new generation."""
    row = await db.fetchrow("SELECT * FROM upd_config_files WHERE oid=%s", oid)
    if row is None:
        raise HTTPException(404)
    data = row["data"]
    for change in body.get("changes", []):
        data[int(change["index"])]["Value"] = change["value"]
    for add in body.get("add", []):
        data.append(add)
    gen = await db.fetchval("SELECT coalesce(max(generation), 0) + 1 FROM upd_config_files")
    await db.execute("UPDATE upd_config_files SET data=%s, generation=%s, updated_at=now() WHERE oid=%s",
                     db.jsonb(data), gen, oid)
    await db.record_event(None, "dashboard", "configfile", {"oid": oid, "generation": gen})
    return ok({"generation": gen})


# -------------------------------------------------------------------------------------- remote jobs

COMMANDS = {
    "Ping": (2, None),
    "TriggerReportFileSetVersions": (1, {"ExecutionTimeFrameMs": 0}),
    "TriggerGetConfigChanges": (2, {"ExecutionTimeFrameMs": 0}),
    "TriggerUpdateConfigStatus": (2, {"ExecutionTimeFrameMs": 0}),
    "RenewIoTCertificates": (1, None),
    "GetIotStatisticsSummary": (2, None),
    "SyncTimestampAndTimezone": (1, {"UtcTimestamp": "", "Timezone": "Eastern Standard Time"}),
    "ExecuteHttpRequest": (2, {"Url": "http://localhost:9001/api/pricing/all/refresh", "HttpMethod": "POST",
                               "JsonBody": ""}),
    "PeekFiles": (2, {"fileType": "UpdateClientServiceLogs"}),
    "GetDownloadFileJobStatus": (2, ""),
}


@app.get("/api/jobs/commands")
async def commands():
    return ok({k: {"version": v, "payload": p} for k, (v, p) in COMMANDS.items()})


@app.get("/api/jobs/clients")
async def clients():
    return ok(list((await online_clients()).values()))


@app.post("/api/jobs/iot")
async def iot_send(body: dict):
    payload = body.get("payload")
    if isinstance(payload, str) and payload.strip():
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            pass
    elif payload == "":
        payload = None
    if body.get("command") == "SyncTimestampAndTimezone" and isinstance(payload, dict) and not payload.get("UtcTimestamp"):
        payload["UtcTimestamp"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    res = await iot("/send", {"target": str(body.get("target") or "all"), "command": body["command"],
                              "version": int(body.get("version") or 1), "payload": payload,
                              "wait": float(body.get("wait") or 0)})
    await db.record_event(None, "dashboard", "iot-send", {"target": body.get("target"), "command": body["command"],
                                                          "requestId": res.get("requestId")})
    return ok(res)


@app.post("/api/jobs/peek")
async def peek(body: dict):
    return ok(await iot(f"/kiosk/{int(body['kiosk_id'])}/peek", body))


@app.post("/api/jobs/upload")
async def upload_logs(body: dict):
    return ok(await iot(f"/kiosk/{int(body['kiosk_id'])}/upload", body, timeout=120))


@app.post("/api/jobs/download")
async def download_job(target: str = Form("all"), name: str = Form(...), destination_path: str = Form(""),
                       file: Optional[UploadFile] = File(None), script: Optional[UploadFile] = File(None)):
    body = {"target": target, "name": name, "destinationPath": destination_path}
    if file is not None and file.filename:
        body.update(fileName=pathlib.PurePath(file.filename).name, fileB64=base64.b64encode(await file.read()).decode())
    if script is not None and script.filename:
        if not script.filename.lower().endswith(".ps1"):
            raise HTTPException(400, "the Update Client only runs .ps1 activation scripts")
        body.update(scriptName=pathlib.PurePath(script.filename).name,
                    scriptB64=base64.b64encode(await script.read()).decode())
    return ok(await iot("/download-job", body))


@app.post("/api/jobs/legacy-download")
async def legacy_download(store: str = Form(""), name: str = Form(...), destination_path: str = Form(...),
                          lua: str = Form(""), file: Optional[UploadFile] = File(None)):
    key = file_name = None
    if file is not None and file.filename:
        key = blobs.put_bytes(await file.read())
        file_name = pathlib.PurePath(file.filename).name
    row = await db.fetchrow("INSERT INTO upd_download_files (number, name, type, file_name, destination_path, blob,"
                            " activate_script) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                            store or None, name, 0 if key else 2, file_name, destination_path, key, lua or None)
    return ok({"id": row["id"], "note": "delivered on the store's next server poll (u pr)"})


@app.get("/api/jobs/requests")
async def requests_(limit: int = 50):
    return ok({"requests": await db.fetch("SELECT * FROM iot_requests ORDER BY created_at DESC LIMIT %s", limit),
               "download_jobs": await db.fetch(
                   "SELECT j.job_id, j.metadata, j.target, j.created_at, s.kiosk_id, s.status, s.updated_at"
                   " FROM iot_download_jobs j LEFT JOIN iot_download_job_status s ON s.job_id = j.job_id"
                   " ORDER BY j.created_at DESC LIMIT %s", limit),
               "legacy_downloads": await db.fetch(
                   "SELECT d.id, d.number, d.name, d.type, d.file_name, d.destination_path, d.created_at,"
                   " (SELECT json_agg(s.number) FROM upd_download_file_sent s WHERE s.download_id = d.id) AS sent_to"
                   " FROM upd_download_files d ORDER BY d.id DESC LIMIT %s", limit)})


@app.get("/api/uploads")
async def uploads():
    root = filesets.s3_root() / "uploads"
    files = []
    if root.is_dir():
        for p in sorted(root.rglob("*"), key=lambda x: x.stat().st_mtime, reverse=True)[:200]:
            if p.is_file():
                st = p.stat()
                files.append({"key": str(p.relative_to(filesets.s3_root())).replace("\\", "/"), "size": st.st_size,
                              "modified": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc)})
    return ok(files)


@app.get("/api/uploads/file")
async def upload_file(key: str):
    root = filesets.s3_root().resolve()
    p = (root / key).resolve()
    if not str(p).startswith(str(root / "uploads")) or not p.is_file():
        raise HTTPException(404)
    return FileResponse(p, filename=p.name)


# ------------------------------------------------------------------------------------------- events

@app.get("/api/events")
async def events(kiosk_id: Optional[int] = None, source: Optional[str] = None, kind: Optional[str] = None,
                 q: Optional[str] = None, before_id: Optional[int] = None, limit: int = 100):
    where, args = [], []
    for col, v in (("kiosk_id", kiosk_id), ("source", source), ("kind", kind)):
        if v not in (None, ""):
            where.append(f"{col} = %s")
            args.append(v)
    if before_id:
        where.append("id < %s")
        args.append(before_id)
    if q:
        where.append("payload::text ILIKE %s")
        args.append(f"%{q}%")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return ok(await db.fetch(f"SELECT * FROM events{clause} ORDER BY id DESC LIMIT %s", *args, min(limit, 500)))


@app.get("/api/events/kinds")
async def event_kinds():
    return ok(await db.fetch("SELECT source, kind, count(*) AS n, max(created_at) AS last FROM events"
                             " GROUP BY source, kind ORDER BY source, kind"))


# ------------------------------------------------------------------------------------------- static

app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")


def main(argv):
    ap = argparse.ArgumentParser(prog="dashboard")
    ap.add_argument("--port", type=int, default=int(os.environ.get("ADMIN_PORT") or 8443))
    ap.add_argument("--no-tls", action="store_true")
    args = ap.parse_args(argv)
    app.state.plain = args.no_tls
    kwargs = {} if args.no_tls else {"ssl_certfile": os.path.join(config.CERT_DIR, "server.crt"),
                                     "ssl_keyfile": os.path.join(config.CERT_DIR, "server.key")}
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level=config.LOG_LEVEL.lower(), access_log=False, **kwargs)
