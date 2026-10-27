"""Bluefin device activation stand-in (Device Service `ActivationService`, base `DeviceService.BluefinServiceUrl`).

`POST {url}/device/activate {KioskId, ReaderSerialNumber, InjectedSerialNumber, LocalDateTime}` and
`POST {url}/device/deactivate {KioskId, ReaderSerialNumber}`, header `x-api-key`, StandardResponse reply.
Served under both `/device/...` and `/bluefinservice/device/...` (the image's URL ends in /bluefinservice).
"""
from fastapi import APIRouter, Request

from ... import db
from ..common import body_json, check_key, ci, error, kiosk_id, standard, touch

router = APIRouter()
SOURCE = "bluefin"


async def activate(request: Request):
    bad = check_key(request, SOURCE)
    if bad:
        return bad
    body = await body_json(request, {}) or {}
    kiosk = kiosk_id(request, body)
    serial = ci(body, "ReaderSerialNumber")
    if not kiosk or not serial:
        return standard(False, [error("BadRequest", "KioskId and ReaderSerialNumber are required")], 400)
    await db.execute(
        "INSERT INTO cloud_bluefin_devices (kiosk_id, reader_serial, injected_serial, status, local_time, activated_at) "
        "VALUES (%s, %s, %s, 'active', %s, now()) ON CONFLICT (kiosk_id, reader_serial) DO UPDATE SET "
        "injected_serial = EXCLUDED.injected_serial, status = 'active', local_time = EXCLUDED.local_time, "
        "activated_at = now(), deactivated_at = NULL",
        kiosk, str(serial), ci(body, "InjectedSerialNumber"), ci(body, "LocalDateTime"))
    await db.record_event(kiosk, SOURCE, "activate", body)
    await touch(kiosk, info={"bluefin": {"ReaderSerialNumber": serial, "Status": "active"}})
    return standard()


async def deactivate(request: Request):
    bad = check_key(request, SOURCE)
    if bad:
        return bad
    body = await body_json(request, {}) or {}
    kiosk = kiosk_id(request, body)
    serial = ci(body, "ReaderSerialNumber")
    if not kiosk or not serial:
        return standard(False, [error("BadRequest", "KioskId and ReaderSerialNumber are required")], 400)
    n = await db.execute("UPDATE cloud_bluefin_devices SET status = 'deactivated', deactivated_at = now() "
                         "WHERE kiosk_id = %s AND reader_serial = %s", kiosk, str(serial))
    await db.record_event(kiosk, SOURCE, "deactivate", body)
    await touch(kiosk, info={"bluefin": {"ReaderSerialNumber": serial, "Status": "deactivated"}})
    if not n:
        return standard(False, [error("NotFound", f"Reader {serial} is not active for kiosk {kiosk}")], 404)
    return standard()


for _prefix in ("", "/bluefinservice"):
    router.add_api_route(_prefix + "/device/activate", activate, methods=["POST"])
    router.add_api_route(_prefix + "/device/deactivate", deactivate, methods=["POST"])
