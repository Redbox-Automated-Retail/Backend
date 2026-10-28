"""Update Service commands used by the kiosk (software/docs/systems/update-manager.md §6.3-§6.6).

Wire client: software/src/UpdateManager/Redbox.UpdateService.Client/UpdateService.cs.
  u p  n:'<store>' c:'{repo: head}' h:'<hash>'           -> line 0 = [ChangeSet], line 1 = Store (if any)
  u pr n:'<store>' v:'2' c:'[PollRequestDTO]' h:'<hash>' -> one PollReplyDTO per line
  installer start hash:'..' version:'..' h:'..'          -> JSON object of strings
  installer log guid:'..' name:'..' value:'..' h:'..'    -> success only
  subscription status|publish name:'..'
  repository link-to-store|remove-link-to-store|link-to-group|remove-link-to-group ...
All JSON goes out single-line and ASCII-only (the client decodes with Encoding.ASCII).
"""
import base64
import hashlib
import json
import logging
import uuid
import zlib

from .. import blobs, config, db, dotnet, filesets
from . import repo
from .rcp import CommandError

log = logging.getLogger("updatesvc")

# PollRequestType / PollReplyType (software/src/UpdateManager/Redbox.UpdateService.Model)
REQ_CONFIGFILES, REQ_STOREINFO, REQ_WORKRESULT, REQ_STATUS, REQ_TRANSFERSTAT, REQ_STOREFILES, REQ_FILESETS = 2, 3, 4, 5, 6, 8, 9
REP_UPDATECONFIG, REP_CONFIGCHANGESET, REP_STOREINFOCHANGESET, REP_UPDATESTOREINFO = 3, 4, 5, 6
REP_DOWNLOADFILE, REP_STOREFILECHANGESET, REP_FILESETCHANGESET = 8, 10, 11


def dumps(obj):
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=True, default=str)


def credential_hash(ident, key1, key2):
    return hashlib.sha1(f"{ident}-{key1}-{key2}".encode("ascii")).hexdigest().upper()


def check_credentials(ident, given):
    if not (config.UPDATE_SERVICE_KEY1 and config.UPDATE_SERVICE_KEY2):
        return True
    expected = credential_hash(ident, config.UPDATE_SERVICE_KEY1, config.UPDATE_SERVICE_KEY2)
    if (given or "").upper() != expected:
        log.warning("credential hash mismatch for %s", ident)
        return False
    return True


async def ensure_store(number):
    await db.execute("INSERT INTO upd_stores (number) VALUES (%s) ON CONFLICT DO NOTHING", number)
    await db.execute("UPDATE upd_stores SET last_check_in = now() WHERE number = %s", number)
    await db.touch_kiosk(number)
    return await db.fetchrow("SELECT * FROM upd_stores WHERE number = %s", number)


def store_json(store, repositories):
    return {"ID": store["id"], "Number": store["number"], "Group": {"ID": 1, "Reference": store["group_name"]},
            "MaxBandwidthWhileOutsideOfSchedule": store["max_bw_outside"],
            "MaxBandwidthWhileWithInSchedule": store["max_bw_within"],
            "StartOfScheduleInHoursFromMidnight": store["schedule_start"],
            "EndOfScheduleInHoursFromMidnight": store["schedule_end"],
            "AllowedBandwidthInMB": store["allowed_mb"], "NoProgressTimeout": store["no_progress_timeout"],
            "LastCheckIn": dotnet.redbox_datetime(store["last_check_in"]), "Enabled": store["enabled"],
            "Repositories": [{"ID": i + 1, "Reference": r} for i, r in enumerate(repositories)],
            "Tags": [], "Node": {"ID": 1, "Reference": config.IOT_INSTANCE_ID}}


# ------------------------------------------------------------------------------------------- u p

async def cmd_poll_repositories(params, reply):
    number = params.get("n", "").strip()
    if not number:
        raise CommandError("U100", "Store number (n:) is required.")
    if not check_credentials(number, params.get("h")):
        raise CommandError("U401", "Invalid credentials.", "Credential hash does not match.")
    try:
        heads = json.loads(params.get("c") or "{}")
    except json.JSONDecodeError as e:
        raise CommandError("U101", "Malformed repository head list.", str(e))
    store = await ensure_store(number)
    for name, head in heads.items():
        await db.execute("INSERT INTO upd_store_heads (number, repo, head) VALUES (%s,%s,%s)"
                         " ON CONFLICT (number, repo) DO UPDATE SET head=EXCLUDED.head, reported_at=now()",
                         number, name, (head or "").upper())
    change_sets = []
    repositories = await repo.repositories_for_store(number)
    if store["enabled"]:
        for name in repositories:
            cs = await repo.change_set_for(name, heads.get(name))
            if cs:
                change_sets.append(cs)
    reply.message(dumps(change_sets))
    if change_sets:
        reply.message(dumps(store_json(store, repositories)))
    await db.record_event(number, "updatesvc", "poll", {"heads": heads, "offered": [
        {"Name": c["Name"], "Head": c["Head"], "Revisions": len(c["Revisions"])} for c in change_sets]})


