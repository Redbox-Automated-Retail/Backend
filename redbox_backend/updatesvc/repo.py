"""Repositories of the legacy Update Service: import, publishing, change sets and snapshots.

Data model (docs/systems/update-manager.md §5.2, §5.4, §7): a repository is a linear list of revisions;
each revision lists changed files (`TargetPath`, `TargetName`, `VersionHash` = SHA-1 of the file after the
revision). Content is a seed (the whole file) or a VCDIFF delta against the previous version, or nothing
(`Composite` placeholder). On the kiosk every content file is named
`<revision hash>-<UPPERHEX(SHA1(ASCII(TargetPath + "\\" + TargetName)))>.patch`.

Change sets (`u p` replies):
  * the kiosk's head is one of our revisions -> the later revisions verbatim, as a tar of .patch files;
  * otherwise (new kiosk, reset repository, unknown head) -> a *snapshot*: one revision (our head) whose
    items are seeds of every file at its head version. The snapshot is computed offline by
    `admin import-store` / `admin snapshot` (xdelta3 reconstruction, every step SHA-1 checked).
"""
import hashlib
import io
import json
import logging
import os
import pathlib
import shutil
import subprocess
import tarfile
import tempfile
import uuid

from .. import blobs, config, db

log = logging.getLogger("repo")


def path_hash(target_path, target_name):
    joined = target_path.rstrip("\\") + "\\" + target_name if target_path else target_name
    return hashlib.sha1(joined.encode("ascii", "replace")).hexdigest().upper()


def patch_name(rev_hash, target_path, target_name):
    return f"{rev_hash}-{path_hash(target_path, target_name)}.patch"


def _read_json(p):
    return json.loads(pathlib.Path(p).read_text(encoding="utf-8-sig"))


# --------------------------------------------------------------------------------------------- import

async def import_repository(repo_dir, name=None, replace=False):
    """Import one `.store/.repository/<name>` directory (revlog + .patch files)."""
    repo_dir = pathlib.Path(repo_dir)
    name = name or repo_dir.name
    revlog_dir = repo_dir / ".revlog"
    if revlog_dir.is_file():  # legacy single-file PersistentList
        entries = _read_json(revlog_dir)
    else:
        files = sorted((p for p in revlog_dir.iterdir() if p.name.isdigit()), key=lambda p: int(p.name))
        entries = [_read_json(p) for p in files]
    if replace:
        await db.execute("DELETE FROM upd_repositories WHERE name = %s", name)
    await db.execute("INSERT INTO upd_repositories (name) VALUES (%s) ON CONFLICT DO NOTHING", name)
    existing = {r["hash"]: r["seq"] for r in await db.fetch("SELECT hash, seq FROM upd_revisions WHERE repo=%s", name)}
    next_seq = (max(existing.values()) + 1) if existing else 0
    stored = missing = 0
    for entry in entries:
        h = entry["Hash"].upper()
        if h in existing:
            continue
        await db.execute("INSERT INTO upd_revisions (repo, seq, hash, label) VALUES (%s,%s,%s,%s)",
                         name, next_seq, h, entry.get("Label"))
        next_seq += 1
        for idx, ch in enumerate(entry.get("Changes") or []):
            blob = None
            if not ch.get("Composite"):
                p = repo_dir / patch_name(h, ch["TargetPath"], ch["TargetName"])
                if p.is_file():
                    blob = blobs.put_file(p)
                    stored += 1
                else:
                    missing += 1
            await db.execute(
                "INSERT INTO upd_changes (repo, rev_hash, idx, target_name, target_path, version_hash, content_hash,"
                " is_seed, composite, blob) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                name, h, idx, ch["TargetName"], ch["TargetPath"], ch["VersionHash"].upper(),
                (ch.get("ContentHash") or "").upper() or None, bool(ch.get("IsSeed")), bool(ch.get("Composite")),
                blob)
    log.info("imported %s: %d revisions in log, %d content files stored, %d missing", name, len(entries),
             stored, missing)
    return {"repository": name, "revisions": len(entries), "stored": stored, "missing": missing}


# ----------------------------------------------------------------------------------- reconstruction

