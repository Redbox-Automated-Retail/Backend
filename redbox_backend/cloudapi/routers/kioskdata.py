"""Kiosk Data Service stand-in.

Callers:
- KCS `DataClient` (`KioskClientDefault.KioskDataServiceUrl`): `api/kioskdata/<kind>` telemetry and
  `api/KioskData/KioskClientServiceEvent` service start/stop events;
- Device Service `KioskDataServiceClient` (`Data\\KioskData.json` ApiUrl): `api/KioskData/DeviceStatus`,
  `RebootStatus`, `CardStats` (StandardResponse replies);
- Kiosk Engine `KioskDataServicesProxy` (`system.config` General/KioskDataServicesUrl):
  `api/KioskData/AddKioskSessionJson` (reply must contain `"success": true`).

Everything is appended to the events table; versions/properties/statistics/device status are also
merged into `kiosks.info`, and customer sessions are decoded into `cloud_kiosk_sessions`.
"""
import base64
import gzip
import json
import logging
import struct

from fastapi import APIRouter, Request

from ... import db
from ..common import HOSTNAME, body_json, check_key, ci, kiosk_id, reply, standard, to_uuid, touch

log = logging.getLogger("cloudapi.kioskdata")
router = APIRouter()
SOURCE = "kioskdata"

# KCS endpoint kind -> kiosks.info key merged from the body
KCS_KINDS = {
    "kioskversion": "Versions",
    "kioskstatistics": "Statistics",
    "kioskproperties": "Properties",
    "kioskalert": None,
    "auditevent": None,
    "kioskevents": None,
    "kioskfunctioncheck": None,
}


async def _body(request):
    b = await body_json(request, {})
    return b if isinstance(b, dict) else {"_body": b}


@router.post("/api/kioskdata/{kind}")
async def kcs_kiosk_data(kind: str, request: Request):
    key = kind.lower()
    if key in ("devicestatus", "rebootstatus", "cardstats"):
        return await device_data(key, request)
    if key == "kioskclientserviceevent":
        return await kcs_service_event(request)
    if key == "addkiosksessionjson":
        return await add_kiosk_session_json(request)
    check_key(request, SOURCE)
    body = await _body(request)
    kiosk = kiosk_id(request, body)
    message_id = to_uuid(ci(body, "MessageId", "UniqueId"))
    if key not in KCS_KINDS:
        log.info("unknown kiosk data kind %s from kiosk %s", kind, kiosk)
    await db.record_event(kiosk, SOURCE, key, body)
    field = KCS_KINDS.get(key)
    info = None
    if field and isinstance(ci(body, field), dict):
        info = {field.lower(): ci(body, field)}
    elif key == "kioskfunctioncheck":
        info = {"functioncheck": body}
    await touch(kiosk, info=info)
    extra = {"MessageId": str(message_id)} if message_id else {}
    return standard(**extra)


async def kcs_service_event(request: Request):
    check_key(request, SOURCE)
    body = await _body(request)
    kiosk = kiosk_id(request, body)
    await db.record_event(kiosk, SOURCE, "kioskclientserviceevent", body)
    info = {"kcs": {"AssemblyVersion": ci(body, "AssemblyVersion"), "LastEvent": ci(body, "EventType"),
                    "EventTimeUtc": ci(body, "EventTimeUtc")}}
    await touch(kiosk, info=info)
    return standard(KinesisResponse=None)


async def device_data(kind: str, request: Request):
    """Device Service posts (StandardResponse)."""
    check_key(request, SOURCE)
    body = await _body(request)
    kiosk = kiosk_id(request, body)
    await db.record_event(kiosk, "deviceservice", kind, body)
    info = None
    if kind == "devicestatus":
        info = {"device_status": body}
    elif kind == "rebootstatus":
        info = {"device_reboot": body}
    await touch(kiosk, info=info)
    return standard()


def decode_session_data(data, uncompressed=False):
    """SessionData = base64(int32 LE length || gzip(UTF-8 JSON)); tolerant of plain JSON / plain gzip."""
    if data is None:
        return None
    if isinstance(data, (dict, list)):
        return data
    text = str(data)
    if uncompressed:
        try:
            return json.loads(text)
        except ValueError:
            pass
    raw = base64.b64decode(text, validate=False)
    payload = raw[4:] if len(raw) > 4 and raw[4:6] == b"\x1f\x8b" else raw
    if payload[:2] == b"\x1f\x8b":
        out = gzip.decompress(payload)
        if len(raw) > 4 and raw[4:6] == b"\x1f\x8b":
            expected = struct.unpack("<i", raw[:4])[0]
            if expected != len(out):
                log.info("session data length prefix %s != %s", expected, len(out))
    else:
        out = payload
    return json.loads(out.decode("utf-8-sig"))


async def add_kiosk_session_json(request: Request):
    check_key(request, SOURCE)
    raw = await request.body()
    text = raw.decode("utf-8-sig", errors="replace")
    try:
        body = json.loads(text)
    except ValueError:
        return reply({"success": False, "error": "body is not JSON"}, 400)
    kiosk = kiosk_id(request, body)
    session_id = str(ci(body, "SessionId") or request.headers.get("x-redbox-activityid") or "")
    data, keep_raw = None, None
    try:
        data = decode_session_data(ci(body, "SessionData"), bool(ci(body, "IsUnCompressed")))
    except Exception as e:  # keep what we got
        log.warning("could not decode session %s from kiosk %s: %s", session_id, kiosk, e)
        keep_raw = str(ci(body, "SessionData"))
    if session_id:
        await db.execute(
            "INSERT INTO cloud_kiosk_sessions (session_id, kiosk_id, start_time, end_time, data, raw) "
            "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (session_id) DO UPDATE SET data = EXCLUDED.data, "
            "raw = EXCLUDED.raw, end_time = EXCLUDED.end_time, received_at = now()",
            session_id, kiosk, ci(body, "StartTime"), ci(body, "EndTime"), db.jsonb(data), keep_raw)
    await db.record_event(kiosk, SOURCE, "kiosksession", {"SessionId": session_id, "Decoded": data is not None})
    await touch(kiosk)
    return reply({"success": True, "sessionId": session_id, "hostName": HOSTNAME})


@router.post("/api/KioskData/DeviceStatus")
async def device_status(request: Request):
    return await device_data("devicestatus", request)


@router.post("/api/KioskData/RebootStatus")
async def reboot_status(request: Request):
    return await device_data("rebootstatus", request)


@router.post("/api/KioskData/CardStats")
async def card_stats(request: Request):
    return await device_data("cardstats", request)


@router.post("/api/KioskData/KioskClientServiceEvent")
async def kcs_event_route(request: Request):
    return await kcs_service_event(request)


@router.post("/api/KioskData/AddKioskSessionJson")
async def add_session_route(request: Request):
    return await add_kiosk_session_json(request)
