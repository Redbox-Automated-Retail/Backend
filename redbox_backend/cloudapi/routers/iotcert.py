"""IoT certificate service (AppSettings:IoTCertificateServiceUrl), docs/systems/update-client.md §5.2.

POST api/thing-types/get-certificates   {type: E("kiosks"), name: E(kioskId), thingGroupName}  -> current or new cert
POST api/thing-types/certificates       same body                                            -> always a new cert
POST api/thing-types/certificate-valid  {..., certificateId: E(certId)}                       -> true / false
Headers: x-api-key (not checked), password = HashService.GetKioskPassword(kioskId) (checked).
The reply PFX is opened by the kiosk with HashService.GetCertificatePassword(kioskId).
"""
import base64
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ... import certs, db, kioskcrypto

log = logging.getLogger("iotcert")
router = APIRouter()


async def _identify(body, password):
    """Return the kiosk id from the encrypted name, or by matching the password against known kiosks."""
    name = body.get("name") or body.get("Name")
    if name and kioskcrypto.passphrase():
        try:
            return int(kioskcrypto.decrypt(name))
        except Exception as e:
            log.warning("could not decrypt thing name: %s", e)
    if password:
        for row in await db.fetch("SELECT kiosk_id FROM kiosks UNION SELECT kiosk_id FROM iot_certs"):
            if kioskcrypto.kiosk_password(row["kiosk_id"]) == password:
                return int(row["kiosk_id"])
    return None


async def _issue(kiosk_id, market):
    pfx, cert_id, _ = certs.issue_device_certificate(kiosk_id, kioskcrypto.certificate_password(kiosk_id))
    pfx_b64 = base64.b64encode(pfx).decode()
    await db.execute("UPDATE iot_certs SET revoked_at = now() WHERE kiosk_id = %s AND revoked_at IS NULL", kiosk_id)
    await db.execute("INSERT INTO iot_certs (certificate_id, kiosk_id, market, pfx_b64) VALUES (%s,%s,%s,%s)",
                     cert_id, kiosk_id, market, pfx_b64)
    await db.record_event(kiosk_id, "iotcert", "issued", {"certificateId": cert_id, "market": market})
    return {"DeviceCertPfxBase64": pfx_b64, "RootCa": certs.ca_pem(), "CertificateId": cert_id}


async def _authenticate(request: Request):
    body = await request.json()
    password = request.headers.get("password")
    kiosk_id = await _identify(body, password)
    if kiosk_id is None:
        return None, body, JSONResponse({"Message": "Unknown kiosk (set UCS_ENCRYPTION_SOURCE or UCS_ENCRYPTION_PASSPHRASE)"},
                                        status_code=403)
    if password != kioskcrypto.kiosk_password(kiosk_id):
        log.warning("kiosk %s sent a wrong password header", kiosk_id)
        return None, body, JSONResponse({"Message": "Invalid password"}, status_code=403)
    market = body.get("thingGroupName") or body.get("ThingGroupName")
    await db.touch_kiosk(kiosk_id, market=market)
    return kiosk_id, body, None


@router.post("/api/thing-types/get-certificates")
async def get_certificates(request: Request):
    kiosk_id, body, error = await _authenticate(request)
    if error:
        return error
    row = await db.fetchrow("SELECT * FROM iot_certs WHERE kiosk_id=%s AND revoked_at IS NULL ORDER BY created_at DESC"
                            " LIMIT 1", kiosk_id)
    if row:
        return {"DeviceCertPfxBase64": row["pfx_b64"], "RootCa": certs.ca_pem(), "CertificateId": row["certificate_id"]}
    return await _issue(kiosk_id, body.get("thingGroupName"))


@router.post("/api/thing-types/certificates")
async def new_certificates(request: Request):
    kiosk_id, body, error = await _authenticate(request)
    if error:
        return error
    return await _issue(kiosk_id, body.get("thingGroupName"))


@router.post("/api/thing-types/certificate-valid")
async def certificate_valid(request: Request):
    kiosk_id, body, error = await _authenticate(request)
    if error:
        return error
    cert_id = body.get("certificateId") or ""
    if kioskcrypto.passphrase():
        try:
            cert_id = kioskcrypto.decrypt(cert_id)
        except Exception:
            pass
    row = await db.fetchrow("SELECT revoked_at FROM iot_certs WHERE certificate_id=%s AND kiosk_id=%s", cert_id, kiosk_id)
    return JSONResponse(bool(row and row["revoked_at"] is None))
