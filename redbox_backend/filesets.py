"""File-set revisions, their S3 objects and per-kiosk change sets.

Formats come from software/docs/systems/update-client.md §5.4, §7.5 and update-manager.md §5.11:
  * a revision zip holds `revision` (the ClientFileSetRevision manifest) and `revision.json` (info with a
    `RevisionId` key); file zips hold `<fileId>-<fileRev>` and `<fileId>-<fileRev>.json`
    (`{FileId, FileRevisionId, ...}`); a set zip holds every file pair. Data entries must not end in
    `.json`, because every `.json` entry is read as info.
  * S3 keys: `filesets/{fs}/Revision/{rev}-0[.legacy].zip`, `filesets/{fs}/File/{fileId}-{fileRev}-0.zip`
    (the Update Client builds this key itself), `filesets/{fs}/Set/{rev}-0.zip`.
  * Hashes: Update Client = Base64(SHA-1), updatemgrd = upper-case hex SHA-1 ("ucs" / "legacy" channel).
"""
import io
import json
import logging
import pathlib
import zipfile

from . import blobs, config, db, dotnet

log = logging.getLogger("filesets")

STATE_ACTIVE, STATE_INPROGRESS, STATE_NEEDSDEP, STATE_ERROR = 0, 1, 2, 3


def s3_root():
    p = pathlib.Path(config.DATA_DIR) / "s3"
    p.mkdir(parents=True, exist_ok=True)
    return p


def s3_put(key, data: bytes):
    target = s3_root() / key
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def _zip(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, data)
    return buf.getvalue()


def _hash(data_or_hex, channel, is_hex=False):
    hex_hash = data_or_hex if is_hex else blobs.sha1_hex(data_or_hex)
    return hex_hash if channel == "legacy" else blobs.hex_to_b64(hex_hash)


def file_key(fs_id, file_id, file_rev):
    return f"filesets/{fs_id}/File/{file_id}-{file_rev}-0.zip"


def revision_key(fs_id, rev_id, channel):
    return f"filesets/{fs_id}/Revision/{rev_id}-0{'.legacy' if channel == 'legacy' else ''}.zip"


def set_key(fs_id, rev_id):
    return f"filesets/{fs_id}/Set/{rev_id}-0.zip"


async def ensure_fileset(fs_id, name, retention_days=30, retention_revisions=3):
    await db.execute("INSERT INTO fs_filesets (id, name, retention_days, retention_revisions) VALUES (%s,%s,%s,%s)"
                     " ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name", fs_id, name, retention_days,
                     retention_revisions)


async def create_revision(fs_id, name, version, files, dependencies=()):
    """files: list of (destination path on the kiosk, bytes). Returns the new revision id."""
    rev_id = await db.fetchval("INSERT INTO fs_revisions (fileset_id, name, version) VALUES (%s,%s,%s) RETURNING id",
                               fs_id, name, version)
    set_entries = []
    for destination, data in files:
        fname = destination.replace("/", "\\").split("\\")[-1]
        file_id = await db.fetchval(
            "INSERT INTO fs_files (fileset_id, destination, name) VALUES (%s,%s,%s)"
            " ON CONFLICT (fileset_id, destination) DO UPDATE SET name = EXCLUDED.name RETURNING file_id",
            fs_id, destination, fname)
        content_blob = blobs.put_bytes(data)
        row = await db.fetchrow("SELECT * FROM fs_file_revisions WHERE file_id=%s AND content_blob=%s",
                                file_id, content_blob)
        if row is None:
            file_rev = await db.fetchval(
                "INSERT INTO fs_file_revisions (file_id, content_blob, content_size) VALUES (%s,%s,%s)"
                " RETURNING file_revision_id", file_id, content_blob, len(data))
            info = {"FileId": file_id, "FileRevisionId": file_rev, "Name": fname, "FileSize": len(data)}
            zdata = _zip([(f"{file_id}-{file_rev}", data), (f"{file_id}-{file_rev}.json", json.dumps(info))])
            zblob = blobs.put_bytes(zdata)
            s3_put(file_key(fs_id, file_id, file_rev), zdata)
            await db.execute("UPDATE fs_file_revisions SET zip_blob=%s, zip_size=%s WHERE file_revision_id=%s",
                             zblob, len(zdata), file_rev)
        else:
            file_rev = row["file_revision_id"]
        await db.execute("INSERT INTO fs_revision_files (revision_id, file_revision_id) VALUES (%s,%s)"
                         " ON CONFLICT DO NOTHING", rev_id, file_rev)
        info = {"FileId": file_id, "FileRevisionId": file_rev, "Name": fname, "FileSize": len(data)}
        set_entries += [(f"{file_id}-{file_rev}", data), (f"{file_id}-{file_rev}.json", json.dumps(info))]
    sdata = _zip(set_entries)
    s3_put(set_key(fs_id, rev_id), sdata)
    await db.execute("UPDATE fs_revisions SET set_blob=%s, set_size=%s WHERE id=%s", blobs.put_bytes(sdata),
                     len(sdata), rev_id)
    for dep in dependencies:
        await db.execute("INSERT INTO fs_dependencies (revision_id, depends_on_fileset, dependency_type,"
                         " minimum_version, maximum_version) VALUES (%s,%s,%s,%s,%s)", rev_id,
                         dep["DependsOnFileSetId"], dep.get("DependencyType", 0), dep.get("MinimumVersion"),
                         dep.get("MaximumVersion"))
    log.info("file set %s revision %s (%s) with %d files", fs_id, rev_id, version, len(files))
    return rev_id


