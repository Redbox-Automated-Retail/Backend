"""IoT service: MQTT broker + the "IoT rules / Update Service instances" behind it + an operator API.

Kiosk -> cloud (docs/systems/update-client.md §5.3):
  $aws/rules/kioskucsregister   UCSRegisterMessage                       (recorded)
  $aws/rules/kioskping          KioskPing request                        (recorded, no reply)
  $aws/rules/kioskrestcall      IoTCommandModel requests                 -> Response on redbox/kiosk/{id}/command
Cloud -> kiosk: IoTCommandModel requests on redbox/kiosk/{id}/command, .../market/{m}/command, .../all/command;
kiosk replies arrive on redbox/updateservice-instance/{IOT_INSTANCE_ID}/request.

Operator API (HTTP, IOT_ADMIN_PORT, keep it on the internal network):
  GET  /clients                        connected kiosks
  POST /send {target, command, version, payload, wait}   target = <kioskId> | market:<m> | all
  POST /push/config {kioskId?, market?}                  TriggerGetConfigChanges
  POST /push/deploy {deploymentId}                       TriggerReportFileSetVersions for its targets
  POST /download-job {name, fileName, destinationPath, fileB64?, scriptName?, scriptB64?, target}
  POST /kiosk/{id}/peek {fileType?, path?, searchPattern?}       PeekFiles v2 (waits)
  POST /kiosk/{id}/upload {fileType?, basePath?, fileNames[], zip}  UploadFiles v2 to the S3 stand-in (waits)
  GET  /requests/{requestId}
"""
import asyncio
import base64
import datetime as dt
import hashlib
import json
import logging
import os
import uuid

import uvicorn
from fastapi import FastAPI, HTTPException

from .. import certs, config, db, dotnet, filesets
from ..cloudapi.routers import s3
from . import kioskconfig, mqtt

log = logging.getLogger("iot")

broker = None
_waiters = {}


def _now_iso():
    return dotnet.iso()


def command(cmd, payload=None, version=1, message_type="Request", request_id=None):
    return {"command": cmd, "messageType": message_type, "version": version,
            "requestId": request_id or str(uuid.uuid4()), "origin": "UpdateService",
            "payload": payload, "sourceId": config.IOT_INSTANCE_ID, "requestTimeUtc": _now_iso()}


def topic_for(target):
    target = str(target)
    if target == "all":
        return "redbox/kiosk/all/command"
    if target.startswith("market:"):
        return f"redbox/kiosk/market/{target[7:]}/command"
    return f"redbox/kiosk/{target}/command"


async def send(target, cmd, payload=None, version=1, wait=0.0):
    msg = command(cmd, payload, version)
    t = topic_for(target)
    kiosk = int(target) if str(target).isdigit() else None
    await db.execute("INSERT INTO iot_requests (request_id, kiosk_id, topic, command, version, payload) VALUES"
                     " (%s,%s,%s,%s,%s,%s)", msg["requestId"], kiosk, t, cmd, version, db.jsonb(payload))
    fut = None
    if wait:
        fut = asyncio.get_running_loop().create_future()
        _waiters[msg["requestId"]] = fut
    delivered = await broker.publish(t, json.dumps(msg).encode(), 1)
    log.info("sent %s v%s to %s (%d deliveries)", cmd, version, t, delivered)
    result = {"requestId": msg["requestId"], "topic": t, "delivered": delivered}
    if fut is not None:
        try:
            result["response"] = await asyncio.wait_for(fut, wait)
        except asyncio.TimeoutError:
            result["response"] = None
        finally:
            _waiters.pop(msg["requestId"], None)
    return result


async def reply(client, request, status=200, value=None, raw_payload=None):
    payload = raw_payload if raw_payload is not None else ({"StatusCode": status, "Value": value}
                                                            if value is not None else {"StatusCode": status})
    msg = command(request.get("command"), payload, request.get("version", 1), "Response", request.get("requestId"))
    await broker.publish(f"redbox/kiosk/{request.get('sourceId') or client.client_id}/command",
                         json.dumps(msg).encode(), 1)


async def _market(kiosk_id):
    row = await db.fetchrow("SELECT market FROM kiosks WHERE kiosk_id=%s", int(kiosk_id))
    return row and row["market"]


# ------------------------------------------------------------------------------------------- rules

