"""Our own builds of the kiosk software: publish, staged rollout, rollback, status (docs/releases.md).

A release is a zip built by the Software repository's `build/release.ps1`:
  release.json   {"Format": 1, "Component", "Version", "Kind": "repository"|"fileset", ...,
                  "Source": {"Repository", "Commit", "Ref"}, "Files": {"<relative path>": "<SHA-1>"}}
  files/<relative path>   the build output, relative to TargetRoot (repository) or Destination (file set)

Repository releases become an *unreleased* revision: no kiosk gets it until it is rolled out to a store, a
market or everyone. Only files the repository already has are published (matched case-insensitively and
written with the repository's own path spelling, because updatemgrd names patch files by the exact path),
so the rest of the build output (framework assemblies, .pdb files, ...) and the repository's own scripts and
settings stay as they are. New files need `NewFiles` in release.json or `allow_new`.

updatemgrd never moves back (software/docs/systems/update-manager.md 5.6), so a rollback of stores that
already took a revision publishes a *revert*: a new revision whose files are the older versions again.
"""
import json
import logging
import pathlib
import re
import tempfile
import zipfile

from .. import blobs, db, filesets
from . import repo as repo_mod

log = logging.getLogger("releases")

FORMAT = 1


class ReleaseError(Exception):
    pass


# ------------------------------------------------------------------------------------------ release zip

def read_release(zip_path):
    """Return (meta, {relative path: bytes}) after checking the zip against its own manifest."""
    try:
        z = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise ReleaseError(f"not a zip: {e}")
    with z:
        try:
            meta = json.loads(z.read("release.json").decode("utf-8-sig"))
        except KeyError:
            raise ReleaseError("release.json is missing")
        if meta.get("Format") != FORMAT:
            raise ReleaseError(f"unsupported release format {meta.get('Format')!r}")
        for key in ("Component", "Version", "Kind", "Files"):
            if not meta.get(key):
                raise ReleaseError(f"release.json has no {key}")
        if not re.fullmatch(r"[0-9A-Za-z.+_-]{1,64}", meta["Version"]):
            raise ReleaseError(f"bad version {meta['Version']!r}")
        files = {}
        for info in z.infolist():
            if info.is_dir() or not info.filename.startswith("files/"):
                continue
            rel = info.filename[len("files/"):]
            parts = rel.split("/")
            if not rel or any(p in ("", ".", "..") or ":" in p or "\\" in p for p in parts):
                raise ReleaseError(f"bad path in zip: {info.filename}")
            files[rel] = z.read(info)
    listed = {k.replace("\\", "/"): v.upper() for k, v in meta["Files"].items()}
    if set(listed) != set(files):
        raise ReleaseError(f"files/ and release.json differ: {sorted(set(listed) ^ set(files))[:10]}")
    for rel, data in files.items():
        if blobs.sha1_hex(data) != listed[rel]:
            raise ReleaseError(f"{rel}: SHA-1 does not match release.json")
    return meta, files


async def _known_paths(repo):
    """{lower-case 'path\\name': (target_path, target_name)} of every file the repository has ever had."""
    rows = await db.fetch("SELECT DISTINCT target_path, target_name FROM upd_changes WHERE repo=%s", repo)
    return {f"{r['target_path']}\\{r['target_name']}".lower(): (r["target_path"], r["target_name"]) for r in rows}


async def publish_release(zip_path, allow_new=False, actor=None):
    meta, files = read_release(zip_path)
    if meta["Kind"] == "fileset":
        return await _publish_fileset(meta, files, actor)
    if meta["Kind"] != "repository":
        raise ReleaseError(f"unknown Kind {meta['Kind']!r}")
    name, root = meta.get("Repository"), (meta.get("TargetRoot") or "").rstrip("\\")
    if not name or not re.fullmatch(r"[A-Za-z]:\\.*", root):
        raise ReleaseError("a repository release needs Repository and an absolute TargetRoot")
    if await db.fetchval("SELECT 1 FROM upd_revisions WHERE repo=%s AND version=%s", name, meta["Version"]):
        raise ReleaseError(f"{name} already has a revision with version {meta['Version']}; build a new version")
    known = await _known_paths(name)
    new_ok = {p.replace("\\", "/").lower() for p in meta.get("NewFiles") or []}
    items, ignored, added = [], [], []
    with tempfile.TemporaryDirectory() as td:
        for i, (rel, data) in enumerate(sorted(files.items())):
            parts = rel.split("/")
            tp = root + ("\\" + "\\".join(parts[:-1]) if len(parts) > 1 else "")
            key = known.get(f"{tp}\\{parts[-1]}".lower())
            if key is None:
                if not (allow_new or rel.lower() in new_ok):
                    ignored.append(rel)
                    continue
                key = (tp, parts[-1])
                added.append(rel)
            local = pathlib.Path(td, str(i))
            local.write_bytes(data)
            items.append((key[0], key[1], local))
        if not items:
            raise ReleaseError(f"none of the build's files is in {name}; wrong TargetRoot?")
        label = meta.get("Label") or f"{name}-{meta['Version']}"
        rev = await repo_mod.publish_revision(name, items, label=label, released=False, version=meta["Version"],
                                              build={k: v for k, v in meta.items() if k != "Files"})
    changed = rev and await db.fetchval("SELECT count(*) FROM upd_changes WHERE repo=%s AND rev_hash=%s", name, rev)
    result = {"repository": name, "revision": rev, "label": label, "version": meta["Version"],
              "files": len(items), "changed": changed or 0, "ignored": len(ignored), "added": added}
    if rev is None:
        result["note"] = "no file differs from the repository's newest revision; nothing published"
    await db.record_event(None, "releases", "publish", result | {"actor": actor, "source": meta.get("Source")})
    return result