async def manifest(rev_id, channel):
    rev = await db.fetchrow("SELECT r.*, f.name AS fs_name, f.retention_days, f.retention_revisions FROM fs_revisions r"
                            " JOIN fs_filesets f ON f.id = r.fileset_id WHERE r.id=%s", rev_id)
    if rev is None:
        return None
    rows = await db.fetch(
        "SELECT fr.*, f.destination, f.name FROM fs_revision_files rf"
        " JOIN fs_file_revisions fr ON fr.file_revision_id = rf.file_revision_id"
        " JOIN fs_files f ON f.file_id = fr.file_id WHERE rf.revision_id=%s ORDER BY f.file_id", rev_id)
    fs_id = rev["fileset_id"]
    files = []
    for r in rows:
        files.append({"FileId": r["file_id"], "Name": r["name"], "FileDestination": r["destination"],
                      "FileRevisionId": r["file_revision_id"],
                      "ContentHash": _hash(r["content_blob"], channel, is_hex=True),
                      "Path": file_key(fs_id, r["file_id"], r["file_revision_id"]), "CompressionType": 2,
                      "FileHash": _hash(r["zip_blob"], channel, is_hex=True), "BlobId": r["file_revision_id"],
                      "ContentSize": r["content_size"], "FileSize": r["zip_size"]})
    deps = await db.fetch("SELECT * FROM fs_dependencies WHERE revision_id=%s ORDER BY id", rev_id)
    return {"FileSetId": fs_id, "FileSetName": rev["fs_name"], "RetentionDays": rev["retention_days"],
            "RetentionRevisions": rev["retention_revisions"], "RevisionId": rev_id, "RevisionName": rev["name"],
            "RevisionVersion": rev["version"], "PatchRevisionId": 0,
            "SetPath": set_key(fs_id, rev_id), "SetCompressionType": 2,
            "SetFileHash": _hash(rev["set_blob"], channel, is_hex=True), "SetFileSize": rev["set_size"],
            "PatchSetPath": "", "PatchSetCompressionType": 0, "PatchSetFileHash": "", "PatchSetFileSize": 0,
            "Files": files, "PatchFiles": [],
            "Dependencies": [{"FileSetRevisionDependencyId": d["id"], "DependsOnFileSetId": d["depends_on_fileset"],
                              "DependencyType": d["dependency_type"], "MinimumVersion": d["minimum_version"],
                              "MaximumVersion": d["maximum_version"]} for d in deps]}


async def revision_zip(rev_id, channel):
    """Write (once) and describe the revision zip: (s3 key, file hash, file size, content hash, content size)."""
    m = await manifest(rev_id, channel)
    content = json.dumps(m).encode()
    info = json.dumps({"FileSetId": m["FileSetId"], "RevisionId": rev_id, "PatchRevisionId": 0,
                       "RevisionVersion": m["RevisionVersion"]})
    zdata = _zip([("revision", content), ("revision.json", info)])
    key = revision_key(m["FileSetId"], rev_id, channel)
    target = s3_root() / key
    if not target.exists() or target.read_bytes() != zdata:
        s3_put(key, zdata)
    return key, _hash(zdata, channel), len(zdata), _hash(content, channel), len(content)