async def handle_rest_call(client, req):
    cmd, ver = req.get("command"), int(req.get("version") or 1)
    payload = req.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            pass
    kiosk_id = int(req.get("sourceId") or client.client_id or 0)
    await db.touch_kiosk(kiosk_id)
    await db.record_event(kiosk_id, "iot", cmd, payload if cmd not in ("GetConfigChanges",) else {"request": payload})
    market = await _market(kiosk_id)

    if cmd in ("BrokerRegister", "BrokerUnRegister"):
        registered = cmd == "BrokerRegister"
        await db.execute(
            "INSERT INTO iot_registrations (kiosk_id, broker_registered, broker_reason, registered_at) VALUES (%s,%s,%s,now())"
            " ON CONFLICT (kiosk_id) DO UPDATE SET broker_registered=EXCLUDED.broker_registered,"
            " broker_reason=EXCLUDED.broker_reason, registered_at=now(), updated_at=now()",
            kiosk_id, registered, (payload or {}).get("Reason") if isinstance(payload, dict) else None)
        await reply(client, req, 200)
    elif cmd == "SyncTimestampAndTimezone":
        tz = await db.fetchval("SELECT timezone FROM kiosks WHERE kiosk_id=%s", kiosk_id)
        await send(kiosk_id, "SyncTimestampAndTimezone", {"UtcTimestamp": _now_iso(),
                                                          "Timezone": tz or "Eastern Standard Time"}, 1)
    elif cmd == "GetPresignedS3Url":
        key = (payload or {}).get("Key", "")
        ptype = (payload or {}).get("Type", 0)
        ptype = {"None": 0, "DownloadFile": 1, "ActivationScript": 2, "FileSet": 3}.get(ptype, ptype)
        if ptype == 1:
            key = f"downloadfiles/{key}/file"
        elif ptype == 2:
            key = f"downloadfiles/{key}/script"
        url = s3.presign(key, "HEAD" if (payload or {}).get("IsHead") else "GET", ttl=7200)
        await reply(client, req, raw_payload=url)
    elif cmd == "ReportFileSetVersions":
        reports = []
        for r in (payload or {}).get("FileSetPollRequests") or []:
            state = r.get("FileSetState", 0)
            state = {"Active": 0, "InProgress": 1, "NeedsDependency": 2, "Error": 3}.get(state, state)
            reports.append((r.get("FileSetId"), r.get("FileSetRevisionId"), state))
        change_sets = await filesets.process_report(kiosk_id, market, reports, "ucs")
        await reply(client, req, 200, change_sets)
    elif cmd == "DownloadFileJobExecutionStatusUpdate":
        p = payload or {}
        await db.execute("INSERT INTO iot_download_job_status (job_id, kiosk_id, status, payload) VALUES (%s,%s,%s,%s)"
                         " ON CONFLICT (job_id, kiosk_id) DO UPDATE SET status=EXCLUDED.status, payload=EXCLUDED.payload,"
                         " updated_at=now()", str(p.get("DownloadFileJobId")), kiosk_id, str(p.get("Status")), db.jsonb(p))
        await reply(client, req, 200)
    elif cmd == "GetConfigChanges":
        await reply(client, req, 200, await kioskconfig.changes_response(payload or {}, market))
    elif cmd == "ActivateConfigVersion":
        await kioskconfig.activated(payload or {})
        await reply(client, req, 200)
    elif cmd == "GetConfigStatus":
        await reply(client, req, 200, await kioskconfig.status(kiosk_id))
    elif cmd == "GetKioskSegments":
        await reply(client, req, 200, await kioskconfig.segments(kiosk_id, market))
    elif cmd == "GetRecordChanges":
        await reply(client, req, 200, await record_changes(payload or {}))
    else:
        log.warning("unhandled rest call %s v%s from %s", cmd, ver, kiosk_id)
        await reply(client, req, 404)


async def record_changes(request):
    """GetRecordChanges (profile product data). Answers from profile_products."""
    responses = []
    ids = [int(r.get("Id")) for r in request.get("RecordRequests") or [] if r.get("Id") is not None]
    hashes = {int(r.get("Id")): r.get("Hash") for r in request.get("RecordRequests") or [] if r.get("Id") is not None}
    found = {}
    try:
        for row in await db.fetch("SELECT product_id, data FROM profile_products WHERE product_id = ANY(%s)", ids):
            found[int(row["product_id"])] = row["data"]
    except Exception as e:
        log.warning("GetRecordChanges lookup failed: %s", e)
    for pid in ids:
        data = found.get(pid)
        if data is None:
            responses.append({"Id": pid, "RecordStatus": 3, "Data": None, "Hash": None})
            continue
        text = json.dumps(data, separators=(",", ":"))
        h = base64.b64encode(hashlib.sha1(text.encode()).digest()).decode()
        responses.append({"Id": pid, "RecordStatus": 0 if hashes.get(pid) == h else 1,
                          "Data": None if hashes.get(pid) == h else text, "Hash": h})
    return {"RequestId": request.get("RequestId"), "TableName": request.get("TableName", 1),
            "RecordResponses": responses, "PageNumber": None, "PageCount": None}


