"""Helpers shared by the kiosk cloud API routers.

Conventions of the .NET clients (KCS, Device Service, Kiosk Engine):

- Request bodies are Newtonsoft JSON with PascalCase names; field lookups here are case-insensitive.
- Replies use PascalCase like the C# models; Newtonsoft reads them case-insensitively.
- Bodies may be gzip/deflate compressed (`Content-Encoding`), e.g. the inventory snapshot.
- API keys arrive in `x-api-key` or `Authorization`. They are logged when missing and only enforced
  when CLOUD_API_ENFORCE_KEYS is true and CLOUD_API_KEYS lists the accepted keys.
"""
import datetime as _dt
import decimal
import gzip
import json
import logging
import os
import socket
import uuid
import zlib

from fastapi import Request
from fastapi.responses import JSONResponse, Response

from .. import db

log = logging.getLogger("cloudapi.common")

HOSTNAME = os.environ.get("CLOUD_API_HOSTNAME") or socket.gethostname()
ENFORCE_KEYS = (os.environ.get("CLOUD_API_ENFORCE_KEYS", "") or "").strip().lower() in ("1", "true", "yes", "on")
ACCEPTED_KEYS = {k.strip() for k in (os.environ.get("CLOUD_API_KEYS", "") or "").split(",") if k.strip()}
# Base URL put into presigned/asset URLs. Empty: derived from the request (scheme + Host header).
PUBLIC_BASE_URL = (os.environ.get("CLOUD_PUBLIC_BASE_URL", "") or "").rstrip("/")


# ------------------------------------------------------------------ JSON

def _default(o):
    if isinstance(o, decimal.Decimal):
        return float(o)
    if isinstance(o, _dt.datetime):
        return net_datetime(o)
    if isinstance(o, _dt.date):
        return o.isoformat() + "T00:00:00"
    if isinstance(o, uuid.UUID):
        return str(o)
    if isinstance(o, (set, tuple)):
        return list(o)
    if isinstance(o, (bytes, bytearray)):
        return None
    raise TypeError(f"not JSON serializable: {type(o)}")


def dumps(obj) -> str:
    return json.dumps(obj, default=_default, separators=(",", ":"))


class NetJSONResponse(JSONResponse):
    def render(self, content) -> bytes:
        return dumps(content).encode("utf-8")


def reply(obj, status=200, headers=None):
    return NetJSONResponse(obj, status_code=status, headers=headers)


def standard(success=True, errors=None, status=200, **extra):
    """BaseResponse / StandardResponse: Success, Errors, HostName, StatusCode (+ model fields)."""
    body = {"Success": success, "Errors": errors or [], "HostName": HOSTNAME, "StatusCode": status}
    body.update(extra)
    return reply(body, status)


def error(code, message):
    return {"Code": code, "Message": message}


def net_datetime(value, utc=None):
    """Newtonsoft DateTime text. Aware values are written in UTC with 'Z'; naive ones without zone."""
    if value is None:
        return None
    if isinstance(value, _dt.date) and not isinstance(value, _dt.datetime):
        return value.isoformat() + "T00:00:00"
    if value.tzinfo is not None:
        value = value.astimezone(_dt.timezone.utc).replace(tzinfo=None)
        return value.strftime("%Y-%m-%dT%H:%M:%S") + (f".{value.microsecond:06d}" if value.microsecond else "") + "Z"
    return value.strftime("%Y-%m-%dT%H:%M:%S") + (f".{value.microsecond:06d}" if value.microsecond else "")


