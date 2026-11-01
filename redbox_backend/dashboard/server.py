"""Admin dashboard: `python -m redbox_backend dashboard [--port 8443] [--no-tls]`.

A single-page web UI (static/) over a JSON API on the same database the services use, so every edit is what
the kiosks get on their next call. Actions that have to reach a kiosk now (push configuration, deploy a file
set, remote jobs) go through rb-iot's operator API (IOT_ADMIN_URL). Every route under /api except /api/login
needs the admin session; changing requests also need the header `X-Requested-With: rb-admin`. Routes under
/api/ci/ (releases from CI, docs/releases.md) take an `Authorization: Bearer` API token instead.
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
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import apitokens, blobs, certs, config, db, filesets, kioskprofile
from ..iot import kioskconfig
from ..updatesvc import releases, repo
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


def _ci_scope(request):
    if request.method in ("GET", "HEAD"):
        return "read"
    return "publish" if request.url.path == "/api/ci/releases" else "deploy"


@app.middleware("http")
async def _guard(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/ci/"):
        # CI clients: a bearer token (apitokens.py) instead of the session cookie; not a browser, so no CSRF header.
        header = request.headers.get("authorization", "")
        token = header[7:].strip() if header[:7].lower() == "bearer " else ""
        row = await apitokens.check(token, _ci_scope(request))
        if row is None:
            await asyncio.sleep(0.5)
            return JSONResponse({"error": f"a token with scope '{_ci_scope(request)}' is required"}, status_code=401)
        request.state.actor = f"token:{row['name']}"
    elif path.startswith("/api/") and path not in ("/api/login", "/api/status"):
        session = auth.read_session(request.cookies.get(auth.COOKIE))
        who = session and await auth.resolve(session)
        if not who:
            return JSONResponse({"error": "login required"}, status_code=401)
        changing = request.method not in ("GET", "HEAD")
        if changing and request.headers.get("x-requested-with") != "rb-admin":
            return JSONResponse({"error": "missing X-Requested-With"}, status_code=403)
        denied = _role_denies(who["role"], request.method, path)
        if denied:
            return JSONResponse({"error": denied}, status_code=403)
        auth.current.set(who)
        response = await call_next(request)
        if changing and path not in ("/api/logout",):
            await db.record_event(None, "audit", f"{request.method} {path}",
                                  {"user": who["user"], "role": who["role"], "status": response.status_code})
        _secure_headers(response)
        return response
    response = await call_next(request)
    _secure_headers(response)
    return response


# Routes only an admin may change (accounts and API tokens); a viewer may change nothing but its own password.
ADMIN_ONLY = ("/api/users", "/api/releases/tokens")
SELF_SERVICE = ("/api/logout", "/api/me/password")


def _role_denies(role, method, path):
    if method in ("GET", "HEAD") or path in SELF_SERVICE or role == "admin":
        return None
    if role == "viewer":
        return "your account is read-only (viewer)"
    if any(path.startswith(p) for p in ADMIN_ONLY):
        return "only an admin can change accounts and API tokens"
    return None


def _secure_headers(response):
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
    role = await auth.login(body.get("username"), body.get("password"))
    if role is None:
        await asyncio.sleep(1.0)  # slow down guessing
        await db.record_event(None, "dashboard", "login-failed", {"username": body.get("username")})
        configured = auth.configured() or await db.fetchval("SELECT count(*) FROM dash_users WHERE enabled")
        return JSONResponse({"error": "wrong user name or password" if configured
                             else "no admin password is configured"}, status_code=401)
    await db.record_event(None, "dashboard", "login", {"username": body.get("username"), "role": role})
    resp = JSONResponse({"user": body["username"], "role": role})
    resp.set_cookie(auth.COOKIE, auth.make_session(body["username"]), httponly=True, samesite="strict",
                    secure=not app.state.plain, max_age=int(float(os.environ.get("ADMIN_SESSION_HOURS") or 8) * 3600))
    return resp


@app.post("/api/logout")
async def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(auth.COOKIE)
    return resp


@app.get("/api/me")
async def me():
    who = auth.current.get()
    return {"user": who["user"], "role": who["role"], "bootstrap": who["user"] == auth.username()}


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

KIOSK_SORTS = {"kiosk_id": "k.kiosk_id", "last_seen": "k.last_seen_at", "market": "lower(k.market)", "city": "lower(l.city)",
               "ucs_version": "k.ucs_version", "config": "c.reported_version", "discs": "discs", "status": "status_rank"}


@app.get("/api/kiosks")
async def kiosks(q: Optional[str] = None, status: Optional[str] = None, market: Optional[str] = None, sort: Optional[str] = None,
                 dir: str = "asc", limit: int = 100, offset: int = 0):
    """Kiosks, filtered, sorted and paged on the server. status: online (MQTT now or seen in the last 20 min), stale
    (not seen for 20 min to a day), offline (longer); the default order puts problems first."""
    clients = await online_clients()
    live = [int(c) for c in clients if str(c).isdigit()]
    recent = f"(k.kiosk_id = ANY(%s) OR k.last_seen_at > now() - interval '{ONLINE_MINUTES} minutes')"
    rank = (f"CASE WHEN {recent} THEN 2 WHEN k.last_seen_at > now() - interval '1 day' THEN 1 ELSE 0 END")
    where, args = [], []
    if q and q.strip():
        where.append("(k.kiosk_id::text LIKE %s OR k.market ILIKE %s OR l.city ILIKE %s OR l.label ILIKE %s OR k.ucs_version ILIKE %s)")
        args += [q.strip() + "%"] + [f"%{q.strip()}%"] * 4
    if status in ("online", "stale", "offline"):
        where.append(f"{rank} = %s")
        args += [live, {"online": 2, "stale": 1, "offline": 0}[status]]
    if market:
        where.append("k.market = %s"); args.append(market)
    base = (" FROM kiosks k LEFT JOIN iot_registrations r ON r.kiosk_id = k.kiosk_id"
            " LEFT JOIN upd_stores s ON s.number = k.kiosk_id::text LEFT JOIN cfg_kiosk_state c ON c.kiosk_id = k.kiosk_id"
            " LEFT JOIN cloud_kiosk_locations l ON l.kiosk_id = k.kiosk_id" + ((" WHERE " + " AND ".join(where)) if where else ""))
    order = KIOSK_SORTS.get(sort or "")
    order = f"{order} {'DESC' if dir == 'desc' else 'ASC'} NULLS LAST, k.kiosk_id" if order else "status_rank, k.kiosk_id"
    rows = await db.fetch(
        f"SELECT k.kiosk_id, k.market, k.banner, k.ucs_version, k.last_seen_at, k.info->'Versions' AS versions, {rank} AS status_rank,"
        " r.ucs_version AS iot_version, r.last_ping_at, r.broker_registered, s.last_check_in, c.reported_version AS config_version,"
        " c.activated_version, l.label, l.city, l.state AS location_state,"
        " (SELECT count(*) FROM cloud_inventory i WHERE i.kiosk_id = k.kiosk_id) AS discs" + base +
        f" ORDER BY {order} LIMIT %s OFFSET %s", live, *args, min(limit, 500), offset)
    for r in rows:
        r["mqtt_connected"] = str(r["kiosk_id"]) in clients
        r["status"] = {2: "online", 1: "stale", 0: "offline"}[r.pop("status_rank")]
    counts = await db.fetchrow(f"SELECT count(*) FILTER (WHERE {rank} = 2) AS online, count(*) FILTER (WHERE {rank} = 1) AS stale,"
                               f" count(*) FILTER (WHERE {rank} = 0) AS offline, count(*) AS total FROM kiosks k", live, live, live)
    return ok({"rows": rows, "total": await db.fetchval("SELECT count(*)" + base, *args) if where else counts["total"],
               "counts": counts, "markets": [r["market"] for r in await db.fetch(
                   "SELECT DISTINCT market FROM kiosks WHERE market IS NOT NULL AND market <> '' ORDER BY 1")]})

@app.get("/api/reprovision/{kiosk_id}.json")
async def reprovision_profile(kiosk_id: int, address: Optional[str] = None, market: Optional[str] = None,
                              base_domain: Optional[str] = None, rewrite: List[str] = Query(default=[])):
    """Settings file for software/src/Tools/Reprovision (`rb-reprovision apply`), as a download."""
    if not base_domain:
        address = address or config.BACKEND_PUBLIC_IP
    if market is None:
        market = await db.fetchval("SELECT market FROM kiosks WHERE kiosk_id=%s", kiosk_id)
    doc = kioskprofile.profile(kiosk_id, address=address, market=market or None, base_domain=base_domain or None,
                               rewrites=dict(r.split("=", 1) for r in rewrite if "=" in r))
    return Response(json.dumps(doc, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="kiosk-{kiosk_id}-reprovision.json"'})


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
    "customer_offers": ("cloud_customer_offers", ["offer_id"], "Customer offers (GetCustomerOffers)"),
    "rbplus_campaigns": ("cloud_rbplus_promo_campaigns", ["promo_campaign_id"], "Redbox+ promo campaigns"),
    "promo_redemptions": ("cloud_promo_redemptions", ["id"], "Promo code redemptions"),
    "return_promo_issued": ("cloud_return_promo_issued", ["transaction_id"], "Return-visit codes issued"),
    "points_ledger": ("cloud_points_ledger", ["id"], "Loyalty points ledger"),
    "customer_subscriptions": ("cloud_customer_subscriptions", ["id"], "Customer subscriptions"),
    "inventory_syncs": ("cloud_inventory_syncs", ["id"], "Inventory syncs"),
    "ad_impressions": ("cloud_ad_impressions", ["id"], "Ad impressions"),
    "assets": ("cloud_assets", ["asset_id"], "Media assets"),
    "planograms": ("cloud_planograms", ["id"], "Planograms"),
    "kiosk_sessions": ("cloud_kiosk_sessions", ["session_id"], "Kiosk sessions"),
    "bluefin_devices": ("cloud_bluefin_devices", ["kiosk_id", "reader_serial"], "Bluefin card readers"),
    "installer_kiosks": ("cloud_installer_kiosks", ["id"], "Kiosk installer: stores to install"),
    "staff_users": ("cloud_staff_users", ["username"], "Field-staff logins (kiosk)"),
    "ticket_events": ("ticket_events", ["event_id"], "Tickets: events"),
    "ticket_productions": ("ticket_productions", ["production_id"], "Tickets: productions"),
    "ticket_groups": ("ticket_groups", ["ticket_group_id"], "Tickets: ticket groups"),
    "ticket_group_prices": ("ticket_group_prices", ["ticket_group_id", "price_code_id"], "Tickets: group prices"),
    "ticket_price_codes": ("ticket_price_codes", ["production_id", "price_code_id"], "Tickets: price codes"),
    "ticket_delivery_methods": ("ticket_delivery_methods", ["delivery_method_id"], "Tickets: delivery methods"),
    "ticket_orders": ("ticket_orders", ["order_id"], "Tickets: orders"),
    "ticket_locks": ("ticket_locks", ["lock_guid"], "Tickets: seat locks"),
    "ticket_notifications": ("ticket_notifications", ["id"], "Tickets: notifications"),
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
                     offset: int = 0, sort: Optional[str] = None, dir: str = "asc"):
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
    order = (f"{sort} {'DESC' if dir == 'desc' else 'ASC'} NULLS LAST, " if sort in names else "") + ', '.join(key)
    rows = await db.fetch(f"SELECT {select} FROM {table}{clause} ORDER BY {order} LIMIT %s OFFSET %s",
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
        " , h.released, (SELECT count(*) FROM upd_rollouts o WHERE o.repo = r.name) AS rollouts"
        " FROM upd_repositories r LEFT JOIN LATERAL (SELECT * FROM upd_revisions v WHERE v.repo = r.name"
        " ORDER BY seq DESC LIMIT 1) h ON true ORDER BY r.name")
    for r in rows:
        r["served"] = await repo.served_head(r["name"])   # differs from head while a release is not rolled out
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
    rolled_out = {r["repo"] for r in await db.fetch("SELECT DISTINCT repo FROM upd_rollouts")}
    heads = {r: await repo.served_head(r) for r in repos_}
    out = []
    for s in await db.fetch("SELECT * FROM upd_stores ORDER BY number"):
        reported = {h["repo"]: h for h in await db.fetch("SELECT * FROM upd_store_heads WHERE number=%s", s["number"])}
        links = {l["repo"]: l["linked"] for l in await db.fetch("SELECT * FROM upd_store_repos WHERE number=%s", s["number"])}
        subscribed = set(await repo.repositories_for_store(s["number"]))
        cells = {}
        for r in repos_:
            h = reported.get(r)
            served = await repo.served_head(r, s["number"]) if r in rolled_out else heads[r]
            state = "current" if h and h["head"] == served else ("behind" if h else "missing")
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


def _exports_dir():
    p = pathlib.Path(config.DATA_DIR) / "exports"
    p.mkdir(parents=True, exist_ok=True)
    return p


@app.post("/api/packages/build")
async def packages_build(body: dict):
    """Build an installer seed (updatesvc/seedzip.py) into DATA_DIR/exports in the background.

    body: {"repos": [{"name": .., "revision": hash prefix | label | null}] or "all": true, "edition": name | null,
           "date": "MMDDYYYY" | null, "catalog": true to add the catalog, built now, as a dvd-data revision}
    """
    from ..updatesvc import seedzip
    edition = body.get("edition") or None
    if body.get("all"):
        q = ("SELECT DISTINCT repo FROM upd_edition_revisions WHERE edition=%s ORDER BY repo", edition) if edition \
            else ("SELECT DISTINCT repo FROM upd_revisions ORDER BY repo",)
        specs = [(r["repo"], None, edition) for r in await db.fetch(*q)]
    else:
        specs = [(r["name"], r.get("revision") or None, edition) for r in body.get("repos") or [] if r.get("name")]
    if not specs:
        raise HTTPException(400, "choose repositories (or all)")
    when = dt.datetime.strptime(body["date"], "%m%d%Y") if body.get("date") else dt.datetime.now()
    name = seedzip.default_name(when.date())
    out = _exports_dir() / name
    job = await db.fetchval("INSERT INTO upd_import_jobs (source, options) VALUES (%s,%s) RETURNING id",
                            f"build {name}", db.jsonb({"kind": "build", "specs": specs}))

    async def progress(msg):
        await db.execute("UPDATE upd_import_jobs SET progress = progress || %s WHERE id=%s",
                         db.jsonb([{"t": dt.datetime.now(dt.timezone.utc).isoformat(), "m": msg}]), job)

    async def run():
        work = None
        try:
            additions = []
            if body.get("catalog"):
                from ..catalog import publish as cpub, store as cstore
                work = cpub.work_dir()
                files, _ = await cpub.build_for_seed(work, progress=progress)
                additions.append((cstore.REPO, files, "catalog-" + dt.datetime.now().strftime("%Y%m%d-%H%M")))
            s = await seedzip.build(out, specs, additions, when=when, progress=progress)
            await progress(f"wrote {name}: {s['bytes'] // 2**20} MB, SHA-1 {s['sha1']}")
            await db.execute("UPDATE upd_import_jobs SET status='done', finished_at=now() WHERE id=%s", job)
        except Exception as e:
            log.exception("seed build failed")
            await progress(f"failed: {e}")
            await db.execute("UPDATE upd_import_jobs SET status='failed', finished_at=now() WHERE id=%s", job)
        finally:
            if work:
                shutil.rmtree(work, ignore_errors=True)
    task = asyncio.create_task(run())
    _import_tasks.add(task)
    task.add_done_callback(_import_tasks.discard)
    await db.record_event(None, "dashboard", "package-build", {"job": job, "file": name, "repositories": len(specs)})
    return ok({"job": job, "file": name})


@app.get("/api/packages/exports")
async def packages_exports():
    return ok([{"file": p.name, "size": p.stat().st_size,
                "modified": dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc)}
               for p in sorted(_exports_dir().glob("*.zip"))])


@app.get("/api/packages/exports/{name}")
async def packages_export_download(name: str):
    p = _exports_dir() / pathlib.PurePath(name).name
    if not p.is_file() or p.suffix != ".zip":
        raise HTTPException(404)
    return FileResponse(p, media_type="application/zip", filename=p.name)


@app.delete("/api/packages/exports/{name}")
async def packages_export_delete(name: str):
    p = _exports_dir() / pathlib.PurePath(name).name
    if p.is_file() and p.suffix == ".zip":
        p.unlink()
    return ok({"deleted": p.name})


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


# ----------------------------------------------------------------------- releases (dashboard session)
# The Releases page: the same operations as the admin CLI and the CI API (updatesvc/releases.py, docs/releases.md).

def _actor():
    who = auth.current.get()
    return "dashboard:" + (who["user"] if who else auth.username())


@app.get("/api/releases")
async def releases_overview():
    rows = await db.fetch(
        "SELECT r.name, n.hash AS newest, n.label AS newest_label, n.version AS newest_version, n.released AS newest_released,"
        " (SELECT count(*) FROM upd_revisions v WHERE v.repo = r.name AND NOT v.released) AS unreleased,"
        " (SELECT count(*) FROM upd_rollouts o WHERE o.repo = r.name) AS rollouts"
        " FROM upd_repositories r LEFT JOIN LATERAL (SELECT * FROM upd_revisions v WHERE v.repo = r.name"
        " ORDER BY seq DESC LIMIT 1) n ON true ORDER BY r.name")
    for r in rows:
        r["served"] = await repo.served_head(r["name"])
        r["served_label"] = await db.fetchval("SELECT label FROM upd_revisions WHERE repo=%s AND hash=%s", r["name"], r["served"])
    log_rows = await db.fetch("SELECT l.*, (SELECT label FROM upd_revisions v WHERE v.repo = l.repo AND v.hash = l.head) AS head_label"
                              " FROM upd_rollout_log l ORDER BY id DESC LIMIT 25")
    return ok({"repositories": rows, "log": log_rows})


@app.get("/api/releases/repo/{name}")
async def releases_repo(name: str, limit: int = 40):
    if not await db.fetchval("SELECT 1 FROM upd_repositories WHERE name=%s", name):
        raise HTTPException(404, "unknown repository")
    st = await releases.status(name, limit=limit)
    heads = {h for h in [st["default_head"]] + [o["head"] for o in st["rollouts"]] +
             [x for l in st["log"] for x in (l["head"], l["previous"])] +
             [x for s in st["stores"] for x in (s["reported"], s["served"])] if h}
    st["labels"] = {r["hash"]: {"seq": r["seq"], "label": r["label"], "version": r["version"], "released": r["released"]}
                    for r in await db.fetch("SELECT hash, seq, label, version, released FROM upd_revisions"
                                            " WHERE repo=%s AND hash = ANY(%s)", name, list(heads))}
    st["markets"] = [r["market"] for r in await db.fetch(
        "SELECT DISTINCT market FROM kiosks WHERE market IS NOT NULL AND market <> '' ORDER BY market")]
    return ok(st)


@app.post("/api/releases/upload")
async def releases_upload(file: UploadFile = File(...), allow_new: bool = Form(False)):
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        while chunk := await file.read(1 << 20):
            tmp.write(chunk)
    try:
        return ok(await releases.publish_release(tmp.name, allow_new=allow_new, actor=_actor()))
    finally:
        os.unlink(tmp.name)


@app.post("/api/releases/repo/{name}/rollout")
async def releases_rollout(name: str, body: dict):
    store, market = _ci_target(body)
    return ok(await releases.rollout(name, body.get("revision"), store=store, market=market, actor=_actor()))


@app.post("/api/releases/repo/{name}/rollback")
async def releases_rollback(name: str, body: dict):
    store, market = _ci_target(body)
    return ok(await releases.rollback(name, store=store, market=market, to=body.get("to") or None, actor=_actor()))


@app.get("/api/releases/tokens")
async def releases_tokens():
    return ok(await apitokens.list_all())


@app.post("/api/releases/tokens")
async def releases_token_create(body: dict):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "give the token a name")
    if await db.fetchval("SELECT 1 FROM api_tokens WHERE name=%s", name):
        raise HTTPException(409, f"a token named {name} exists")
    try:
        token = await apitokens.create(name, body.get("scopes") or ["publish"])
    except ValueError as e:
        raise HTTPException(400, str(e))
    await db.record_event(None, "dashboard", "api-token-create", {"name": name, "scopes": body.get("scopes")})
    return ok({"name": name, "token": token})


@app.post("/api/releases/tokens/revoke")
async def releases_token_revoke(body: dict):
    n = await apitokens.revoke(body.get("name") or "")
    await db.record_event(None, "dashboard", "api-token-revoke", {"name": body.get("name"), "revoked": n})
    return ok({"revoked": n})


# ------------------------------------------------------------------------------- CI releases (tokens)

def _ci_target(body):
    """{store} | {market} | {all: true}; everyone has to be asked for explicitly."""
    store, market = body.get("store"), body.get("market")
    if not store and not market and body.get("all") is not True:
        raise HTTPException(400, "give store, market, or all: true")
    return (str(store) if store else None), (market or None)


@app.exception_handler(releases.ReleaseError)
async def _release_error(request: Request, e: releases.ReleaseError):
    return JSONResponse({"error": str(e)}, status_code=400)


@app.post("/api/ci/releases")
async def ci_release(request: Request, file: UploadFile = File(...), allow_new: bool = Form(False)):
    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        while chunk := await file.read(1 << 20):
            tmp.write(chunk)
    try:
        return ok(await releases.publish_release(tmp.name, allow_new=allow_new, actor=request.state.actor))
    finally:
        os.unlink(tmp.name)


@app.get("/api/ci/repos/{name}/status")
async def ci_repo_status(name: str):
    return ok(await releases.status(name))


@app.post("/api/ci/repos/{name}/rollout")
async def ci_rollout(request: Request, name: str, body: dict):
    store, market = _ci_target(body)
    return ok(await releases.rollout(name, body.get("revision"), store=store, market=market, actor=request.state.actor))


@app.post("/api/ci/repos/{name}/rollback")
async def ci_rollback(request: Request, name: str, body: dict):
    store, market = _ci_target(body)
    return ok(await releases.rollback(name, store=store, market=market, to=body.get("to"), actor=request.state.actor))


@app.get("/api/ci/filesets/{fs_id}/status")
async def ci_fileset_status(fs_id: int):
    return ok(await releases.fileset_status(fs_id))


@app.post("/api/ci/filesets/{fs_id}/deploy")
async def ci_fileset_deploy(request: Request, fs_id: int, body: dict):
    """{revision (0 = remove), kiosk | market | all: true, channel ucs|legacy, window "01:00-05:00"}"""
    kiosk, market = _ci_target({"store": body.get("kiosk"), "market": body.get("market"), "all": body.get("all")})
    rev = int(body["revision"])
    if rev and not await db.fetchval("SELECT 1 FROM fs_revisions WHERE id=%s AND fileset_id=%s", rev, fs_id):
        raise HTTPException(404, f"file set {fs_id} has no revision {rev}")
    start, end = (body["window"].split("-", 1) if body.get("window") else (None, None))
    ttype, target = ("kiosk", kiosk) if kiosk else ("market", market) if market else ("all", None)
    dep = await filesets.deploy(fs_id, rev or None, ttype, target, body.get("channel", "ucs"),
                                activate_start=start, activate_end=end)
    pushed = None
    if dep["channel"] == "ucs":
        try:
            pushed = await iot("/push/deploy", {"deploymentId": dep["id"]})
        except HTTPException as e:
            pushed = {"error": e.detail}   # recorded; kiosks still get it on their next report
    await db.record_event(None, "releases", "fileset-deploy", {"deployment": dep["id"], "actor": request.state.actor})
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


# ------------------------------------------------------------------------------------------ catalog

def _catalog_error(e):
    from ..catalog import edit, inventory, lua
    if isinstance(e, (edit.EditError, inventory.InventoryError, lua.LuaError, ValueError)):
        return HTTPException(400, str(e))
    return e


async def _catalog_call(coro):
    try:
        return await coro
    except Exception as e:
        raise _catalog_error(e)


def _background(source, options, fn):
    """Run fn(progress) as a job in upd_import_jobs (shown with the package jobs). Returns the job id."""
    async def start():
        job = await db.fetchval("INSERT INTO upd_import_jobs (source, options) VALUES (%s,%s) RETURNING id",
                                source, db.jsonb(options))

        async def progress(msg):
            await db.execute("UPDATE upd_import_jobs SET progress = progress || %s WHERE id=%s",
                             db.jsonb([{"t": dt.datetime.now(dt.timezone.utc).isoformat(), "m": str(msg)}]), job)

        async def run():
            try:
                res = await fn(progress)
                await progress(json.dumps(res, default=str)[:2000])
                await db.execute("UPDATE upd_import_jobs SET status='done', finished_at=now() WHERE id=%s", job)
            except Exception as e:
                log.exception("%s failed", source)
                await progress(f"failed: {e}")
                await db.execute("UPDATE upd_import_jobs SET status='failed', finished_at=now() WHERE id=%s", job)
        task = asyncio.create_task(run())
        _import_tasks.add(task)
        task.add_done_callback(_import_tasks.discard)
        return job
    return start()


@app.get("/api/catalog")
async def catalog_status():
    from ..catalog import store
    return ok({
        "state": await db.fetchrow("SELECT source, imported_at, changed_at, stamped_at, last_build FROM catalog_state WHERE id=1"),
        "tables": await db.fetch("SELECT t.name, t.ord, CASE WHEN t.name = 'Cache' THEN (SELECT count(*) FROM catalog_images)"
                                 " ELSE (SELECT count(*) FROM catalog_rows r WHERE r.tbl = t.name) END AS rows"
                                 " FROM catalog_tables t ORDER BY t.ord"),
        "images": await db.fetchval("SELECT count(*) FROM catalog_images"),
        # an estimate for the ~26 million imported ones; exact for the few added here or learned from kiosks
        "barcodes": [{"source": "in all (estimate)", "n": int(await db.fetchval(
            "SELECT reltuples FROM pg_class WHERE oid = 'cloud_barcodes'::regclass") or 0)}] + await db.fetch(
            "SELECT source, count(*) AS n FROM cloud_barcodes WHERE source <> 'catalog' GROUP BY source ORDER BY source"),
        "repo": await db.fetchrow("SELECT r.name, r.serve_head, (SELECT hash FROM upd_revisions v WHERE v.repo = r.name"
                                  " ORDER BY seq DESC LIMIT 1) AS head FROM upd_repositories r WHERE r.name = %s", store.REPO),
        "markets": [r["market"] for r in await db.fetch("SELECT DISTINCT market FROM kiosks WHERE market IS NOT NULL"
                                                        " AND market <> '' ORDER BY market")],
        "jobs": await db.fetch("SELECT * FROM upd_import_jobs WHERE source LIKE 'catalog%%' ORDER BY id DESC LIMIT 10"),
        "omdb": bool(os.environ.get("OMDB_API_KEY"))})


@app.get("/api/catalog/lookups")
async def catalog_lookups():
    async def table(name):
        return [dict(r["value"] or {}, key=r["key"]) for r in
                await db.fetch("SELECT key, value FROM catalog_rows WHERE tbl=%s ORDER BY ord", name)]
    return ok({"types": await table("ProductType"), "ratings": await table("ProductRating"),
               "genres": await table("Genres"), "families": await table("ProductFamily"),
               "subplatforms": await table("SubPlatform")})


@app.get("/api/catalog/titles")
async def catalog_titles(q: Optional[str] = None, limit: int = 50, offset: int = 0):
    from ..catalog import edit
    return ok(await edit.search_titles(q, min(limit, 500), offset))


@app.get("/api/catalog/titles/{pid}")
async def catalog_title(pid: int):
    from ..catalog import edit
    row = await edit.get_row(edit.TITLES, pid)
    if row is None:
        raise HTTPException(404)
    return ok({"product_id": pid, "lua": row["lua"], "value": row["value"], "groups": await edit.groups_of(pid),
               "barcodes": await db.fetch("SELECT barcode, status, rental_count, source FROM cloud_barcodes"
                                          " WHERE product_id=%s ORDER BY barcode LIMIT 200", pid),
               "barcode_count": await db.fetchval("SELECT count(*) FROM cloud_barcodes WHERE product_id=%s", pid)})


@app.post("/api/catalog/titles")
async def catalog_title_save(body: dict):
    """body: {"product_id": id or null (new), "fields": {...}} or {"product_id": id, "lua": text}."""
    from ..catalog import edit, store
    pid = body.get("product_id")
    if body.get("lua"):
        if pid is None:
            raise HTTPException(400, "product_id is needed with lua")
        await _catalog_call(edit.put_row(edit.TITLES, int(pid), text=body["lua"]))
        await store.refresh_products()
    else:
        pid = await _catalog_call(edit.save_title(body.get("fields") or {}, pid))
    await db.record_event(None, "dashboard", "catalog-title", {"product_id": pid})
    return ok({"product_id": pid})


@app.post("/api/catalog/titles/{pid}/delete")
async def catalog_title_delete(pid: int, body: dict):
    from ..catalog import edit
    res = await _catalog_call(edit.delete_title(pid, barcodes=bool(body.get("barcodes"))))
    await db.record_event(None, "dashboard", "catalog-title-delete", res)
    return ok(res)


@app.post("/api/catalog/titles/{pid}/image")
async def catalog_title_image(pid: int, file: Optional[UploadFile] = File(None), url: str = Form("")):
    from ..catalog import edit
    data = await file.read() if file is not None and file.filename else \
        await _catalog_call(edit.fetch_image(url)) if url else None
    if not data:
        raise HTTPException(400, "give an image file or URL")
    return ok({"image_file": await _catalog_call(edit.put_image(data, pid))})


@app.get("/api/catalog/images/{name}")
async def catalog_image(name: str):
    from ..catalog import edit
    data = await edit.image_bytes(name)
    if data is None:
        raise HTTPException(404)
    kind = {".png": "image/png", ".gif": "image/gif"}.get(pathlib.PurePath(name).suffix.lower(), "image/jpeg")
    return Response(data, media_type=kind)


@app.post("/api/catalog/omdb")
async def catalog_omdb(body: dict):
    from ..catalog import edit
    return ok(await _catalog_call(edit.omdb_lookup(body.get("title"), body.get("year"), body.get("imdb_id"))))


@app.get("/api/catalog/groups")
async def catalog_groups(q: Optional[str] = None, product_id: Optional[int] = None, limit: int = 100, offset: int = 0,
                         sort: Optional[str] = None, dir: str = "desc", paged: bool = False):
    """Product groups; paged=true answers {rows, total} for the dashboard's paged table."""
    where, args = "", []
    if product_id:
        where, args = " AND value->'product_id_list' @> %s", [db.jsonb([product_id])]
    elif q:
        where, args = " AND (key = %s OR value->>'product_group_name' ILIKE %s)", [q.strip(), f"%{q}%"]
    by = {"name": "lower(value->>'product_group_name')", "type": "value->>'product_group_type_id'"}.get(sort or "", "key_num")
    rows = await db.fetch("SELECT key::bigint AS product_group_id, value FROM catalog_rows WHERE tbl='ProductGroup'" + where +
                          f" ORDER BY {by} {'ASC' if dir == 'asc' else 'DESC'} NULLS LAST, key_num DESC LIMIT %s OFFSET %s",
                          *args, min(limit, 500), offset)
    if not paged:
        return ok(rows)
    return ok({"rows": rows, "total": await db.fetchval("SELECT count(*) FROM catalog_rows WHERE tbl='ProductGroup'" + where, *args)})