async def _publish_fileset(meta, files, actor):
    fs = meta.get("FileSet") or {}
    dest = (meta.get("Destination") or "").rstrip("\\")
    if not fs.get("Id") or not fs.get("Name") or not re.fullmatch(r"[A-Za-z]:\\.*", dest):
        raise ReleaseError("a file-set release needs FileSet {Id, Name} and an absolute Destination")
    fs_id = int(fs["Id"])
    if await db.fetchval("SELECT 1 FROM fs_revisions WHERE fileset_id=%s AND version=%s", fs_id, meta["Version"]):
        raise ReleaseError(f"file set {fs_id} already has version {meta['Version']}; build a new version")
    await filesets.ensure_fileset(fs_id, fs["Name"])
    entries = [(dest + "\\" + rel.replace("/", "\\"), data) for rel, data in sorted(files.items())]
    rev = await filesets.create_revision(fs_id, f"{fs['Name']} {meta['Version']}", meta["Version"], entries,
                                         meta.get("Dependencies") or ())
    result = {"fileset": fs_id, "revision": rev, "version": meta["Version"], "files": len(entries)}
    await db.record_event(None, "releases", "publish", result | {"actor": actor, "source": meta.get("Source")})
    return result


# ------------------------------------------------------------------------------------------ rollout

async def resolve(repo, ref):
    """A revision by hash (or prefix), label or version; the newest match wins."""
    ref = (ref or "").strip()
    if not ref:
        raise ReleaseError("no revision given")
    prefix = ref.upper() + "%" if re.fullmatch(r"[0-9A-Fa-f]{6,40}", ref) else None
    row = await db.fetchrow(
        "SELECT * FROM upd_revisions WHERE repo=%s AND ((%s::text IS NOT NULL AND hash LIKE %s) OR label=%s OR version=%s)"
        " ORDER BY seq DESC LIMIT 1", repo, prefix, prefix, ref, ref)
    if row is None:
        raise ReleaseError(f"{repo}: no revision {ref}")
    return row


def _target(store=None, market=None):
    if store and market:
        raise ReleaseError("give a store or a market, not both")
    return ("store", str(store)) if store else ("market", market) if market else ("all", None)


async def _default_head(repo):
    return await repo_mod.served_head(repo)


async def _target_head(repo, ttype, target):
    """What the target is served now: its own rollout, else the default."""
    if ttype != "all":
        row = await db.fetchval("SELECT head FROM upd_rollouts WHERE repo=%s AND target_type=%s AND target=%s",
                                repo, ttype, target)
        if row:
            return row
    return await _default_head(repo)


async def _ensure_snapshot(repo, head):
    if not await db.fetchval("SELECT 1 FROM upd_snapshots WHERE repo=%s AND head_hash=%s LIMIT 1", repo, head):
        await repo_mod.build_snapshot(repo, head)