# ------------------------------------------------------------------------------------------ u pr

def _config_sync_id(rows):
    text = "|".join(f"{r['oid']}:{r['generation']}" for r in sorted(rows, key=lambda r: r["oid"]))
    return (zlib.crc32(text.encode()) & 0x7FFFFFFF) or 1


async def _config_files(number, req, out):
    rows = await db.fetch("SELECT * FROM upd_config_files ORDER BY oid")
    if not rows:
        return
    sync_id = _config_sync_id(rows)
    if req["SId"]:
        if req["SId"] != sync_id:
            out.append({"PRT": REP_UPDATECONFIG, "SId": 0, "D": None})
        return
    try:
        client = json.loads(req.get("D") or "[]")
    except json.JSONDecodeError:
        client = []
    have = {}
    for c in client:
        oid = int(c.get("Id") or 0)
        have[oid] = (c.get("SGId"), c.get("AGId"))
        await db.execute("INSERT INTO upd_store_config (number, oid, staged_gen, active_gen) VALUES (%s,%s,%s,%s)"
                         " ON CONFLICT (number, oid) DO UPDATE SET staged_gen=EXCLUDED.staged_gen,"
                         " active_gen=EXCLUDED.active_gen, reported_at=now()", number, oid, c.get("SGId"), c.get("AGId"))
    changes = []
    for r in rows:
        staged, active = have.get(r["oid"], (None, None))
        if staged == r["generation"] or (staged in (None, 0) and active == r["generation"]):
            continue
        changes.append({"OID": r["oid"], "Name": r["name"], "Path": r["path"], "GenerationOID": r["generation"],
                        "ConfigurationData": r["data"]})
    out.append({"PRT": REP_CONFIGCHANGESET, "SId": sync_id, "D": dumps(changes)})


async def _store_info(store, req, out):
    # The sync id tracks the store's time settings: when an admin changes them the kiosk is asked to
    # resend its full store info (UpdateStoreInfo), and the next reply carries the correction.
    sync_id = (zlib.crc32(f"{store['time_zone']}|{store['correct_time']}".encode()) & 0x7FFFFFFF) or 1
    if req["SId"]:
        if req["SId"] != sync_id:
            out.append({"PRT": REP_UPDATESTOREINFO, "SId": 0, "D": None})
        return
    info = {}
    if req.get("D"):
        try:
            info = json.loads(req["D"])
        except json.JSONDecodeError:
            pass
        await db.touch_kiosk(store["number"], info={"storeInfo": info})
    kiosk_time = dotnet.parse_redbox_datetime(info.get("UTCDateTime")) if info else None
    now = dotnet.utcnow()
    if kiosk_time is not None and kiosk_time.tzinfo is None:
        kiosk_time = kiosk_time.replace(tzinfo=now.tzinfo)
    correct_time = bool(store["correct_time"] and kiosk_time is not None
                        and abs((now - kiosk_time).total_seconds()) > 300)
    correct_tz = bool(store["time_zone"]) and store["time_zone"] != info.get("TimeZoneInfoString")
    out.append({"PRT": REP_STOREINFOCHANGESET, "SId": sync_id, "D": dumps({
        "TimeZoneInfoString": store["time_zone"] or info.get("TimeZoneInfoString"),
        "CorrectTimeZone": correct_tz, "CurrentUTCDateTime": dotnet.redbox_datetime(now),
        "CorrectDateTime": correct_time})})


async def _store_files(number, req, out):
    reported = {}
    for part in (req.get("D") or "").split("|"):
        bits = part.split(",")
        if len(bits) == 3 and bits[0].isdigit():
            reported[int(bits[0])] = (int(bits[1]), int(bits[2]))
    for f in await db.fetch("SELECT * FROM upd_store_files ORDER BY id"):
        have = reported.get(f["id"])
        if f["deleted"]:
            if have:
                out.append({"PRT": REP_STOREFILECHANGESET, "SId": 0, "D": dumps({
                    "StoreFile": f["id"], "Name": f["name"], "Path": f["path"], "StoreFileData": f["data_id"],
                    "SyncId": 0, "SyncHash": "", "DataHash": "", "Base64Data": "",
                    "ModifiedOn": dotnet.redbox_datetime(f["modified_on"]), "Action": 2})})
            continue
        if have and have[0] == f["data_id"]:
            continue
        data = blobs.get_bytes(f["blob"])
        out.append({"PRT": REP_STOREFILECHANGESET, "SId": f["data_id"], "D": dumps({
            "StoreFile": f["id"], "Name": f["name"], "Path": f["path"], "StoreFileData": f["data_id"],
            "SyncId": f["data_id"], "SyncHash": blobs.sha1_hex(data), "DataHash": blobs.sha1_hex(data),
            "Base64Data": base64.b64encode(data).decode(), "ModifiedOn": dotnet.redbox_datetime(f["modified_on"]),
            "Action": 1})})