@app.post("/api/catalog/groups")
async def catalog_group_save(body: dict):
    from ..catalog import edit
    gid = await _catalog_call(edit.save_group(body.get("product_group_id"), body.get("name"),
                                              body.get("type") or 1, body.get("product_ids") or []))
    await db.record_event(None, "dashboard", "catalog-group", {"product_group_id": gid})
    return ok({"product_group_id": gid})


@app.post("/api/catalog/groups/{gid}/delete")
async def catalog_group_delete(gid: int):
    from ..catalog import edit
    await _catalog_call(edit.delete_group(gid))
    return ok({"deleted": gid})


@app.get("/api/catalog/barcodes")
async def catalog_barcodes(q: Optional[str] = None, product_id: Optional[int] = None, limit: int = 100, offset: int = 0,
                           paged: bool = False):
    """Barcodes of a title, from a barcode on (in barcode order), or the ones added here / learned from kiosks.
    paged=true answers {rows, total} (the total of "from a barcode on" is the catalog's estimate: 26 million rows)."""
    if product_id:
        sql, args = " FROM cloud_barcodes WHERE product_id=%s", [product_id]
        order = " ORDER BY barcode"
    elif q:
        sql, args = " FROM cloud_barcodes WHERE barcode >= %s", [q.strip()]
        order = " ORDER BY barcode"
    else:
        sql, args = " FROM cloud_barcodes WHERE source <> 'catalog'", []
        order = " ORDER BY updated_at DESC"
    rows = await db.fetch("SELECT *" + sql + order + " LIMIT %s OFFSET %s", *args, min(limit, 1000), offset)
    if not paged:
        return ok(rows)
    if q and not product_id:
        total = offset + len(rows) + (1000 if len(rows) == min(limit, 1000) else 0)   # open-ended: "more" while pages are full
    else:
        total = await db.fetchval("SELECT count(*)" + sql, *args)
    return ok({"rows": rows, "total": total})