async def on_rule(client, rule, topic, payload):
    try:
        msg = json.loads(payload.decode("utf-8"))
    except Exception:
        log.warning("rule %s: unparsable payload from %s", rule, client.client_id)
        return
    kiosk_id = msg.get("KioskId") or msg.get("sourceId") or client.client_id
    if rule == "kioskucsregister":
        await db.touch_kiosk(kiosk_id, ucs_version=msg.get("AssemblyVersion"))
        await db.execute("INSERT INTO iot_registrations (kiosk_id, ucs_version, assembly_version) VALUES (%s,%s,%s)"
                         " ON CONFLICT (kiosk_id) DO UPDATE SET ucs_version=EXCLUDED.ucs_version,"
                         " assembly_version=EXCLUDED.assembly_version, updated_at=now()",
                         int(kiosk_id), msg.get("Version"), msg.get("AssemblyVersion"))
        await db.record_event(kiosk_id, "iot", "ucsregister", msg)
    elif rule == "kioskping":
        await db.touch_kiosk(kiosk_id)
        await db.execute("INSERT INTO iot_registrations (kiosk_id, last_ping_at, last_ping) VALUES (%s, now(), %s)"
                         " ON CONFLICT (kiosk_id) DO UPDATE SET last_ping_at=now(), last_ping=EXCLUDED.last_ping",
                         int(kiosk_id), db.jsonb(msg.get("payload")))
    elif rule == "kioskrestcall":
        await handle_rest_call(client, msg)
    else:
        await db.record_event(kiosk_id, "iot", f"rule:{rule}", msg)


async def on_publish(client, topic, payload):
    if not topic.startswith("redbox/updateservice-instance/"):
        return
    try:
        msg = json.loads(payload.decode("utf-8"))
    except Exception:
        return
    rid = msg.get("requestId")
    body = msg.get("payload")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            pass
    await db.execute("UPDATE iot_requests SET response=%s, responded_at=now() WHERE request_id=%s", db.jsonb(body), rid)
    fut = _waiters.get(rid)
    if fut and not fut.done():
        fut.set_result(body)


async def authorize(client_id, cert_cn, der):
    if not config.MQTT_REQUIRE_CLIENT_CERT:
        return True
    if der is None or cert_cn != client_id:
        return False
    fingerprint = hashlib.sha256(der).hexdigest()
    row = await db.fetchrow("SELECT revoked_at FROM iot_certs WHERE certificate_id=%s", fingerprint)
    return bool(row and row["revoked_at"] is None)


async def on_connect(client):
    await db.touch_kiosk(client.client_id)
    await db.record_event(client.client_id, "iot", "connected", {"peer": str(client.peer)})


async def on_disconnect(client):
    await db.record_event(client.client_id, "iot", "disconnected", None)


# ---------------------------------------------------------------------------------------- admin

