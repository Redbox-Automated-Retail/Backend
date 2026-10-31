"""Control-center API of the dashboard: system health, fleet summary and alerts, search, kiosk edits and bulk
commands, dashboard accounts, field-staff logins and provisioning. Mounted by server.py; same session and role
rules (server._guard)."""
import asyncio
import datetime as dt
import hashlib
import json
import os
import pathlib
import platform
import shutil
import sys
import time
import urllib.error
import urllib.request
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from .. import certs, config, db, kioskprofile
from . import auth
from . import server as srv

router = APIRouter()
STARTED = time.time()
ONLINE_MINUTES = srv.ONLINE_MINUTES

# Health endpoints of the other services, as seen from rb-admin on the compose / WSLC network.
HEALTH_URLS = {
    "rb-cloudapi": os.environ.get("CLOUDAPI_HEALTH_URL", "http://rb-cloudapi:80/_health"),
    "rb-updatesvc": os.environ.get("UPDATESVC_HEALTH_URL", "http://rb-updatesvc:7080/_health"),
    "rb-iot": srv.IOT_ADMIN_URL + "/_health",
    "rb-remoting": srv.REMOTING_CONTROL_URL + "/health",
}
SERVICE_ROLES = {
    "rb-db": "PostgreSQL: every service's state",
    "rb-cloudapi": "Kiosk cloud APIs on 443 / 80 (transactions, inventory, data, ads, tickets, files, CRL)",
    "rb-updatesvc": "Update Service for updatemgrd (srcp 7002, BITS content 7080)",
    "rb-iot": "MQTT broker for the Update Client (8883) and its operator API",
    "rb-remoting": ".NET Remoting on Mono: reservation broker (7890) and Kiosk Services MessageService",
    "rb-admin": "This dashboard",
}


def _who():
    return (auth.current.get() or {}).get("user") or auth.username()


# ------------------------------------------------------------------------------------------- system

def _probe(url, timeout=3):
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=timeout) as r:
            body = r.read(2048)
            try:
                detail = json.loads(body or b"null")
            except ValueError:
                detail = body.decode(errors="replace")[:200]
            return {"ok": 200 <= r.status < 300, "status": r.status, "ms": round((time.monotonic() - t0) * 1000),
                    "detail": detail}
    except urllib.error.HTTPError as e:
        return {"ok": False, "status": e.code, "ms": round((time.monotonic() - t0) * 1000), "error": str(e)}
    except Exception as e:
        return {"ok": False, "status": None, "ms": round((time.monotonic() - t0) * 1000), "error": str(e)}


def _cert_info(path):
    from cryptography import x509
    p = pathlib.Path(path)
    if not p.exists():
        return None
    try:
        cert = x509.load_pem_x509_certificate(p.read_bytes())
    except Exception as e:
        return {"file": p.name, "error": str(e)}
    try:
        sans = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        names = [str(v) for v in sans.get_values_for_type(x509.DNSName)] + \
            [str(v) for v in sans.get_values_for_type(x509.IPAddress)]
    except x509.ExtensionNotFound:
        names = []
    not_after = cert.not_valid_after_utc if hasattr(cert, "not_valid_after_utc") else \
        cert.not_valid_after.replace(tzinfo=dt.timezone.utc)
    return {"file": p.name, "subject": cert.subject.rfc4514_string(), "issuer": cert.issuer.rfc4514_string(),
            "not_after": not_after, "days_left": (not_after - dt.datetime.now(dt.timezone.utc)).days, "names": names}


def _flag(name):
    return (os.environ.get(name) or "").strip()