def _date(value, channel):
    return dotnet.redbox_datetime(value) if channel == "legacy" else dotnet.iso(value)


async def change_set(deployment, channel, action=1):
    rev_id = deployment["revision_id"]
    base = {"FileSetId": deployment["fileset_id"], "RevisionId": rev_id or 0, "PatchRevisionId": 0}
    if action == 2 or rev_id is None:
        return {**base, "Action": 2, "Path": "", "CompressionType": 0, "ContentHash": "", "FileHash": "",
                "ContentSize": 0, "FileSize": 0, "DownloadOn": _date(None, channel), "DownloadPriority": 1,
                "ActiveOn": _date(None, channel), "ActivateStartTime": "", "ActivateEndTime": "", "DownloadUrl": ""}
    key, fhash, fsize, chash, csize = await revision_zip(rev_id, channel)
    download_url = config.UPDATE_CONTENT_BASE_URL.rstrip("/") + "/s3" if channel == "legacy" else config.S3_BASE_URL
    return {**base, "Action": 1, "Path": key, "CompressionType": 2, "ContentHash": chash, "FileHash": fhash,
            "ContentSize": csize, "FileSize": fsize, "DownloadOn": _date(deployment["created_at"], channel),
            "DownloadPriority": deployment["download_priority"], "ActiveOn": _date(deployment["active_on"], channel),
            "ActivateStartTime": deployment["activate_start"] or "", "ActivateEndTime": deployment["activate_end"] or "",
            "DownloadUrl": download_url}


async def desired_deployments(kiosk_id, market, channel):
    """Latest deployment per file set for this kiosk: kiosk-specific beats market beats all."""
    rows = await db.fetch(
        "SELECT DISTINCT ON (fileset_id) * FROM fs_deployments WHERE channel=%s AND ("
        " target_type='all' OR (target_type='market' AND target=%s) OR (target_type='kiosk' AND target=%s))"
        " ORDER BY fileset_id, CASE target_type WHEN 'kiosk' THEN 0 WHEN 'market' THEN 1 ELSE 2 END, created_at DESC",
        channel, market or "", str(kiosk_id))
    return {r["fileset_id"]: r for r in rows}


async def process_report(kiosk_id, market, reports, channel):
    """reports: list of (fileset_id, revision_id, state). Records them and returns the change sets to send."""
    reported = {}
    for fs_id, rev_id, state in reports:
        reported[int(fs_id)] = (int(rev_id), int(state))
        await db.execute(
            "INSERT INTO fs_kiosk_state (kiosk_id, fileset_id, channel, revision_id, state) VALUES (%s,%s,%s,%s,%s)"
            " ON CONFLICT (kiosk_id, fileset_id, channel) DO UPDATE SET revision_id=EXCLUDED.revision_id,"
            " state=EXCLUDED.state, updated_at=now()", int(kiosk_id), int(fs_id), channel, int(rev_id), int(state))
    out = []
    for fs_id, dep in (await desired_deployments(kiosk_id, market, channel)).items():
        have = reported.get(fs_id)
        if dep["revision_id"] is None:
            if have is not None:
                out.append(await change_set(dep, channel, action=2))
            continue
        if have and have[0] == dep["revision_id"]:
            if have[1] in (STATE_ACTIVE, STATE_INPROGRESS):
                continue
            # Error / NeedsDependency: retry only when the deployment was re-issued after the failure.
            st = await db.fetchrow("SELECT updated_at FROM fs_kiosk_state WHERE kiosk_id=%s AND fileset_id=%s"
                                   " AND channel=%s", int(kiosk_id), fs_id, channel)
            if st and st["updated_at"] > dep["created_at"]:
                continue
        out.append(await change_set(dep, channel))
    return out


async def deploy(fs_id, rev_id, target_type="all", target=None, channel="ucs", active_on=None,
                 activate_start=None, activate_end=None, priority=1):
    return await db.fetchrow(
        "INSERT INTO fs_deployments (fileset_id, revision_id, target_type, target, channel, active_on,"
        " activate_start, activate_end, download_priority) VALUES (%s,%s,%s,%s,%s,coalesce(%s, now()),%s,%s,%s)"
        " RETURNING *", fs_id, rev_id, target_type, target, channel, active_on, activate_start, activate_end, priority)