async def _file_sets(number, req, out):
    reports = []
    for part in (req.get("D") or "").split("|"):
        bits = part.split(",")
        if len(bits) == 3 and all(b.strip().lstrip("-").isdigit() for b in bits):
            reports.append(tuple(int(b) for b in bits))
    kiosk = await db.fetchrow("SELECT market FROM kiosks WHERE kiosk_id=%s", int(number)) if number.isdigit() else None
    for cs in await filesets.process_report(int(number) if number.isdigit() else 0, kiosk and kiosk["market"],
                                            reports, "legacy"):
        out.append({"PRT": REP_FILESETCHANGESET, "SId": 0, "D": dumps(cs)})


async def _download_files(number, out):
    rows = await db.fetch(
        "SELECT d.* FROM upd_download_files d WHERE (d.number IS NULL OR d.number = %s) AND NOT EXISTS"
        " (SELECT 1 FROM upd_download_file_sent s WHERE s.download_id = d.id AND s.number = %s) ORDER BY d.id",
        number, number)
    for d in rows:
        url = f"{config.UPDATE_CONTENT_BASE_URL.rstrip('/')}/blobs/{d['blob']}" if d["blob"] else ""
        script = base64.b64encode(d["activate_script"].encode("utf-16-le")).decode() if d["activate_script"] else None
        out.append({"PRT": REP_DOWNLOADFILE, "SId": 0, "D": dumps({
            "Id": d["id"], "Name": d["name"], "DownloadFileType": d["type"], "StatusKey": f"downloadfile-{d['id']}",
            "FileName": d["file_name"], "DestinationPath": d["destination_path"], "FileKey": d["blob"] or "",
            "ActivateScript": script, "StartTime": d["start_time"], "EndTime": d["end_time"], "Url": url})})
        await db.execute("INSERT INTO upd_download_file_sent (download_id, number) VALUES (%s,%s) ON CONFLICT DO NOTHING",
                         d["id"], number)


def _decode_status(message):
    if message.get("Encode") and message.get("Data"):
        try:
            message = dict(message, Data=base64.b64decode(message["Data"]).decode("utf-16-le"))
        except Exception:
            pass
    return message


async def cmd_server_poll(params, reply):
    number = params.get("n", "").strip()
    if not number:
        raise CommandError("U100", "Store number (n:) is required.")
    if not check_credentials(number, params.get("h")):
        raise CommandError("U401", "Invalid credentials.", "Credential hash does not match.")
    try:
        requests = json.loads(params.get("c") or "[]")
    except json.JSONDecodeError as e:
        raise CommandError("U102", "Malformed poll request list.", str(e))
    store = await ensure_store(number)
    out, kinds = [], []
    for req in requests:
        prt = int(req.get("PRT") or 0)
        req["SId"] = int(req.get("SId") or 0)
        kinds.append(prt)
        if prt == REQ_CONFIGFILES:
            await _config_files(number, req, out)
        elif prt == REQ_STOREINFO:
            await _store_info(store, req, out)
        elif prt == REQ_STOREFILES:
            await _store_files(number, req, out)
        elif prt == REQ_FILESETS:
            await _file_sets(number, req, out)
        elif prt == REQ_STATUS:
            try:
                await db.record_event(number, "updatemgrd", "status", _decode_status(json.loads(req.get("D") or "{}")))
            except json.JSONDecodeError:
                await db.record_event(number, "updatemgrd", "status", {"raw": req.get("D")})
        elif prt == REQ_TRANSFERSTAT:
            await db.record_event(number, "updatemgrd", "transfer-statistic", json.loads(req.get("D") or "{}"))
        elif prt == REQ_WORKRESULT:
            await db.record_event(number, "updatemgrd", "work-result", {"raw": req.get("D")})
    if REQ_FILESETS not in kinds:
        await _file_sets(number, {"D": ""}, out)  # kiosk without any file set yet
    await _download_files(number, out)
    for r in out:
        reply.message(dumps(r))
    await db.record_event(number, "updatesvc", "serverpoll", {"requests": kinds, "replies": [r["PRT"] for r in out],
                                                             "version": params.get("v")})