@router.get("/api/system")
async def system():
    probes = await asyncio.gather(*[asyncio.to_thread(_probe, u) for u in HEALTH_URLS.values()])
    services = [{"name": "rb-admin", "role": SERVICE_ROLES["rb-admin"], "ok": True, "ms": 0,
                 "detail": {"uptime_s": round(time.time() - STARTED)}}]
    t0 = time.monotonic()
    try:
        dbinfo = await db.fetchrow(
            "SELECT version() AS version, pg_database_size(current_database()) AS size,"
            " extract(epoch FROM now() - pg_postmaster_start_time())::bigint AS uptime_s,"
            " (SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()) AS connections")
        services.insert(0, {"name": "rb-db", "role": SERVICE_ROLES["rb-db"], "ok": True,
                            "ms": round((time.monotonic() - t0) * 1000), "detail": dbinfo})
    except Exception as e:
        services.insert(0, {"name": "rb-db", "role": SERVICE_ROLES["rb-db"], "ok": False, "error": str(e)})
    for (name, url), p in zip(HEALTH_URLS.items(), probes):
        services.append(dict(p, name=name, role=SERVICE_ROLES[name], url=url))
    tables = await db.fetch(
        "SELECT relname AS table, n_live_tup AS rows, pg_total_relation_size(relid) AS bytes"
        " FROM pg_stat_user_tables ORDER BY pg_total_relation_size(relid) DESC LIMIT 15")
    try:
        du = shutil.disk_usage(config.DATA_DIR)
        disk = {"path": config.DATA_DIR, "total": du.total, "used": du.used, "free": du.free}
    except OSError:
        disk = None
    keys = [k for k in _flag("CLOUD_API_KEYS").split(",") if k.strip()]
    settings = {
        "Payment mode": config.PAYMENT_MODE,
        "MQTT client certificate required": config.MQTT_REQUIRE_CLIENT_CERT,
        "MQTT plain port": config.MQTT_PLAIN_PORT or "off",
        "Cloud API keys enforced": _flag("CLOUD_API_ENFORCE_KEYS").lower() == "true",
        "Cloud API keys configured": len(keys),
        "Public base URL (cloud APIs)": _flag("CLOUD_PUBLIC_BASE_URL") or "(from the request)",
        "S3 base URL": _flag("S3_BASE_URL") or "(default)",
        "Update content base URL": config.UPDATE_CONTENT_BASE_URL,
        "CRL URL": config.CRL_URL,
        "Backend public IP (installer, hosts files)": config.BACKEND_PUBLIC_IP,
        "Extra host names": ", ".join(config.EXTRA_HOSTNAMES) or "-",
        "Update Service credential check": bool(_flag("UPDATE_SERVICE_KEY1")),
        "Update Service plain port": config.UPDATE_SERVICE_PLAIN_PORT or "off",
        "Update-store seeds folder": srv.PACKAGES_DIR or "(not mounted)",
        "OMDb lookups": bool(_flag("OMDB_API_KEY")),
        "Dashboard TLS": not getattr(srv.app.state, "plain", False),
        "Dashboard session hours": _flag("ADMIN_SESSION_HOURS") or "8",
        "Log level": config.LOG_LEVEL,
    }
    return srv.ok({
        "services": services, "tables": tables, "disk": disk, "settings": settings,
        "certificates": [c for c in (_cert_info(pathlib.Path(config.CERT_DIR) / n) for n in ("ca.crt", "server.crt")) if c],
        "runtime": {"python": sys.version.split()[0], "platform": platform.platform(), "pid": os.getpid(),
                    "uptime_s": round(time.time() - STARTED)},
    })


# -------------------------------------------------------------------------------------------- fleet

@router.get("/api/fleet")
async def fleet():
    """The overview's tiles: kiosks by status, rentals, discs, reservations. No money: the kiosks take no real payments."""
    clients = await srv.online_clients()
    kiosks = await db.fetch("SELECT kiosk_id, last_seen_at FROM kiosks")
    now = dt.datetime.now(dt.timezone.utc)
    status = {"online": 0, "recent": 0, "stale": 0, "offline": 0}
    for k in kiosks:
        age = (now - k["last_seen_at"]).total_seconds() if k["last_seen_at"] else 1e12
        status["online" if str(k["kiosk_id"]) in clients else "recent" if age < ONLINE_MINUTES * 60
               else "stale" if age < 86400 else "offline"] += 1
    # rentals per hour (line items: a disc rented or bought)
    hourly = await db.fetch(
        "SELECT h AS hour, count(r.id) AS n FROM generate_series(date_trunc('hour', now()) - interval '23 hours',"
        " date_trunc('hour', now()), interval '1 hour') h LEFT JOIN cloud_rentals r ON r.rented_at >= h"
        " AND r.rented_at < h + interval '1 hour' GROUP BY h ORDER BY h")
    counts = await db.fetchrow(
        "SELECT (SELECT count(*) FROM cloud_rentals WHERE rented_at >= date_trunc('day', now())) AS rentals_today,"
        " (SELECT count(*) FROM cloud_rentals WHERE status = 'out') AS rentals_out,"
        " (SELECT count(*) FROM cloud_inventory) AS discs,"
        " (SELECT count(*) FROM cloud_reservations WHERE status = 'reserved' AND (expires_at IS NULL OR expires_at > now()))"
        "   AS reservations_open,"
        " (SELECT count(*) FROM cloud_returns WHERE created_at > now() - interval '24 hours') AS returns_24h,"
        " (SELECT count(*) FROM broker_kiosks) AS broker_kiosks")
    return srv.ok({"status": dict(status, total=len(kiosks), mqtt=len(clients)), "hourly": hourly, "counts": counts})

# ------------------------------------------------------------------------------------------- search

@router.get("/api/search")
async def search(q: str):
    q = q.strip()
    if not q:
        return srv.ok({})
    like = f"%{q}%"
    digits = q.isdigit()
    out = {
        "kiosks": await db.fetch(
            "SELECT k.kiosk_id, k.market, l.city, l.state, l.label FROM kiosks k LEFT JOIN cloud_kiosk_locations l"
            " ON l.kiosk_id = k.kiosk_id WHERE k.kiosk_id::text LIKE %s OR l.city ILIKE %s OR l.label ILIKE %s"
            " OR k.market ILIKE %s ORDER BY k.kiosk_id LIMIT 8", q + "%", like, like, like),
        "customers": await db.fetch(
            "SELECT customer_id, cpn, email, first_name, last_name FROM cloud_customers WHERE email ILIKE %s OR cpn = %s"
            " OR phone = %s OR (first_name || ' ' || coalesce(last_name, '')) ILIKE %s ORDER BY customer_id LIMIT 8",
            like, q, q, like),
        "reservations": await db.fetch(
            "SELECT reference_number, kiosk_id, status, created_at FROM cloud_reservations"
            " WHERE reference_number::text = %s LIMIT 5", q) if digits else [],
        "titles": [],
    }
    try:
        from ..catalog import edit
        out["titles"] = (await edit.search_titles(q, 8, 0))["rows"]
    except Exception:
        pass
    return srv.ok(out)