@app.post("/api/catalog/barcodes")
async def catalog_barcode_save(body: dict):
    from ..catalog import edit
    row = await _catalog_call(edit.set_barcode(body["barcode"], body["product_id"], body.get("status") or 0))
    await db.record_event(None, "dashboard", "catalog-barcode", {"barcode": body["barcode"], "product_id": body["product_id"]})
    return ok(row)


@app.post("/api/catalog/barcodes/delete")
async def catalog_barcode_delete(body: dict):
    from ..catalog import edit
    return ok({"deleted": await edit.delete_barcode(body["barcode"])})


@app.get("/api/catalog/rows/{table}")
async def catalog_rows(table: str, q: Optional[str] = None, limit: int = 100, offset: int = 0, sort: Optional[str] = None,
                       dir: str = "asc"):
    where, args = "", []
    if q:
        where, args = " AND (key = %s OR lua ILIKE %s)", [q, f"%{q}%"]
    base = " FROM catalog_rows WHERE tbl=%s" + where
    by = {"key": "key_num NULLS LAST, key", "ord": "ord"}.get(sort or "ord", "ord")
    order = ", ".join(f"{c.split()[0]} {'DESC' if dir == 'desc' else 'ASC'}" + (" NULLS LAST" if "NULLS" in c else "")
                      for c in by.split(", "))
    return ok({"total": await db.fetchval("SELECT count(*)" + base, table, *args),
               "rows": await db.fetch("SELECT key, lua" + base + f" ORDER BY {order} LIMIT %s OFFSET %s", table, *args,
                                      min(limit, 1000), offset)})


