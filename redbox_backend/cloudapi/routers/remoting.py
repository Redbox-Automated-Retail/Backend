"""Legacy .NET Remoting backends, together with the Mono host in rb-remoting (remoting/).

Public, on the kiosk-facing host:
  POST /KioskServices/MessageService.rem
       * binary .NET Remoting request (HttpChannel + binary formatter, KioskServicesProtocol = Remoting): the
         body is forwarded unchanged to the Mono host, whose real System.Runtime.Remoting decodes it and calls
         back into /_internal/kioskservices/process;
       * form post `payload=<url-encoded json>` (KioskServicesProtocol = REST): answered here; the reply is the
         URL-encoded JSON the client URL-decodes.
Internal (X-Internal-Token from CERT_DIR/internal.key; the Mono host is the only caller):
  POST /_internal/kioskservices/process     body = message JSON, reply = response JSON
  POST /_internal/broker/registered         {kioskId, version, host}
  POST /_internal/broker/disconnected       {kioskId, reason}
"""
import hmac
import json
import logging
import os
import urllib.parse

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ... import certs, db
from .. import kioskservices

log = logging.getLogger("cloudapi.remoting")
router = APIRouter()
MONO_MESSAGESERVICE = os.environ.get("REMOTING_MESSAGESERVICE_URL",
                                     "http://rb-remoting:8090/KioskServices/MessageService.rem")


def _check_internal(request: Request):
    given = request.headers.get("x-internal-token", "")
    if not given or not hmac.compare_digest(given, certs.internal_token()):
        raise HTTPException(403, "internal endpoint")


async def _process(text, transport, request):
    try:
        message = json.loads(text) if text else {}
    except json.JSONDecodeError:
        return {"Success": False, "Error": "malformed JSON"}
    try:
        result = await kioskservices.process(message, transport=transport)
    except Exception as e:  # never return a stack trace to a kiosk
        log.exception("kiosk services message failed")
        result = {"Success": False, "Error": f"{type(e).__name__}: {e}"}
    inner = message.get("ReturnMessage") if isinstance(message.get("ReturnMessage"), dict) else message  # queued Return
    kid = inner.get("KioskId") or message.get("StoreNumber") or request.headers.get("x-redbox-kioskid")
    await db.record_event(kid, "kioskservices", str(inner.get("MessageType") or "unknown"),
                          {"transport": transport, "request": _scrub(message), "success": result.get("Success")})
    return result


SECRET_KEYS = {"password", "pin", "track2", "number", "creditcard", "cardnumber", "ksn", "icencrypteddata"}


def _scrub(obj):
    if isinstance(obj, dict):
        return {k: ("***" if k.lower() in SECRET_KEYS and v else _scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


@router.post("/KioskServices/MessageService.rem")
async def message_service(request: Request):
    ctype = (request.headers.get("content-type") or "").lower()
    body = await request.body()
    if ctype.startswith("application/x-www-form-urlencoded"):
        form = urllib.parse.parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
        result = await _process((form.get("payload") or [""])[0], "rest", request)
        text = urllib.parse.quote(kioskservices.dumps(result), safe="")
        return Response(text, media_type="text/plain; charset=utf-8")
    # Binary remoting: hand the HTTP request to Mono's HttpChannel as is.
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length", "connection",
                                                                              "transfer-encoding")}
    async with httpx.AsyncClient(timeout=120) as client:
        try:
            r = await client.post(MONO_MESSAGESERVICE, content=body, headers=headers)
        except httpx.HTTPError as e:
            log.warning("remoting host unreachable: %s", e)
            raise HTTPException(502, "remoting host unreachable")
    keep = {k: v for k, v in r.headers.items() if k.lower() not in ("content-length", "transfer-encoding",
                                                                    "connection", "server", "date")}
    return Response(r.content, status_code=r.status_code, headers=keep)


@router.post("/_internal/kioskservices/process")
async def internal_process(request: Request, transport: str = "remoting"):
    _check_internal(request)
    result = await _process((await request.body()).decode("utf-8", "replace"), transport, request)
    return Response(kioskservices.dumps(result), media_type="application/json")


@router.post("/_internal/broker/registered")
async def broker_registered(request: Request):
    _check_internal(request)
    body = await request.json()
    kid = int(body["kioskId"])
    try:
        version = json.loads(body.get("version") or "null")
    except json.JSONDecodeError:
        version = {"raw": body.get("version")}
    await db.execute("INSERT INTO broker_kiosks (kiosk_id, version, host, connected, registered_at) VALUES"
                     " (%s,%s,%s,true,now()) ON CONFLICT (kiosk_id) DO UPDATE SET version=EXCLUDED.version,"
                     " host=EXCLUDED.host, connected=true, registered_at=now(), registrations=broker_kiosks.registrations+1",
                     kid, db.jsonb(version), body.get("host"))
    await db.touch_kiosk(kid)
    await db.record_event(kid, "broker", "registered", {"version": version, "host": body.get("host")})
    return {"ok": True}


@router.post("/_internal/broker/disconnected")
async def broker_disconnected(request: Request):
    _check_internal(request)
    body = await request.json()
    kid = int(body["kioskId"])
    await db.execute("UPDATE broker_kiosks SET connected=false, disconnected_at=now() WHERE kiosk_id=%s", kid)
    await db.record_event(kid, "broker", "disconnected", {"reason": body.get("reason")})
    return {"ok": True}