# ------------------------------------------------------------------------------------------- kiosks

KIOSK_FIELDS = ("market", "banner", "timezone", "state")
LOCATION_FIELDS = ("label", "location_name", "location_type", "address", "city", "state", "zip", "latitude",
                   "longitude", "dual_kiosk_id", "ops_market")


@router.put("/api/kiosks/{kiosk_id}")
async def kiosk_update(kiosk_id: int, body: dict):
    if not await db.fetchval("SELECT 1 FROM kiosks WHERE kiosk_id=%s", kiosk_id):
        raise HTTPException(404)
    k = {f: (body.get("kiosk") or {})[f] or None for f in KIOSK_FIELDS if f in (body.get("kiosk") or {})}
    if k:
        await db.execute(f"UPDATE kiosks SET {', '.join(f'{f} = %s' for f in k)} WHERE kiosk_id = %s",
                         *k.values(), kiosk_id)
    loc = body.get("location") or {}
    loc = {f: (loc[f] if loc[f] not in ("",) else None) for f in LOCATION_FIELDS if f in loc}
    if loc:
        if "location_type" in loc:
            loc["location_type"] = int(loc["location_type"] or 1)
        for f in ("latitude", "longitude"):
            if loc.get(f) is not None:
                loc[f] = float(loc[f])
        if loc.get("dual_kiosk_id") is not None:
            loc["dual_kiosk_id"] = int(loc["dual_kiosk_id"])
        cols = list(loc)
        await db.execute(f"INSERT INTO cloud_kiosk_locations (kiosk_id, {', '.join(cols)}) VALUES (%s, "
                         + ", ".join(["%s"] * len(cols)) + ") ON CONFLICT (kiosk_id) DO UPDATE SET "
                         + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols), kiosk_id, *loc.values())
    await db.record_event(kiosk_id, "dashboard", "kiosk-edit", {"kiosk": list(k), "location": list(loc), "by": _who()})
    return srv.ok({"updated": True})


@router.get("/api/kiosks/{kiosk_id}/activity")
async def kiosk_activity(kiosk_id: int):
    """What the kiosk page shows beyond /api/kiosks/{id}: commands, reservations, stock, certificates, uploads."""
    root = srv.filesets.s3_root() / "uploads" / str(kiosk_id)
    uploads = []
    if root.is_dir():
        for p in sorted(root.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True)[:30]:
            st = p.stat()
            uploads.append({"key": f"uploads/{kiosk_id}/{p.name}", "size": st.st_size,
                            "modified": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc)})
    return srv.ok({
        "requests": await db.fetch("SELECT request_id, command, version, payload, response, created_at, responded_at"
                                   " FROM iot_requests WHERE kiosk_id=%s ORDER BY created_at DESC LIMIT 30", kiosk_id),
        "reservations": await db.fetch("SELECT reference_number, status, reason_code, items, expires_at, created_at"
                                       " FROM cloud_reservations WHERE kiosk_id=%s ORDER BY created_at DESC LIMIT 20",
                                       kiosk_id),
        "inventory": await db.fetchrow(
            "SELECT count(*) AS discs, count(DISTINCT b.product_id) AS titles,"
            " (SELECT max(created_at) FROM cloud_inventory_syncs s WHERE s.kiosk_id = %s) AS last_sync"
            " FROM cloud_inventory i LEFT JOIN cloud_barcodes b ON b.barcode = i.barcode WHERE i.kiosk_id = %s",
            kiosk_id, kiosk_id),
        "out": await db.fetchval("SELECT count(*) FROM cloud_rentals WHERE kiosk_id=%s AND status='out'", kiosk_id),
        "certificates": await db.fetch("SELECT certificate_id, market, created_at, revoked_at FROM iot_certs"
                                       " WHERE kiosk_id=%s ORDER BY created_at DESC", kiosk_id),
        "broker": await db.fetchrow("SELECT * FROM broker_kiosks WHERE kiosk_id=%s", kiosk_id),
        "daily": await db.fetch(
            "SELECT d AS day, count(r.id) AS n FROM generate_series(date_trunc('day', now()) - interval '13 days',"
            " date_trunc('day', now()), interval '1 day') d LEFT JOIN cloud_rentals r ON r.kiosk_id = %s"
            " AND r.rented_at >= d AND r.rented_at < d + interval '1 day' GROUP BY d ORDER BY d", kiosk_id),
        "uploads": uploads,
    })