@app.put("/api/catalog/rows/{table}/{key}")
async def catalog_row_save(table: str, key: str, body: dict):
    from ..catalog import edit, store
    await _catalog_call(edit.put_row(table, key, text=str(body.get("lua") or "").strip()))
    if table in ("ProductCatalog", "ProductGroup", "ProductType"):
        await store.refresh_products()
    await db.record_event(None, "dashboard", "catalog-row", {"table": table, "key": key})
    return ok({"saved": key})


@app.post("/api/catalog/rows/{table}/{key}/delete")
async def catalog_row_delete(table: str, key: str):
    from ..catalog import edit
    return ok({"deleted": await edit.delete_row(table, key)})


@app.post("/api/catalog/import")
async def catalog_import(body: dict):
    """body: {"folder": path inside the container} or {"revision": dvd-data hash/label or null (served head)}."""
    from ..catalog import store
    folder = (body.get("folder") or "").strip()
    if folder:
        if folder.lower().endswith(".zip") or pathlib.Path(folder).is_file():
            # a seed (data-<date>.zip) holds dvd-data as Update Service revisions, not as the two files
            raise HTTPException(400, f"{folder} is a file, not a folder. To use a seed's catalog, import the seed on Data zips"
                                     " (or rb.ps1 import-packages) and leave this field empty: the catalog is then imported"
                                     " from the dvd-data that seed brought in (or name a dvd-data revision instead)")
        if not (pathlib.Path(folder) / store.PROFILE).is_file():
            raise HTTPException(400, f"no profile.data in {folder} (a folder inside the rb-admin container)")
        job = await _background(f"catalog import {folder}", body, lambda p: store.import_folder(folder, p))
    else:
        rev = body.get("revision") or None
        job = await _background(f"catalog import {store.REPO}@{rev or 'served head'}", body,
                                lambda p: store.import_repository(store.REPO, rev, p))
    await db.record_event(None, "dashboard", "catalog-import", {"job": job, **body})
    return ok({"job": job})