def admin_app():
    app = FastAPI(title="IoT operator API")

    @app.get("/clients")
    async def clients():
        return [{"clientId": c.client_id, "peer": str(c.peer), "subscriptions": list(c.subscriptions)}
                for c in broker.clients.values()]

    @app.post("/send")
    async def post_send(body: dict):
        return await send(body.get("target", "all"), body["command"], body.get("payload"),
                          int(body.get("version") or 1), float(body.get("wait") or 0))

    @app.post("/push/config")
    async def push_config(body: dict):
        target = body.get("kioskId") or (f"market:{body['market']}" if body.get("market") else "all")
        return await send(target, "TriggerGetConfigChanges", {"ExecutionTimeFrameMs": int(body.get("jitterMs") or 0)}, 2)

    @app.post("/push/deploy")
    async def push_deploy(body: dict):
        dep = await db.fetchrow("SELECT * FROM fs_deployments WHERE id=%s", int(body["deploymentId"]))
        if dep is None:
            raise HTTPException(404)
        target = dep["target"] if dep["target_type"] == "kiosk" else (
            f"market:{dep['target']}" if dep["target_type"] == "market" else "all")
        return await send(target, "TriggerReportFileSetVersions", {"ExecutionTimeFrameMs": 0}, 1)

    @app.post("/download-job")
    async def download_job(body: dict):
        job_id = str(uuid.uuid4())
        if body.get("fileB64"):
            filesets.s3_put(f"downloadfiles/{job_id}/file", base64.b64decode(body["fileB64"]))
        if body.get("scriptB64"):
            filesets.s3_put(f"downloadfiles/{job_id}/script", base64.b64decode(body["scriptB64"]))
        meta = {"FileKey": job_id, "Name": body.get("name", job_id), "FileName": body.get("fileName"),
                "DestinationPath": body.get("destinationPath"), "ActivationScriptName": body.get("scriptName")}
        target = str(body.get("target", "all"))
        await db.execute("INSERT INTO iot_download_jobs (job_id, file_key, metadata, target) VALUES (%s,%s,%s,%s)",
                         job_id, job_id, db.jsonb(meta), target)
        now = dotnet.utcnow()
        job = {"DownloadFileJobId": job_id, "FileKey": job_id, "Metadata": meta,
               "StartDateUtc": now.isoformat(), "StartTime": "00:00:00", "EndTime": "23:59:59",
               "CreatedOnUtc": now.isoformat(),
               "PresignedFileUrl": s3.presign(f"downloadfiles/{job_id}/file") if body.get("fileB64") else None,
               "PresignedScriptUrl": s3.presign(f"downloadfiles/{job_id}/script") if body.get("scriptB64") else None,
               "TargetKiosks": [int(target)] if target.isdigit() else [], "BitsJobId": str(uuid.uuid4())}
        return await send(target, "ExecuteDownloadFileJob", job, 1)

    @app.post("/kiosk/{kiosk_id}/peek")
    async def peek(kiosk_id: int, body: dict):
        req = {"fileType": body.get("fileType", "UpdateClientServiceLogs")}
        if body.get("path"):
            req["fileQuery"] = {"path": body["path"], "searchPattern": body.get("searchPattern", "*"),
                                "searchOptions": None}
        return await send(kiosk_id, "PeekFiles", req, 2, wait=float(body.get("wait") or 30))

    @app.post("/kiosk/{kiosk_id}/upload")
    async def upload(kiosk_id: int, body: dict):
        key = f"uploads/{kiosk_id}/{dt.datetime.utcnow():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}" + (
            ".zip" if body.get("zip", True) else "")
        req = {"KioskId": kiosk_id, "S3PreSignedUrl": s3.presign(key, "PUT", ttl=3600),
               "FileType": body.get("fileType", "UpdateClientServiceLogs"), "FileNames": body.get("fileNames", []),
               "ZipFiles": bool(body.get("zip", True)), "BasePath": body.get("basePath")}
        res = await send(kiosk_id, "UploadFiles", req, 2, wait=float(body.get("wait") or 60))
        res["s3Key"] = key
        return res

    @app.get("/requests/{request_id}")
    async def get_request(request_id: str):
        return await db.fetchrow("SELECT * FROM iot_requests WHERE request_id=%s", request_id)

    @app.get("/_health")
    async def health():
        return {"ok": True, "clients": len(broker.clients)}

    return app


async def run():
    global broker
    await db.init()
    certs.ensure_all()
    await kioskconfig.seed_defaults()
    broker = mqtt.Broker(on_rule=on_rule, on_publish=on_publish, on_connect=on_connect,
                         on_disconnect=on_disconnect, authorize=authorize)
    ctx = mqtt.server_context(os.path.join(config.CERT_DIR, "server.crt"), os.path.join(config.CERT_DIR, "server.key"),
                              os.path.join(config.CERT_DIR, "ca.crt"), config.MQTT_REQUIRE_CLIENT_CERT)
    servers = [await broker.serve("0.0.0.0", config.MQTT_PORT, ctx)]
    if config.MQTT_PLAIN_PORT:
        servers.append(await broker.serve("0.0.0.0", config.MQTT_PLAIN_PORT, None))
    http = uvicorn.Server(uvicorn.Config(admin_app(), host="0.0.0.0", port=config.IOT_ADMIN_PORT,
                                         log_level="warning", access_log=False))
    await asyncio.gather(http.serve(), *(s.serve_forever() for s in servers))


def main(argv):
    asyncio.run(run())