@router.post("/api/kiosks/command")
async def kiosk_bulk_command(body: dict):
    """One IoT command to several kiosks: {"kiosks": [ids] | "target": "all" / "market:X", "command", "version", "payload"}."""
    targets = [str(k) for k in body.get("kiosks") or []] or [str(body.get("target") or "")]
    if not targets or not targets[0]:
        raise HTTPException(400, "choose kiosks")
    payload = body.get("payload")
    if isinstance(payload, str):
        payload = json.loads(payload) if payload.strip() else None
    if body.get("command") == "SyncTimestampAndTimezone" and isinstance(payload, dict) and not payload.get("UtcTimestamp"):
        payload["UtcTimestamp"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    async def one(t):
        try:
            r = await srv.iot("/send", {"target": t, "command": body["command"], "version": int(body.get("version") or 1),
                                        "payload": payload, "wait": 0}, timeout=20)
            return {"target": t, "delivered": r.get("delivered"), "requestId": r.get("requestId")}
        except HTTPException as e:
            return {"target": t, "error": e.detail}
    results = await asyncio.gather(*[one(t) for t in targets[:500]])
    await db.record_event(None, "dashboard", "iot-bulk", {"command": body["command"], "targets": targets[:50],
                                                          "count": len(targets), "by": _who()})
    return srv.ok({"results": results, "delivered": sum(r.get("delivered") or 0 for r in results)})


# -------------------------------------------------------------------------------------------- users

def _check_password(p):
    if len(p or "") < auth.MIN_PASSWORD:
        raise HTTPException(400, f"use at least {auth.MIN_PASSWORD} characters")


@router.get("/api/users")
async def users():
    rows = await db.fetch("SELECT username, display_name, role, enabled, created_at, created_by, password_changed_at,"
                          " last_login_at FROM dash_users ORDER BY username")
    last = await db.fetchval("SELECT max(created_at) FROM events WHERE source='dashboard' AND kind='login'"
                             " AND payload->>'username' = %s", auth.username())
    return srv.ok({"bootstrap": {"username": auth.username(), "role": "admin", "configured": auth.configured(),
                                 "last_login_at": last},
                   "users": rows, "roles": list(auth.ROLES)})


@router.post("/api/users")
async def user_create(body: dict):
    name = (body.get("username") or "").strip()
    if not name or name == auth.username() or len(name) > 64 or any(c.isspace() for c in name):
        raise HTTPException(400, "choose another user name")
    if body.get("role") not in auth.ROLES:
        raise HTTPException(400, "unknown role")
    _check_password(body.get("password"))
    if await db.fetchval("SELECT 1 FROM dash_users WHERE username=%s", name):
        raise HTTPException(409, "that user exists")
    await db.execute("INSERT INTO dash_users (username, display_name, password_hash, role, created_by)"
                     " VALUES (%s,%s,%s,%s,%s)", name, body.get("display_name") or None,
                     auth.hash_password(body["password"]), body["role"], _who())
    return srv.ok({"created": name})


@router.put("/api/users/{name}")
async def user_update(name: str, body: dict):
    row = await db.fetchrow("SELECT * FROM dash_users WHERE username=%s", name)
    if row is None:
        raise HTTPException(404)
    sets, args = [], []
    if "role" in body:
        if body["role"] not in auth.ROLES:
            raise HTTPException(400, "unknown role")
        sets.append("role=%s"); args.append(body["role"])
    if "enabled" in body:
        sets.append("enabled=%s"); args.append(bool(body["enabled"]))
    if "display_name" in body:
        sets.append("display_name=%s"); args.append(body["display_name"] or None)
    if body.get("password"):
        _check_password(body["password"])
        sets += ["password_hash=%s", "password_changed_at=now()"]; args.append(auth.hash_password(body["password"]))
    if not sets:
        raise HTTPException(400, "nothing to change")
    await db.execute(f"UPDATE dash_users SET {', '.join(sets)} WHERE username=%s", *args, name)
    return srv.ok({"updated": name})


@router.delete("/api/users/{name}")
async def user_delete(name: str):
    if name == _who():
        raise HTTPException(400, "you cannot delete your own account")
    return srv.ok({"deleted": await db.execute("DELETE FROM dash_users WHERE username=%s", name)})


@router.post("/api/me/password")
async def my_password(body: dict):
    who = auth.current.get()
    if who["user"] == auth.username():
        raise HTTPException(400, "the bootstrap admin's password lives in .env: run scripts/set-admin-password.sh "
                                 "(or rb.ps1 set-admin-password)")
    row = await db.fetchrow("SELECT password_hash FROM dash_users WHERE username=%s", who["user"])
    if not row or not auth.verify_password(body.get("current") or "", row["password_hash"]):
        raise HTTPException(400, "the current password is wrong")
    _check_password(body.get("password"))
    await db.execute("UPDATE dash_users SET password_hash=%s, password_changed_at=now() WHERE username=%s",
                     auth.hash_password(body["password"]), who["user"])
    resp = srv.ok({"changed": True})
    resp.set_cookie(auth.COOKIE, auth.make_session(who["user"]), httponly=True, samesite="strict",
                    secure=not srv.app.state.plain,
                    max_age=int(float(os.environ.get("ADMIN_SESSION_HOURS") or 8) * 3600))
    return resp


@router.get("/api/access")
async def access():
    """Everything else that authenticates: field-staff logins, Cloud API keys, IoT certificates, CI tokens."""
    keys = [k.strip() for k in _flag("CLOUD_API_KEYS").split(",") if k.strip()]
    return srv.ok({
        "staff": await db.fetch("SELECT username, active FROM cloud_staff_users ORDER BY username"),
        "cloud_api": {"enforced": _flag("CLOUD_API_ENFORCE_KEYS").lower() == "true",
                      "keys": [k[:4] + "..." + k[-2:] if len(k) > 8 else "****" for k in keys]},
        "iot_certs": await db.fetchrow("SELECT count(*) FILTER (WHERE revoked_at IS NULL) AS active,"
                                       " count(*) FILTER (WHERE revoked_at IS NOT NULL) AS revoked FROM iot_certs"),
        "mqtt_client_cert": config.MQTT_REQUIRE_CLIENT_CERT,
        "update_service_check": bool(_flag("UPDATE_SERVICE_KEY1")),
    })


@router.post("/api/staff")
async def staff_set(body: dict):
    """Create or change a field-staff login (POST api/kiosk/authenticate checks SHA-256 of the password)."""
    name = (body.get("username") or "").strip()
    if not name:
        raise HTTPException(400, "user name required")
    if body.get("password"):
        await db.execute("INSERT INTO cloud_staff_users (username, password_sha256, active) VALUES (%s,%s,%s)"
                         " ON CONFLICT (username) DO UPDATE SET password_sha256 = EXCLUDED.password_sha256,"
                         " active = EXCLUDED.active", name, hashlib.sha256(body["password"].encode()).hexdigest(),
                         bool(body.get("active", True)))
    elif not await db.execute("UPDATE cloud_staff_users SET active=%s WHERE username=%s", bool(body.get("active")), name):
        raise HTTPException(400, "a new staff login needs a password")
    return srv.ok({"saved": name})


@router.post("/api/staff/delete")
async def staff_delete(body: dict):
    return srv.ok({"deleted": await db.execute("DELETE FROM cloud_staff_users WHERE username=%s", body.get("username"))})


# ------------------------------------------------------------------------------------- provisioning

@router.get("/api/provisioning")
async def provisioning():
    return srv.ok({
        "backend_ip": config.BACKEND_PUBLIC_IP,
        "host_entries": [h for h in config.INSTALLER_HOST_ENTRIES.split("|") if h.strip()],
        "service_hosts": kioskprofile.SERVICE_HOSTS if hasattr(kioskprofile, "SERVICE_HOSTS") else {},
        "installer_kiosks": await db.fetch("SELECT * FROM cloud_installer_kiosks ORDER BY pending DESC, id"),
        "installer_sessions": await db.fetch("SELECT guid, hash, version, jsonb_array_length(log) AS log_lines, log,"
                                             " created_at FROM upd_installer_sessions ORDER BY created_at DESC LIMIT 30"),
        "certificates": await db.fetch("SELECT certificate_id, kiosk_id, market, created_at, revoked_at FROM iot_certs"
                                       " ORDER BY created_at DESC LIMIT 200"),
        "registrations": await db.fetch("SELECT kiosk_id, ucs_version, assembly_version, broker_registered, broker_reason,"
                                        " registered_at, last_ping_at FROM iot_registrations ORDER BY kiosk_id"),
        "markets": [r["market"] for r in await db.fetch("SELECT DISTINCT market FROM kiosks WHERE market IS NOT NULL"
                                                        " AND market <> '' ORDER BY market")],
    })


@router.post("/api/provisioning/certificates/revoke")
async def certificate_revoke(body: dict):
    """Revoke an Update Client certificate: rb-iot refuses it at once; the kiosk asks for a new one on reconnect."""
    n = await db.execute("UPDATE iot_certs SET revoked_at=now() WHERE certificate_id=%s AND revoked_at IS NULL",
                         body.get("certificate_id"))
    if not n:
        raise HTTPException(404, "no such active certificate")
    return srv.ok({"revoked": body.get("certificate_id")})


@router.get("/api/provisioning/ca.crt")
async def ca_download():
    return Response(certs.ca_pem(), media_type="application/x-x509-ca-cert",
                    headers={"Content-Disposition": 'attachment; filename="redbox-backend-ca.crt"'})


@router.get("/api/provisioning/installer/{kiosk_id}.json")
async def installer_settings(kiosk_id: int, kiosk_type: str = "R630", data_zip: str = "data.zip",
                             test_mode: bool = False, address: Optional[str] = None):
    """settings.json for software/installer/cli/Install-Kiosk.ps1 (the other keys keep the script's defaults)."""
    doc = {"StoreId": kiosk_id, "DataZip": data_zip, "KioskType": kiosk_type, "Environment": "Production",
           "TestMode": test_mode, "Reboot": not test_mode,
           "Backend": {"ProfileFile": f"kiosk-{kiosk_id}.json", "Address": address or None,
                       "Reprovision": "rb-reprovision\\rb-reprovision.exe"}}
    return Response(json.dumps(doc, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="settings-{kiosk_id}.json"'})


# ------------------------------------------------------------------------------------------ catalog

@router.get("/api/catalog/browse")
async def catalog_browse(q: Optional[str] = None, types: Optional[str] = None, family: Optional[int] = None,
                         changed: bool = False, noart: bool = False, soon: bool = False, newest: bool = False,
                         limit: int = 60, offset: int = 0, sort: Optional[str] = None, dir: str = "desc"):
    """The catalog's poster grid: titles filtered by platform / family (movies 1, games 2), changed since the last
    build, without box art or coming soon, with the discs of each in kiosks; and the counts for the filter chips."""
    # t_name / t_type / t_release / t_image / key_num are stored columns (schema 70_dashboard.sql), so filtering, counting
    # and sorting 11,000 titles never unpacks their values
    where, args = ["r.tbl = 'ProductCatalog'"], []
    if q and q.strip():
        q = q.strip()
        if q.isdigit():
            where.append("(r.key = %s OR r.key IN (SELECT product_id::text FROM cloud_barcodes WHERE barcode = %s) OR r.t_name ILIKE %s)")
            args += [q, q, f"%{q}%"]
        else:
            where.append("r.t_name ILIKE %s")
            args.append(f"%{q}%")
    type_ids = [int(t) for t in (types or "").split(",") if t.strip().isdigit()]
    if type_ids:
        where.append("r.t_type = ANY(%s)")
        args.append(type_ids)
    if family:
        where.append("r.t_type IN (SELECT key_num FROM catalog_rows t WHERE t.tbl = 'ProductType' AND t.value->>'product_family_id' = %s)")
        args.append(str(family))
    stamped = await db.fetchval("SELECT stamped_at FROM catalog_state WHERE id=1")
    if changed:
        where.append("r.edited_at IS NOT NULL AND r.edited_at > coalesce(%s, '-infinity'::timestamptz)")
        args.append(stamped)
    if noart:
        where.append("coalesce(r.t_image, '') = ''")
    if soon:
        where.append("r.t_release > to_char(now(), 'YYYYMMDDHH24MISS')")
    clause = " WHERE " + " AND ".join(where)
    by = {"title": "lower(r.t_name)", "release": "r.t_release", "id": "r.key_num"}.get(sort or "", None)
    if by is None:
        by, dir = ("r.t_release", "desc") if newest or soon else ("r.key_num", dir)
    order = f"{by} {'ASC' if dir == 'asc' else 'DESC'}" + ("" if by == "r.key_num" else " NULLS LAST") + ", r.key_num DESC"
    outer = order.replace("r.key_num", "p.product_id").replace("r.t_name", "p.long_name").replace("r.t_release", "p.release_date")
    # the page is picked on the stored columns alone; only its rows' values are unpacked (rating, accessory)
    rows = await db.fetch(
        "SELECT p.*, c.value->>'rating_id' AS rating_id, c.value->>'sub_platform_id' AS sub_platform_id FROM ("
        "SELECT r.key, r.key_num AS product_id, r.t_name AS long_name, r.t_type AS product_type_id, r.t_release AS release_date,"
        f" r.t_image AS image_file, r.edited_at FROM catalog_rows r{clause} ORDER BY {order} LIMIT %s OFFSET %s) p"
        " JOIN catalog_rows c ON c.tbl = 'ProductCatalog' AND c.key = p.key ORDER BY " + outer, *args, min(limit, 300), offset)
    for r in rows:
        r.pop("key")
    stock = {}
    if rows:
        for s in await db.fetch("SELECT b.product_id, count(*) AS discs, count(DISTINCT i.kiosk_id) AS kiosks"
                                " FROM cloud_inventory i JOIN cloud_barcodes b ON b.barcode = i.barcode"
                                " WHERE b.product_id = ANY(%s) GROUP BY b.product_id", [r["product_id"] for r in rows]):
            stock[s["product_id"]] = s
    for r in rows:
        s = stock.get(r["product_id"]) or {}
        r["discs"], r["kiosks"] = s.get("discs", 0), s.get("kiosks", 0)
        edited = r.pop("edited_at")
        r["changed"] = bool(edited and (stamped is None or edited > stamped))
    counts = await db.fetchrow(
        "SELECT count(*) AS total,"
        " count(*) FILTER (WHERE edited_at IS NOT NULL AND edited_at > coalesce(%s, '-infinity'::timestamptz)) AS changed,"
        " count(*) FILTER (WHERE coalesce(t_image, '') = '') AS noart,"
        " count(*) FILTER (WHERE t_release > to_char(now(), 'YYYYMMDDHH24MISS')) AS soon"
        " FROM catalog_rows WHERE tbl = 'ProductCatalog'", stamped)
    by_type = await db.fetch("SELECT t_type AS product_type_id, count(*) AS n FROM catalog_rows"
                             " WHERE tbl = 'ProductCatalog' GROUP BY 1 ORDER BY 2 DESC")
    return srv.ok({"total": await db.fetchval(f"SELECT count(*) FROM catalog_rows r{clause}", *args), "rows": rows,
                   "counts": counts, "by_type": by_type, "stamped_at": stamped})


# ------------------------------------------------------------------------------------------ rentals

@router.get("/api/rentals")
async def rentals(kind: str = "rentals", kiosk_id: Optional[str] = None, status: Optional[str] = None,
                  q: Optional[str] = None, limit: int = 200, offset: int = 0, sort: Optional[str] = None, dir: str = "desc"):
    """Rentals (cloud_rentals line items) or returns, with the catalog title of each disc. No amounts."""
    table, when = ("cloud_rentals x", "x.rented_at") if kind == "rentals" else ("cloud_returns x", "coalesce(x.return_date, x.created_at)")
    pid = "x.product_id" if kind == "rentals" else "(SELECT product_id FROM cloud_barcodes b WHERE b.barcode = x.barcode)"
    where, args = [], []
    if kiosk_id and kiosk_id.strip().isdigit():
        where.append("x.kiosk_id = %s"); args.append(int(kiosk_id))
    if status and kind == "rentals":
        where.append("x.status = %s"); args.append(status)
    if q and q.strip():
        where.append(f"(x.barcode = %s OR {pid}::text IN (SELECT key FROM catalog_rows WHERE tbl = 'ProductCatalog'"
                     " AND value->>'long_name' ILIKE %s))")
        args += [q.strip(), f"%{q.strip()}%"]
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    order_col = {"when": when, "kiosk_id": "x.kiosk_id", "title": "lower(c.value->>'long_name')", "barcode": "x.barcode",
                 "status": "x.status" if kind == "rentals" else when, "returned_at": "x.returned_at" if kind == "rentals" else when
                 }.get(sort or "when", when)
    order = f"{order_col} {'ASC' if dir == 'asc' else 'DESC'} NULLS LAST, x.id DESC"
    cols = ("x.id, x.kiosk_id, x.barcode, x.product_id, x.product_type, x.status, x.rented_at, x.returned_at, x.return_kiosk_id"
            if kind == "rentals" else f"x.id, x.kiosk_id, x.barcode, {pid} AS product_id, x.deck, x.slot, x.return_date, x.created_at")
    rows = await db.fetch(
        f"SELECT {cols}, c.value->>'long_name' AS title, t.value->>'product_type_name' AS type FROM {table}"
        f" LEFT JOIN catalog_rows c ON c.tbl = 'ProductCatalog' AND c.key = ({pid})::text"
        " LEFT JOIN catalog_rows t ON t.tbl = 'ProductType' AND t.key = c.value->>'product_type_id'"
        f"{clause} ORDER BY {order} LIMIT %s OFFSET %s", *args, min(limit, 500), offset)
    return srv.ok({"total": await db.fetchval(f"SELECT count(*) FROM {table}{clause}", *args), "rows": rows})


# ------------------------------------------------------------------------------------------- stores
# Store management: the profile.data tables Store, Banner and Market (catalog/stores.py), built into profile.data
# with the catalog, and the stores offered to the kiosk installer. Dates come in as ISO dates, times as "HH:mm".

from ..catalog import stores as st  # noqa: E402


def _bool(v):
    return None if v in (None, "") else str(v).lower() in ("1", "true", "yes")


async def _store_call(coro):
    try:
        return await coro
    except st.StoreError as e:
        raise HTTPException(400, str(e))


@router.get("/api/stores")
async def stores_list(q: Optional[str] = None, offered: Optional[str] = None, state: Optional[str] = None,
                      banner_id: Optional[str] = None, market_id: Optional[str] = None, has_kiosk: Optional[str] = None,
                      sort: Optional[str] = None, dir: str = "asc", limit: int = 100, offset: int = 0):
    return srv.ok(await st.search(q, _bool(offered), limit, offset, sort, dir, state, banner_id, market_id, _bool(has_kiosk)))


@router.get("/api/stores/stats")
async def stores_stats():
    return srv.ok(dict(await st.stats(), present=sorted(await st.tables_present())))


@router.get("/api/stores/lookups")
async def stores_lookups():
    return srv.ok(await st.lookups())


@router.get("/api/stores/installer")
async def stores_installer():
    return srv.ok(await db.fetch("SELECT i.*, k.last_seen_at FROM cloud_installer_kiosks i LEFT JOIN kiosks k"
                                 " ON k.kiosk_id = i.id ORDER BY i.pending DESC, i.state_name, i.banner_name, i.id"))


@router.get("/api/stores/{store_id}")
async def store_get(store_id: int):
    s = await st.get_store(store_id)
    if s is None:
        raise HTTPException(404, f"no store {store_id}")
    return srv.ok(s)


async def _save_store(body):
    value = await _store_call(st.save_store(body))
    if "offered" in body:
        await _store_call(st.set_offer(value["store_id"], bool(body["offered"]), body.get("due_time") or None,
                                       body.get("kaseya_market_name") or None))
    await db.record_event(value["store_id"], "dashboard", "store-save", {"store": value["store_id"], "by": _who()})
    return srv.ok(await st.get_store(value["store_id"]))


@router.post("/api/stores")
async def store_create(body: dict):
    try:
        sid = int(body.get("store_id"))
    except (TypeError, ValueError):
        raise HTTPException(400, "store ID (a number) required")
    if await st.get_store(sid):
        raise HTTPException(409, f"store {sid} already exists")
    return await _save_store(body)


@router.put("/api/stores/{store_id}")
async def store_update(store_id: int, body: dict):
    if not await st.get_store(store_id):
        raise HTTPException(404, f"no store {store_id}")
    return await _save_store(dict(body, store_id=store_id))


@router.post("/api/stores/{store_id}/offer")
async def store_offer(store_id: int, body: dict):
    await _store_call(st.set_offer(store_id, bool(body.get("offered", True)), body.get("due_time"), body.get("kaseya_market_name")))
    return srv.ok({"store": store_id, "offered": bool(body.get("offered", True))})


@router.delete("/api/stores/{store_id}")
async def store_delete(store_id: int):
    n = await _store_call(st.delete_store(store_id))
    if not n:
        raise HTTPException(404, f"no store {store_id}")
    await db.record_event(store_id, "dashboard", "store-delete", {"store": store_id, "by": _who()})
    return srv.ok({"deleted": store_id})


def _named_routes(kind, tbl):
    """List / usage / create / rename / delete for /api/banners and /api/markets."""
    async def listing(q: Optional[str] = None, sort: str = "name", dir: str = "asc", limit: int = 100, offset: int = 0):
        return srv.ok(await st.named_list(tbl, q, limit, offset, sort, dir))

    async def usage(key: int):
        return srv.ok({"stores": await st.named_usage(tbl, key)})

    async def create(body: dict):
        return srv.ok({"id": await _store_call(st.save_named(tbl, None, body.get("name")))})

    async def update(key: int, body: dict):
        return srv.ok({"id": await _store_call(st.save_named(tbl, key, body.get("name")))})

    async def delete(key: int, reassign_to: Optional[int] = None, clear: bool = False):
        moved = await _store_call(st.delete_named(tbl, key, reassign_to, clear))
        await db.record_event(None, "dashboard", f"{kind[:-1]}-delete", {"id": key, "moved": moved, "to": reassign_to, "by": _who()})
        return srv.ok({"deleted": key, "moved": moved})

    router.add_api_route(f"/api/{kind}", listing, methods=["GET"])
    router.add_api_route(f"/api/{kind}/{{key}}/usage", usage, methods=["GET"])
    router.add_api_route(f"/api/{kind}", create, methods=["POST"])
    router.add_api_route(f"/api/{kind}/{{key}}", update, methods=["PUT"])
    router.add_api_route(f"/api/{kind}/{{key}}", delete, methods=["DELETE"])


_named_routes("banners", st.BANNER)
_named_routes("markets", st.MARKET)

# ------------------------------------------------------------------------------------ administration
# Admin-only, long-running operations (System administration page): every import / build job and the stores import.

_admin_tasks = set()


async def _job(source, options, fn):
    job = await db.fetchval("INSERT INTO upd_import_jobs (source, options) VALUES (%s,%s) RETURNING id", source, db.jsonb(options))

    async def progress(msg):
        await db.execute("UPDATE upd_import_jobs SET progress = progress || %s WHERE id=%s",
                         db.jsonb([{"t": dt.datetime.now(dt.timezone.utc).isoformat(), "m": msg}]), job)

    async def run():
        try:
            await fn(progress)
            await db.execute("UPDATE upd_import_jobs SET status='done', finished_at=now() WHERE id=%s", job)
        except Exception as e:
            await progress(f"failed: {e}")
            await db.execute("UPDATE upd_import_jobs SET status='failed', finished_at=now() WHERE id=%s", job)
    task = asyncio.create_task(run())
    _admin_tasks.add(task)
    task.add_done_callback(_admin_tasks.discard)
    return job


@router.get("/api/admin/jobs")
async def admin_jobs(limit: int = 30):
    return srv.ok(await db.fetch("SELECT * FROM upd_import_jobs ORDER BY id DESC LIMIT %s", min(limit, 200)))


@router.get("/api/admin/jobs/{job_id}")
async def admin_job(job_id: int):
    j = await db.fetchrow("SELECT * FROM upd_import_jobs WHERE id=%s", job_id)
    if j is None:
        raise HTTPException(404)
    return srv.ok(j)


@router.post("/api/admin/stores-import")
async def admin_stores_import(body: dict):
    """Replace Store / Banner / Market with a seed's (a dvd-data revision, default the served head), in the background."""
    revision = (body.get("revision") or "").strip() or None
    job = await _job(f"stores from dvd-data@{revision or 'served head'}", {"kind": "stores", "revision": revision},
                     lambda p: st.import_tables(revision=revision, progress=p))
    await db.record_event(None, "dashboard", "stores-import", {"job": job, "revision": revision, "by": _who()})
    return srv.ok({"job": job})