@app.post("/api/catalog/publish")
async def catalog_publish(body: dict):
    """body: {"scope": "all" | "market" | "store", "target": market or store number, "label": ...}."""
    from ..catalog import publish as cpub
    scope, target = body.get("scope") or "all", (str(body.get("target") or "").strip() or None)
    if scope not in ("all", "market", "store") or (scope != "all" and not target):
        raise HTTPException(400, "scope all, or market / store with a target")
    job = await _background(f"catalog publish {scope} {target or ''}".strip(), body,
                            lambda p: cpub.publish(scope, target, body.get("label") or None, progress=p,
                                                   actor=f"dashboard:{auth.username()}"))
    await db.record_event(None, "dashboard", "catalog-publish", {"job": job, **body})
    return ok({"job": job})


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
                 q: Optional[str] = None, before_id: Optional[int] = None, limit: int = 100, offset: int = 0,
                 sort: Optional[str] = None, dir: str = "desc", paged: bool = False, since: Optional[str] = None,
                 until: Optional[str] = None):
    where, args = [], []
    for col, v in (("kiosk_id", kiosk_id), ("source", source), ("kind", kind)):
        if v not in (None, ""):
            where.append(f"{col} = %s")
            args.append(v)
    if before_id:
        where.append("id < %s")
        args.append(before_id)
    if q:
        where.append("(payload::text ILIKE %s OR kind ILIKE %s)")
        args += [f"%{q}%", f"%{q}%"]
    if since:
        where.append("created_at >= %s::timestamptz"); args.append(since)
    if until:
        where.append("created_at < %s::timestamptz"); args.append(until)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    col = {"id": "id", "created_at": "id", "kiosk_id": "kiosk_id", "source": "source", "kind": "kind"}.get(sort or "id", "id")
    rows = await db.fetch(f"SELECT * FROM events{clause} ORDER BY {col} {'ASC' if dir == 'asc' else 'DESC'} NULLS LAST, id DESC"
                          " LIMIT %s OFFSET %s", *args, min(limit, 500), offset)
    if not paged:
        return ok(rows)
    # counted up to 100,000 so a filter over a long history stays quick
    total = await db.fetchval(f"SELECT count(*) FROM (SELECT 1 FROM events{clause} LIMIT 100001) x", *args)
    return ok({"rows": rows, "total": total, "capped": total > 100000})