async def _set_target(repo, ttype, target, head):
    """Serve `head` to the target (None = back to the default for a store or market)."""
    if ttype == "all":
        seq = await db.fetchval("SELECT seq FROM upd_revisions WHERE repo=%s AND hash=%s", repo, head)
        await db.execute("UPDATE upd_revisions SET released=true WHERE repo=%s AND seq<=%s AND NOT released", repo, seq)
        await db.execute("UPDATE upd_repositories SET serve_head=%s WHERE name=%s", head, repo)
        # Narrower rollouts at or below it are now the default anyway; the newer ones stay ahead.
        await db.execute("DELETE FROM upd_rollouts o USING upd_revisions v WHERE o.repo=%s AND v.repo=o.repo"
                         " AND v.hash=o.head AND v.seq<=%s", repo, seq)
    elif head is None:
        await db.execute("DELETE FROM upd_rollouts WHERE repo=%s AND target_type=%s AND target=%s", repo, ttype, target)
    else:
        await db.execute("INSERT INTO upd_rollouts (repo, target_type, target, head) VALUES (%s,%s,%s,%s)"
                         " ON CONFLICT (repo, target_type, target) DO UPDATE SET head=EXCLUDED.head, created_at=now()",
                         repo, ttype, target, head)
    if head:
        await _ensure_snapshot(repo, head)


async def _log(repo, ttype, target, action, head, previous, actor):
    await db.execute("INSERT INTO upd_rollout_log (repo, target_type, target, action, head, previous, actor)"
                     " VALUES (%s,%s,%s,%s,%s,%s,%s)", repo, ttype, target, action, head, previous, actor)
    await db.record_event(int(target) if ttype == "store" and target.isdigit() else None, "releases", action,
                          {"repo": repo, "target": f"{ttype}:{target}" if target else ttype, "head": head,
                           "previous": previous, "actor": actor})


async def rollout(repo, ref, store=None, market=None, actor=None):
    """Serve a revision to one store, a market, or everyone (store=market=None)."""
    ttype, target = _target(store, market)
    rev = await resolve(repo, ref)
    previous = await _target_head(repo, ttype, target)
    await _set_target(repo, ttype, target, rev["hash"])
    await _log(repo, ttype, target, "rollout", rev["hash"], previous, actor)
    return {"repository": repo, "target": ttype, "name": target, "head": rev["hash"], "label": rev["label"],
            "previous": previous}


async def _stores_in(repo, ttype, target):
    """The stores whose served head the target decides (a store's own rollout beats its market's and the default)."""
    if ttype == "store":
        return [target]
    own = ("SELECT 1 FROM upd_rollouts o WHERE o.repo=%s AND o.target_type='store' AND o.target=s.number")
    if ttype == "market":
        return [r["number"] for r in await db.fetch(
            "SELECT s.number FROM upd_stores s JOIN kiosks k ON k.kiosk_id::text = s.number"
            f" WHERE k.market=%s AND NOT EXISTS ({own})", target, repo)]
    return [r["number"] for r in await db.fetch(
        "SELECT s.number FROM upd_stores s LEFT JOIN kiosks k ON k.kiosk_id::text = s.number"
        f" WHERE NOT EXISTS ({own}) AND NOT EXISTS (SELECT 1 FROM upd_rollouts m WHERE m.repo=%s"
        " AND m.target_type='market' AND m.target=k.market)", repo, repo)]


async def rollback(repo, store=None, market=None, to=None, actor=None):
    """Put a target back on an older revision.

    Stores of the target that already report a head after it get a revert revision (the older files published
    again on top of the history); when none has taken anything newer, the target is simply pointed back.
    """
    ttype, target = _target(store, market)
    current = await _target_head(repo, ttype, target)
    if to:
        dest = (await resolve(repo, to))["hash"]
    else:
        dest = await db.fetchval("SELECT previous FROM upd_rollout_log WHERE repo=%s AND target_type=%s"
                                 " AND target IS NOT DISTINCT FROM %s AND head=%s ORDER BY id DESC LIMIT 1",
                                 repo, ttype, target, current)
        if dest is None and ttype != "all":
            dest = await _default_head(repo)
        if dest is None:
            raise ReleaseError(f"{repo}: no earlier revision recorded for {ttype} {target or ''}; give --to")
    if dest == current:
        raise ReleaseError(f"{repo}: {ttype} {target or ''} is already served {dest}")
    seqs = {r["hash"]: r["seq"] for r in await db.fetch("SELECT hash, seq FROM upd_revisions WHERE repo=%s", repo)}
    stores = set(await _stores_in(repo, ttype, target))
    ahead = sorted(r["number"] for r in await db.fetch("SELECT number, head FROM upd_store_heads WHERE repo=%s", repo)
                   if r["number"] in stores and seqs.get(r["head"], -1) > seqs[dest])
    result = {"repository": repo, "target": ttype, "name": target, "from": current, "to": dest, "stores_ahead": ahead}
    if not ahead:
        head = None if ttype != "all" and dest == await _default_head(repo) else dest
        await _set_target(repo, ttype, target, head)
        await _log(repo, ttype, target, "rollback", head, current, actor)
        return result | {"head": dest, "revert": None}
    revert, leftovers = await publish_revert(repo, dest, released=ttype == "all")
    await _set_target(repo, ttype, target, revert)
    await _log(repo, ttype, target, "revert", revert, current, actor)
    return result | {"head": revert, "revert": revert, "not_removed": leftovers}