def xdelta_apply(source: bytes, patch: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as td:
        s, p, o = (os.path.join(td, n) for n in ("src", "patch", "out"))
        with open(s, "wb") as f:
            f.write(source)
        with open(p, "wb") as f:
            f.write(patch)
        res = subprocess.run(["xdelta3", "-f", "-d", "-s", s, p, o], capture_output=True)
        if res.returncode != 0 or res.stderr.strip():
            raise RuntimeError("xdelta3 failed: " + res.stderr.decode(errors="replace").strip())
        with open(o, "rb") as f:
            return f.read()


def xdelta_encode(source: bytes, target: bytes) -> bytes:
    with tempfile.TemporaryDirectory() as td:
        s, t, o = (os.path.join(td, n) for n in ("src", "tgt", "out"))
        with open(s, "wb") as f:
            f.write(source)
        with open(t, "wb") as f:
            f.write(target)
        # `-S none`: the kiosk's xdelta3.exe (3.0u) rejects the LZMA secondary compressor that newer
        # xdelta3 builds use by default ("unknown secondary compressor ID").
        res = subprocess.run(["xdelta3", "-f", "-e", "-S", "none", "-s", s, t, o], capture_output=True)
        if res.returncode != 0:
            raise RuntimeError("xdelta3 encode failed: " + res.stderr.decode(errors="replace").strip())
        with open(o, "rb") as f:
            return f.read()


async def _history(repo):
    revs = await db.fetch("SELECT seq, hash, label FROM upd_revisions WHERE repo=%s ORDER BY seq", repo)
    changes = await db.fetch("SELECT c.* FROM upd_changes c JOIN upd_revisions r ON r.repo=c.repo AND r.hash=c.rev_hash"
                             " WHERE c.repo=%s ORDER BY r.seq, c.idx", repo)
    by_rev = {}
    for c in changes:
        by_rev.setdefault(c["rev_hash"], []).append(c)
    return revs, by_rev


async def reconstruct_head(repo, upto_hash=None):
    """Return ({(path,name): (version_hash, blob)}, head_hash, problems) for the repository at a revision."""
    revs, by_rev = await _history(repo)
    if not revs:
        return {}, None, ["repository has no revisions"]
    if upto_hash:
        idx = next((i for i, r in enumerate(revs) if r["hash"] == upto_hash), None)
        if idx is None:
            raise ValueError(f"{repo}: unknown revision {upto_hash}")
        revs = revs[: idx + 1]
    head = revs[-1]["hash"]
    current = {}     # key -> (version_hash, bytes or None)
    latest = {}      # key -> latest version hash listed (placeholders included)
    problems = []
    for r in revs:
        for c in by_rev.get(r["hash"], []):
            key = (c["target_path"], c["target_name"])
            latest[key] = c["version_hash"]
            if c["composite"] or not c["blob"]:
                continue
            data = blobs.get_bytes(c["blob"]) if blobs.exists(c["blob"]) else None
            if data is None:
                problems.append(f"missing content for {key[1]} at {r['hash']}")
                current[key] = (c["version_hash"], None)
                continue
            if c["is_seed"]:
                content = data
            else:
                prev = current.get(key)
                if not prev or prev[1] is None:
                    current[key] = (c["version_hash"], None)
                    continue
                try:
                    content = xdelta_apply(prev[1], data)
                except RuntimeError as e:
                    problems.append(f"{key[1]} at {r['hash']}: {e}")
                    current[key] = (c["version_hash"], None)
                    continue
            if blobs.sha1_hex(content) != c["version_hash"]:
                problems.append(f"{key[1]} at {r['hash']}: SHA-1 mismatch after patch")
                current[key] = (c["version_hash"], None)
                continue
            current[key] = (c["version_hash"], content)
    result = {}
    for key, version in latest.items():
        have = current.get(key)
        if have and have[1] is not None and have[0] == version:
            result[key] = (version, blobs.put_bytes(have[1]))
        elif have is None:
            pass  # only ever a placeholder: installed by the image, not shipped
        else:
            problems.append(f"{key[1]}: head version {version} cannot be reconstructed")
    return result, head, problems


async def build_snapshot(repo):
    files, head, problems = await reconstruct_head(repo)
    if head is None:
        return {"repository": repo, "files": 0, "problems": problems}
    await db.execute("DELETE FROM upd_snapshots WHERE repo=%s", repo)
    for (tp, tn), (vh, blob) in files.items():
        await db.execute("INSERT INTO upd_snapshots (repo, head_hash, target_name, target_path, version_hash, blob)"
                         " VALUES (%s,%s,%s,%s,%s,%s)", repo, head, tn, tp, vh, blob)
    for p in problems[:20]:
        log.warning("%s snapshot: %s", repo, p)
    log.info("%s snapshot at %s: %d files, %d problems", repo, head, len(files), len(problems))
    return {"repository": repo, "head": head, "files": len(files), "problems": problems}


# ------------------------------------------------------------------------------------------ publish

async def publish_revision(repo, files, label=None, use_deltas=True):
    """Add a revision. `files` = list of (target_path, target_name, local_file). Unchanged files are skipped.

    Content is a VCDIFF delta against the head version when it is available and smaller, else a seed.
    Returns the new revision hash, or None when nothing changed.
    """
    await db.execute("INSERT INTO upd_repositories (name) VALUES (%s) ON CONFLICT DO NOTHING", repo)
    current, _, _ = await reconstruct_head(repo) if await db.fetchval(
        "SELECT count(*) FROM upd_revisions WHERE repo=%s", repo) else ({}, None, [])
    rev_hash = hashlib.sha1(uuid.uuid4().bytes).hexdigest().upper()
    items = []
    for tp, tn, local in files:
        data = pathlib.Path(local).read_bytes()
        vh = blobs.sha1_hex(data)
        prev = current.get((tp, tn))
        if prev and prev[0] == vh:
            continue
        seed_blob = blobs.put_bytes(data)
        item = {"tp": tp, "tn": tn, "vh": vh, "seed": True, "blob": seed_blob, "ch": vh}
        if use_deltas and prev and shutil.which("xdelta3"):
            delta = xdelta_encode(blobs.get_bytes(prev[1]), data)
            if len(delta) < len(data) // 2:
                item.update(seed=False, blob=blobs.put_bytes(delta), ch=blobs.sha1_hex(delta))
        items.append(item)
    if not items:
        return None
    seq = await db.fetchval("SELECT coalesce(max(seq), -1) + 1 FROM upd_revisions WHERE repo=%s", repo)
    await db.execute("INSERT INTO upd_revisions (repo, seq, hash, label) VALUES (%s,%s,%s,%s)", repo, seq, rev_hash, label)
    for idx, it in enumerate(items):
        await db.execute(
            "INSERT INTO upd_changes (repo, rev_hash, idx, target_name, target_path, version_hash, content_hash,"
            " is_seed, composite, blob) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,false,%s)",
            repo, rev_hash, idx, it["tn"], it["tp"], it["vh"], it["ch"], it["seed"], it["blob"])
    await build_snapshot(repo)
    log.info("published %s revision %s with %d files", repo, rev_hash, len(items))
    return rev_hash


# -------------------------------------------------------------------------------------- change sets

def changeset_dir():
    p = pathlib.Path(config.DATA_DIR) / "changesets"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_tar(target, members):
    """members: list of (archive name, blob key). Plain ustar, like SharpZipLib's TarArchive expects."""
    tmp = target.with_suffix(".tmp")
    with tarfile.open(tmp, "w", format=tarfile.USTAR_FORMAT) as tar:
        for name, key in members:
            info = tarfile.TarInfo(name)
            info.size = blobs.size(key)
            info.mode = 0o644
            info.mtime = 0
            with open(blobs.path(key), "rb") as f:
                tar.addfile(info, f)
    os.replace(tmp, target)


def _file_sha1(p):
    h = hashlib.sha1()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


async def change_set_for(repo, client_head):
    """Return a ChangeSet dict for this repository and kiosk head, or None when the kiosk is current."""
    revs, by_rev = await _history(repo)
    if not revs:
        return None
    head = revs[-1]["hash"]
    client_head = (client_head or "").upper()
    if client_head == head:
        return None
    index = next((i for i, r in enumerate(revs) if r["hash"] == client_head), None)
    if index is not None:
        selected = revs[index + 1:]
        items, members = [], []
        for r in selected:
            for c in by_rev.get(r["hash"], []):
                placeholder = c["composite"] or not c["blob"]
                items.append({"IsFirst": c["is_seed"], "Revision": r["hash"], "VersionHash": c["version_hash"],
                              "ContentHash": c["content_hash"] or c["version_hash"], "TargetName": c["target_name"],
                              "TargetPath": c["target_path"], "Composite": placeholder})
                if not placeholder:
                    members.append((patch_name(r["hash"], c["target_path"], c["target_name"]), c["blob"]))
        kind = f"{client_head[:8]}-{head[:8]}"
        revisions = [r["hash"] for r in selected]
        label = selected[-1]["label"]
    else:
        snap = await db.fetch("SELECT * FROM upd_snapshots WHERE repo=%s AND head_hash=%s ORDER BY target_path, target_name",
                              repo, head)
        if not snap:
            log.warning("%s: kiosk head %s unknown and no snapshot at %s; run `admin snapshot %s`",
                        repo, client_head or "(none)", head, repo)
            return None
        items = [{"IsFirst": True, "Revision": head, "VersionHash": s["version_hash"], "ContentHash": s["version_hash"],
                  "TargetName": s["target_name"], "TargetPath": s["target_path"], "Composite": False} for s in snap]
        members = [(patch_name(head, s["target_path"], s["target_name"]), s["blob"]) for s in snap]
        kind = f"seed-{head[:8]}"
        revisions = [head]
        label = revs[-1]["label"]
    target = changeset_dir() / repo / f"{kind}.tar"
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_tar(target, members)
    sha_file = target.with_suffix(".sha1")
    if not sha_file.exists():
        sha_file.write_text(_file_sha1(target))
    url = f"{config.UPDATE_CONTENT_BASE_URL.rstrip('/')}/changesets/{repo}/{target.name}"
    return {"Name": repo, "Head": head, "Revisions": revisions, "Label": label, "Url": url,
            "ContentHash": sha_file.read_text().strip(), "CompressionType": 0, "IsArchive": True, "Items": items}


async def repositories_for_store(number):
    rows = await db.fetch(
        "SELECT r.name FROM upd_repositories r"
        " LEFT JOIN upd_store_repos l ON l.repo = r.name AND l.number = %s"
        " WHERE (l.linked IS TRUE) OR (l.linked IS NULL AND r.auto_subscribe"
        "   AND NOT EXISTS (SELECT 1 FROM upd_store_repos x WHERE x.number = %s AND x.linked))"
        " ORDER BY r.name", number, number)
    return [r["name"] for r in rows]