@app.get("/api/events/kinds")
async def event_kinds():
    return ok(await db.fetch("SELECT source, kind, count(*) AS n, max(created_at) AS last FROM events"
                             " GROUP BY source, kind ORDER BY source, kind"))


# ------------------------------------------------------------------------------------------- static

from . import control, geocode  # noqa: E402  (control-center routes; must be added before the static mount)

app.include_router(control.router)
app.include_router(geocode.router)


class SinglePageApp(StaticFiles):
    """The React app (dashboard-ui, built into static/): files as they are; any other path that is not under /api gets
    index.html, so a route like /stores/banners?q=... can be reloaded or linked."""

    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code != 404 or path.startswith("api/") or "." in path.rsplit("/", 1)[-1]:
                raise
            return await super().get_response("index.html", scope)


if not (STATIC / "index.html").exists():
    log.warning("the dashboard frontend is not built (%s): cd dashboard-ui && npm ci && npm run build", STATIC)
    STATIC.mkdir(parents=True, exist_ok=True)
app.mount("/", SinglePageApp(directory=STATIC, html=True), name="static")


def main(argv):
    ap = argparse.ArgumentParser(prog="dashboard")
    ap.add_argument("--port", type=int, default=int(os.environ.get("ADMIN_PORT") or 8443))
    ap.add_argument("--no-tls", action="store_true",
                    default=(os.environ.get("ADMIN_TLS") or "true").strip().lower() == "false")
    args = ap.parse_args(argv)
    app.state.plain = args.no_tls
    kwargs = {} if args.no_tls else {"ssl_certfile": os.path.join(config.CERT_DIR, "server.crt"),
                                     "ssl_keyfile": os.path.join(config.CERT_DIR, "server.key")}
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level=config.LOG_LEVEL.lower(), access_log=False, **kwargs)