async def publish_revert(repo, dest, released=False):
    """A new revision that puts every file back to its version at `dest`. Returns (hash, files it cannot remove)."""
    old, _, problems = await repo_mod.reconstruct_head(repo, dest)
    if problems:
        raise ReleaseError(f"{repo}: cannot rebuild {dest}: {problems[:3]}")
    newest, newest_head, problems = await repo_mod.reconstruct_head(repo)
    if problems:
        raise ReleaseError(f"{repo}: cannot rebuild the newest revision: {problems[:3]}")
    items = [(tp, tn, blobs.path(blob)) for (tp, tn), (vh, blob) in sorted(old.items())
             if newest.get((tp, tn), (None,))[0] != vh]
    leftovers = sorted(f"{tp}\\{tn}" for (tp, tn) in newest if (tp, tn) not in old)
    row = await db.fetchrow("SELECT label, version FROM upd_revisions WHERE repo=%s AND hash=%s", repo, dest)
    if not items:
        return newest_head, leftovers   # the newest revision already has the old files
    rev = await repo_mod.publish_revision(
        repo, items, label=f"{row['label'] or dest[:8]}-revert", released=released, version=row["version"],
        build={"Revert": {"To": dest, "From": newest_head}})
    for f in leftovers:
        log.warning("%s revert to %s: %s was added later and stays on kiosks (updatemgrd cannot delete files)",
                    repo, dest, f)
    return rev, leftovers


# ------------------------------------------------------------------------------------------ status

async def status(repo, limit=10):
    revs = await db.fetch("SELECT seq, hash, label, version, released, origin, created_at FROM upd_revisions"
                          " WHERE repo=%s ORDER BY seq DESC LIMIT %s", repo, limit)
    by_hash = {r["hash"]: r for r in await db.fetch("SELECT seq, hash, label, version FROM upd_revisions"
                                                    " WHERE repo=%s", repo)}
    default = await _default_head(repo)
    rollouts = await db.fetch("SELECT target_type, target, head, created_at FROM upd_rollouts WHERE repo=%s"
                              " ORDER BY target_type, target", repo)
    stores = []
    for s in await db.fetch("SELECT s.number, s.last_check_in, k.market, h.head, h.reported_at FROM upd_stores s"
                            " LEFT JOIN kiosks k ON k.kiosk_id::text = s.number"
                            " LEFT JOIN upd_store_heads h ON h.number = s.number AND h.repo = %s ORDER BY s.number", repo):
        served = await repo_mod.served_head(repo, s["number"])
        have, want = by_hash.get(s["head"]), by_hash.get(served)
        if not s["head"]:
            state = "no report"
        elif s["head"] == served:
            state = "current"
        elif have is None:
            state = "unknown head"
        elif want and have["seq"] > want["seq"]:
            state = "ahead"
        else:
            state = "behind"
        stores.append({"store": s["number"], "market": s["market"], "reported": s["head"],
                       "reported_label": have and have["label"], "reported_version": have and have["version"],
                       "reported_at": s["reported_at"], "served": served, "served_label": want and want["label"],
                       "state": state})
    log_rows = await db.fetch("SELECT * FROM upd_rollout_log WHERE repo=%s ORDER BY id DESC LIMIT 10", repo)
    return {"repository": repo, "default_head": default, "revisions": revs, "rollouts": rollouts,
            "stores": stores, "log": log_rows}


async def fileset_status(fs_id):
    revs = await db.fetch("SELECT id, name, version, created_at FROM fs_revisions WHERE fileset_id=%s"
                          " ORDER BY id DESC LIMIT 10", fs_id)
    deployments = await db.fetch("SELECT * FROM fs_deployments WHERE fileset_id=%s ORDER BY id DESC LIMIT 10", fs_id)
    kiosks = await db.fetch("SELECT s.kiosk_id, s.channel, s.revision_id, r.version, s.state, s.updated_at"
                            " FROM fs_kiosk_state s LEFT JOIN fs_revisions r ON r.id = s.revision_id"
                            " WHERE s.fileset_id=%s ORDER BY s.kiosk_id, s.channel", fs_id)
    return {"fileset": fs_id, "revisions": revs, "deployments": deployments, "kiosks": kiosks}