# ------------------------------------------------------------------------------------- installer

async def cmd_installer(form, params, reply):
    """The store installer's first contact (installer-steps.lua in the 3.1.0.48 store installer).

    The script requires DataFileExpiration, InstallerExpiration, CurrentDateTime, URL, HostEntries and guid to be
    non-empty strings (Redbox.UpdateManager.Remoting.UpdateService.StartInstaller keeps only string values), treats
    expiration 0 as expired and >= 1 as a warning (so -1 = never expires), sets the clock from CurrentDateTime
    (DateTime.Parse, UTC) and only probes URL with a GET. HostEntries is pipe separated hosts-file lines; the
    script that wrote them to the hosts file is commented out in that installer.
    """
    if form == "start":
        guid = str(uuid.uuid4())
        edition = await db.fetchval("SELECT name FROM upd_editions WHERE heads->>'dvd-data' = %s ORDER BY edition_date"
                                    " DESC LIMIT 1", (params.get("hash") or "").upper())
        await db.execute("INSERT INTO upd_installer_sessions (guid, hash, version, log) VALUES (%s,%s,%s,%s)",
                         guid, params.get("hash"), params.get("version"),
                         db.jsonb([{"name": "data-file-edition", "value": edition}]))
        await db.record_event(None, "updatesvc", "installer-start", {"guid": guid, "hash": params.get("hash"),
                                                                     "version": params.get("version"), "edition": edition})
        now = dotnet.utcnow()
        hosts = config.INSTALLER_HOST_ENTRIES or "|".join(
            f"{config.BACKEND_PUBLIC_IP} {h}" for h in config.all_hostnames()
            if h != "localhost" and not h.startswith("rb-"))
        reply.message(dumps({"DataFileExpiration": "-1", "InstallerExpiration": "-1",
                             "CurrentDateTime": now.strftime("%Y-%m-%d %H:%M:%S"),
                             "URL": config.UPDATE_CONTENT_BASE_URL.rstrip("/") + "/_health", "HostEntries": hosts,
                             "guid": guid, "ErrorMessage": ""}))
    elif form == "log":
        await db.execute("UPDATE upd_installer_sessions SET log = log || %s WHERE guid = %s",
                         db.jsonb([{"name": params.get("name"), "value": params.get("value")}]), params.get("guid"))
        await db.record_event(None, "updatesvc", "installer-log", {"guid": params.get("guid"), "name": params.get("name"),
                                                                   "value": params.get("value")})
    else:
        raise CommandError("U404", f"Unknown installer form '{form}'.")


async def cmd_subscription(form, params, reply):
    name = params.get("name", "")
    if form == "status":
        reply.message(dumps({"ModifiedOn": dotnet.redbox_datetime(), "State": 1}))
    elif form == "publish":
        await db.record_event(None, "updatesvc", "subscription-publish", {"name": name})
    else:
        raise CommandError("U404", f"Unknown subscription form '{form}'.")


async def cmd_repository(form, params, reply):
    name, number = params.get("name"), params.get("number")
    if form in ("link-to-store", "remove-link-to-store"):
        await ensure_store(number)
        await db.execute("INSERT INTO upd_store_repos (number, repo, linked) VALUES (%s,%s,%s)"
                         " ON CONFLICT (number, repo) DO UPDATE SET linked = EXCLUDED.linked",
                         number, name, form == "link-to-store")
        store = await db.fetchrow("SELECT * FROM upd_stores WHERE number=%s", number)
        reply.message(dumps(store_json(store, await repo.repositories_for_store(number))))
    elif form in ("link-to-group", "remove-link-to-group"):
        reply.message(dumps({"ID": 1, "Name": params.get("groupname", "Default"), "Repositories": []}))
    else:
        raise CommandError("U404", f"Repository form '{form}' is an administrative command; use the admin CLI.")


async def handle(words, params, reply, peer):
    cmd, form = (words + ["", ""])[:2]
    if cmd == "u" and form == "p":
        await cmd_poll_repositories(params, reply)
    elif cmd == "u" and form == "pr":
        await cmd_server_poll(params, reply)
    elif cmd == "installer":
        await cmd_installer(form, params, reply)
    elif cmd == "subscription":
        await cmd_subscription(form, params, reply)
    elif cmd == "repository":
        await cmd_repository(form, params, reply)
    elif cmd == "interserver" and form == "ping" or cmd == "ping":
        reply.message("pong")
    elif cmd == "help":
        reply.message("u p | u pr | installer start|log | subscription status|publish | repository link-to-store ...")
    else:
        raise CommandError("U404", f"Unknown command '{' '.join(words)}'.")
    return True