def net_offset(value):
    """DateTimeOffset text (ISO with offset)."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=_dt.timezone.utc)
    return value.isoformat()


def now():
    return _dt.datetime.now(_dt.timezone.utc)


def parse_dt(value):
    """Parse a .NET date (ISO, with or without offset). Naive results are taken as UTC."""
    if value in (None, ""):
        return None
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc)
    s = str(value).strip()
    if s.lower() == "null":
        return None
    from ..dotnet import parse_iso
    try:
        d = parse_iso(s)
    except Exception:
        d = None
    if d is None:
        return None
    return d if d.tzinfo else d.replace(tzinfo=_dt.timezone.utc)


def trunc_seconds(value):
    return value.replace(microsecond=0) if value is not None else None


# ------------------------------------------------------------------ requests

def ci(d, *names, default=None):
    """Case-insensitive lookup of the first present key."""
    if not isinstance(d, dict):
        return default
    lower = {str(k).lower(): v for k, v in d.items()}
    for n in names:
        v = lower.get(n.lower())
        if v is not None:
            return v
    return default


def ci_list(d, *names):
    v = ci(d, *names)
    return v if isinstance(v, list) else []


def to_int(v, default=None):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default


def to_dec(v, default=decimal.Decimal("0")):
    try:
        return decimal.Decimal(str(v)) if v is not None else default
    except (decimal.InvalidOperation, ValueError):
        return default


def to_uuid(v):
    try:
        u = uuid.UUID(str(v))
        return None if u.int == 0 else u
    except (TypeError, ValueError, AttributeError):
        return None


def decode_body(raw: bytes, encoding: str) -> bytes:
    enc = (encoding or "").lower()
    if not raw:
        return raw
    if "gzip" in enc or raw[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(raw)
        except OSError:
            pass
    if "deflate" in enc:
        for wbits in (zlib.MAX_WBITS, -zlib.MAX_WBITS):
            try:
                return zlib.decompress(raw, wbits)
            except zlib.error:
                continue
    return raw


async def body_json(request: Request, default=None):
    raw = decode_body(await request.body(), request.headers.get("content-encoding"))
    if not raw or not raw.strip():
        return default
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        log.warning("%s %s: body is not JSON (%d bytes)", request.method, request.url.path, len(raw))
        return default


def kiosk_id(request: Request, body=None, fallback=None):
    v = to_int(request.headers.get("x-redbox-kioskid"))
    if not v and isinstance(body, dict):
        v = to_int(ci(body, "KioskId", "SourceKioskId", "Id"))
    if not v:
        v = to_int(fallback)
    return v or None


def activity_id(request: Request):
    return request.headers.get("x-redbox-activityid")


def check_key(request: Request, service: str):
    """Log missing/unknown API keys; return a 401 response when enforcement is on and the key is bad."""
    key = request.headers.get("x-api-key")
    auth = request.headers.get("authorization")
    if not key and auth:
        key = auth.split(" ", 1)[1].strip() if auth.lower().startswith("bearer ") else auth.strip()
    if not key:
        log.info("%s: no API key on %s %s", service, request.method, request.url.path)
        if ENFORCE_KEYS:
            return standard(False, [error("Unauthorized", "missing API key")], 401)
        return None
    if ACCEPTED_KEYS and key not in ACCEPTED_KEYS:
        log.info("%s: unknown API key ...%s on %s", service, key[-4:], request.url.path)
        if ENFORCE_KEYS:
            return standard(False, [error("Unauthorized", "invalid API key")], 401)
    return None


def base_url(request: Request) -> str:
    if PUBLIC_BASE_URL:
        return PUBLIC_BASE_URL
    host = request.headers.get("host") or f"{request.url.hostname}:{request.url.port}"
    return f"{request.url.scheme}://{host}"


async def touch(kiosk, **fields):
    if kiosk:
        try:
            await db.touch_kiosk(kiosk, **fields)
        except Exception:
            log.exception("touch_kiosk failed")


async def event(kiosk, source, kind, payload=None):
    await db.record_event(kiosk, source, kind, payload)
    await touch(kiosk)


# ------------------------------------------------------------------ idempotency

async def replayed(message_id, endpoint):
    """Return the stored (status, body) for a message already processed, else None."""
    if not message_id:
        return None
    row = await db.fetchrow("SELECT status, response FROM cloud_processed_messages WHERE message_id = %s AND endpoint = %s",
                            message_id, endpoint)
    return (row["status"], row["response"]) if row else None


async def remember(message_id, endpoint, kiosk, status, body):
    if not message_id:
        return
    await db.execute("INSERT INTO cloud_processed_messages (message_id, endpoint, kiosk_id, status, response) "
                     "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                     message_id, endpoint, kiosk, status, db.jsonb(json.loads(dumps(body))))


def raw_response(content: bytes, media_type: str, filename=None):
    headers = {}
    if filename:
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    return Response(content, media_type=media_type, headers=headers)
