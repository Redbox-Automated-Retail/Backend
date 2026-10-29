"""Build and check installer seed zips (`data-<MMDDYYYY>.zip`) in the store format kiosks use.

A seed holds every repository of a kiosk's update store as the store installer hands it to updatemgrd
(software/docs/systems/update-manager.md §5.4, §7, §7.4). This is the layout of all 24 seeds from 2014-2024 in
the archive (docs/seed-zips.md has the details):

    <repo>/.active                 JSON string, 40 zeros (nothing activated yet)
    <repo>/.current                JSON string, 40 zeros (nothing staged yet)
    <repo>/<rev>-<SHA1(path)>.patch  content files, sorted by name
    <repo>/.revlog/<n>             one revision per file, oldest = 0; entries sorted as strings (0, 1, 10, 100, 11...)

Repositories are sorted by name. Each revlog file is compact JSON with the keys in this order:
`{"Hash","Label","Changes":[{"VersionHash","ContentHash","TargetName","TargetPath","IsSeed","Composite"}]}`.
Every revision of the history is kept. Only the *last* change of each file carries content: a seed (the whole
file, SHA-1 = VersionHash). Every earlier change is a placeholder (`Composite: true`, no content file). So a
fresh kiosk stages each file from its seed in one step. Revisions added on top of a base (`Addition`) may also
carry VCDIFF deltas against the base version (xdelta3 `-S none`, which the kiosk's xdelta3 3.0 can read).
updatemgrd applies those after the seed.

Zip entries: deflated, no data descriptors, DOS attributes (`create_system` 0, `external_attr` 0x20), version
made by 4.5 / needed 2.0, all stamped with the build time. These are the values in the real seeds.
"""
import asyncio
import datetime as dt
import hashlib
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import zipfile

from .. import blobs, db
from . import editions as editions_mod
from . import repo as repomod

ZERO = "0" * 40
STATE = json.dumps(ZERO).encode()          # b'"0000000000000000000000000000000000000000"'
CHANGE_KEYS = ("VersionHash", "ContentHash", "TargetName", "TargetPath", "IsSeed", "Composite")


class SeedError(Exception):
    pass


def default_name(date=None):
    d = date or dt.date.today()
    return f"data-{d:%m%d%Y}.zip"


def _sha1_file(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


# ---------------------------------------------------------------------------------------------- model

class Repo:
    """One repository of a seed: its revlog entries and the content file of every non-placeholder change.

    revisions: list of {"Hash", "Label", "Changes": [change dict with CHANGE_KEYS]}
    content:   {(rev hash, TargetPath, TargetName): ("blob", key) | ("file", path) | ("bytes", data)}
    head_files: {(TargetPath, TargetName): (version hash, content ref)} - the files at the head (for additions)
    """

    def __init__(self, name):
        self.name = name
        self.revisions = []
        self.content = {}
        self.head_files = {}

    @property
    def head(self):
        return self.revisions[-1]["Hash"] if self.revisions else None


def _change(version_hash, content_hash, name, path, is_seed, composite):
    return {"VersionHash": version_hash, "ContentHash": content_hash, "TargetName": name, "TargetPath": path,
            "IsSeed": bool(is_seed), "Composite": bool(composite)}


async def from_store(name, revision=None, edition=None, retarget=None, allow_placeholders=False):
    """A repository from the backend's store, as a seed: its history up to `revision` (hash prefix or label;
    default the served head, or the edition's head), with each file's last change re-seeded from the
    reconstructed file. `edition` takes the history of that imported edition instead of the served one."""
    revs, by_rev = await repomod._history(name, edition)
    if not revs:
        raise SeedError(f"{name}: no revisions" + (f" in edition {edition}" if edition else ""))
    if revision:
        r = revision.upper()
        idx = [i for i, x in enumerate(revs) if x["hash"].startswith(r) or x["label"] == revision]
        if not idx:
            raise SeedError(f"{name}: no revision or label {revision}")
        target = revs[idx[-1]]["hash"]
    elif edition:
        target = revs[-1]["hash"]
    else:
        target = await repomod.served_head(name)
    revs = revs[: next(i for i, x in enumerate(revs) if x["hash"] == target) + 1]
    files, head, problems = await repomod.reconstruct_head(name, upto_hash=target, edition=edition)
    if problems:
        raise SeedError(f"{name} @ {target}: cannot rebuild every file: " + "; ".join(problems[:5]))
    # A revlog can repeat a revision hash (kop-shared-bundles has one three times in a row); its changes belong to
    # the first copy, the later copies are empty - as in Redbox's seeds and as the importer stores them.
    first = {}
    for i, r in enumerate(revs):
        first.setdefault(r["hash"], i)

    def changes_of(i, r):
        return by_rev.get(r["hash"], []) if first[r["hash"]] == i else []
    last = {}
    for i, r in enumerate(revs):
        for c in changes_of(i, r):
            last[(c["target_path"], c["target_name"])] = (i, c["idx"])
    out = Repo(name)
    for i, r in enumerate(revs):
        changes = []
        for c in changes_of(i, r):
            key = (c["target_path"], c["target_name"])
            path = _retarget(c["target_path"], retarget)
            vh = c["version_hash"]
            ch = c["content_hash"] or vh
            if last[key] == (i, c["idx"]):
                have = files.get(key)
                if have is None or have[0] != vh:
                    if not allow_placeholders:
                        raise SeedError(f"{name}: {c['target_name']} has no content at {r['hash']} (only ever a placeholder)")
                    changes.append(_change(vh, ch, c["target_name"], path, c["is_seed"], True))
                    continue
                # Re-seeded: the whole file. ContentHash is kept from the source revision, as Redbox's seeds do.
                changes.append(_change(vh, ch, c["target_name"], path, True, False))
                out.content[(r["hash"], path, c["target_name"])] = ("blob", have[1])
                out.head_files[(path, c["target_name"])] = (vh, ("blob", have[1]))
            else:
                changes.append(_change(vh, ch, c["target_name"], path, c["is_seed"], True))
        out.revisions.append({"Hash": r["hash"], "Label": r["label"], "Changes": changes})
    return out


def _retarget(path, retarget):
    for old, new in retarget or []:
        if path.lower().startswith(old.lower()):
            return new + path[len(old):]
    return path


def add_revision(r, files, label=None, deltas=True):
    """Append a revision with these files, `files` = [(TargetPath, TargetName, local path)]. Unchanged files are
    left out; a changed file is a VCDIFF delta against the head version when that is less than half its size,
    else a seed. The revision hash is the SHA-1 of the repository, its previous head, the label and the new
    versions, so the same inputs always give the same seed."""
    items = []
    for tp, tn, local in sorted(files, key=lambda f: (f[0].lower(), f[1].lower())):
        vh = _sha1_file(local)
        prev = r.head_files.get((tp, tn))
        if prev and prev[0] == vh:
            continue
        items.append((tp, tn, local, vh, prev))
    if not items:
        return None
    basis = "\n".join([r.name, r.head or "", label or ""] + [f"{tp}\\{tn} {vh}" for tp, tn, _, vh, _ in items])
    rev = hashlib.sha1(basis.encode("utf-8")).hexdigest().upper()
    changes = []
    for tp, tn, local, vh, prev in items:
        content, is_seed, ch = ("file", str(local)), True, vh
        if deltas and prev and shutil.which("xdelta3"):
            delta = repomod.xdelta_encode(_read(prev[1]), pathlib.Path(local).read_bytes())
            if len(delta) < os.path.getsize(local) // 2:
                content, is_seed, ch = ("bytes", delta), False, hashlib.sha1(delta).hexdigest().upper()
        changes.append(_change(vh, ch, tn, tp, is_seed, False))
        r.content[(rev, tp, tn)] = content
        r.head_files[(tp, tn)] = (vh, ("file", str(local)))
    r.revisions.append({"Hash": rev, "Label": label, "Changes": changes})
    return rev


def files_from_dir(root, target_path):
    """(TargetPath, TargetName, local path) for every file under `root`, subfolders appended to target_path."""
    root = pathlib.Path(root)
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            sub = "\\".join(p.relative_to(root).parts[:-1])
            out.append((target_path.rstrip("\\") + ("\\" + sub if sub else ""), p.name, p))
    return out


def _read(ref):
    kind, v = ref
    if kind == "blob":
        return blobs.get_bytes(v)
    if kind == "file":
        return pathlib.Path(v).read_bytes()
    return v


def _open(ref):
    kind, v = ref
    if kind == "blob":
        return open(blobs.path(v), "rb")
    if kind == "file":
        return open(v, "rb")
    return io.BytesIO(v)


def _size(ref):
    kind, v = ref
    if kind == "blob":
        return blobs.size(v)
    if kind == "file":
        return os.path.getsize(v)
    return len(v)


# ---------------------------------------------------------------------------------------------- write

def revlog_bytes(entry):
    e = {"Hash": entry["Hash"], "Label": entry["Label"],
         "Changes": [{k: c[k] for k in CHANGE_KEYS} for c in entry["Changes"] or []]}
    return json.dumps(e, separators=(",", ":")).encode("utf-8")


def write_zip(path, repos, when=None, progress=None):
    """Write the seed. `repos` = list of Repo. Returns a summary dict."""
    when = when or dt.datetime.now()
    stamp = (when.year, when.month, when.day, when.hour, when.minute, when.second - when.second % 2)
    path = pathlib.Path(path)
    tmp = path.with_name(path.name + ".tmp")
    summary = {"file": str(path), "repositories": {}}
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as z:
        def add(name, ref):
            zi = zipfile.ZipInfo(name, stamp)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 0
            zi.create_version = 45
            zi.extract_version = 20
            zi.external_attr = 0x20
            size = _size(ref)
            with _open(ref) as src, z.open(zi, "w", force_zip64=size > 0x7FFF0000) as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
        for r in sorted(repos, key=lambda x: x.name):
            patches = {}
            for (rev, tp, tn), ref in r.content.items():
                patches[repomod.patch_name(rev, tp, tn)] = ref
            add(f"{r.name}/.active", ("bytes", STATE))
            add(f"{r.name}/.current", ("bytes", STATE))
            for n in sorted(patches):
                add(f"{r.name}/{n}", patches[n])
            for n in sorted(str(i) for i in range(len(r.revisions))):
                add(f"{r.name}/.revlog/{n}", ("bytes", revlog_bytes(r.revisions[int(n)])))
            summary["repositories"][r.name] = {"head": r.head, "label": r.revisions[-1]["Label"] if r.revisions else None,
                                               "revisions": len(r.revisions), "content_files": len(patches)}
            if progress:
                progress(f"{r.name}: {len(r.revisions)} revisions, {len(patches)} content files")
    os.replace(tmp, path)
    summary["bytes"] = path.stat().st_size
    summary["sha1"] = _sha1_file(path)
    return summary


async def build(path, specs, additions=(), when=None, retarget=None, deltas=True, allow_placeholders=False,
                progress=None):
    """specs: [(repo, revision or None, edition or None)]; additions: [(repo, [(TargetPath, TargetName, file)], label)].
    `progress(msg)` may be a plain function or a coroutine function. The zip is written in a worker thread."""
    loop = asyncio.get_running_loop()

    def say(msg):  # from this task or from the writer thread
        if not progress:
            return
        if asyncio.iscoroutinefunction(progress):
            asyncio.run_coroutine_threadsafe(progress(msg), loop)
        else:
            progress(msg)
    repos = {}
    for name, revision, edition in specs:
        repos[name] = await from_store(name, revision, edition, retarget=retarget, allow_placeholders=allow_placeholders)
        say(f"{name}: {len(repos[name].revisions)} revisions up to {repos[name].head}")
    for name, files, label in additions:
        if name not in repos:
            known = await db.fetchval("SELECT 1 FROM upd_revisions WHERE repo=%s LIMIT 1", name)
            repos[name] = await from_store(name, retarget=retarget) if known else Repo(name)
        rev = add_revision(repos[name], files, label=label, deltas=deltas)
        say(f"{name}: added revision {rev or '(no changes)'} with {len(files)} files")
    if not repos:
        raise SeedError("nothing to build: give repositories (or --all) and/or additions")
    return await asyncio.to_thread(write_zip, path, list(repos.values()), when, say)


# ---------------------------------------------------------------------------------------------- check

def check(path, reference=None, apply=True, progress=None):
    """Check a seed the way updatemgrd will use it, and optionally compare it with a reference seed.

    For every repository: .active/.current are 40-zero JSON strings; .revlog/0..n-1 are contiguous and parse;
    every non-placeholder change has its content file and no content file is unreferenced; the head revision has
    no placeholders; per file, the first non-placeholder change is a seed and none after it is a placeholder; with
    `apply`, every file is rebuilt like RepositoryService.UpdateToInternal does (seed copy, xdelta3 -d for deltas,
    SHA-1 = VersionHash after every step). Returns {"ok": bool, "problems": [...], "repositories": {...}}."""
    problems, repos = [], {}
    src = editions_mod.ZipSource(path)
    z = src.zip
    names = set(z.namelist())
    for name in src.repositories():
        p = src.prefix + name + "/"
        info = {"revisions": 0, "seeds": 0, "deltas": 0, "placeholders": 0, "files": 0}
        repos[name] = info
        for state in (".active", ".current"):
            n = p + state
            if n not in names:
                problems.append(f"{name}: no {state}")
                continue
            try:
                v = json.loads(z.read(n).decode("utf-8-sig"))
                if not (isinstance(v, str) and re.fullmatch(r"[0-9A-Fa-f]{40}", v)):
                    problems.append(f"{name}: {state} is not a revision hash")
                elif v != ZERO:
                    problems.append(f"{name}: {state} is {v}, a seed has 40 zeros")
            except ValueError:
                problems.append(f"{name}: {state} is not JSON")
        idx = sorted(int(m.group(1)) for n in names for m in [re.match(re.escape(p) + r"\.revlog/(\d+)$", n)] if m)
        if idx != list(range(len(idx))):
            problems.append(f"{name}: .revlog entries are not 0..{len(idx) - 1}")
        entries = [json.loads(z.read(f"{p}.revlog/{i}").decode("utf-8-sig")) for i in idx]
        info["revisions"] = len(entries)
        referenced = set()
        chains = {}
        for i, e in enumerate(entries):
            for c in e.get("Changes") or []:
                key = (c["TargetPath"], c["TargetName"])
                pn = repomod.patch_name(e["Hash"].upper(), c["TargetPath"], c["TargetName"])
                if c["Composite"]:
                    info["placeholders"] += 1
                    if i == len(entries) - 1:
                        problems.append(f"{name}: head revision has a placeholder for {c['TargetName']} (updatemgrd refuses it)")
                    if key in chains:
                        problems.append(f"{name}: placeholder for {c['TargetName']} after its content in {e['Hash']}")
                    continue
                referenced.add(p + pn)
                if p + pn not in names:
                    problems.append(f"{name}: missing content file {pn}")
                    continue
                if key not in chains:
                    if not c["IsSeed"]:
                        problems.append(f"{name}: {c['TargetName']} starts with a delta in {e['Hash']} (no seed to patch)")
                    chains[key] = []
                chains[key].append((p + pn, c["IsSeed"], c["VersionHash"].upper(), e["Hash"]))
                info["seeds" if c["IsSeed"] else "deltas"] += 1
        unreferenced = [n for n in names if n.startswith(p) and n.endswith(".patch") and n not in referenced]
        if unreferenced:
            problems.append(f"{name}: {len(unreferenced)} content files no change refers to")
        info["files"] = len(chains)
        if apply:
            with tempfile.TemporaryDirectory() as td:
                for key, chain in chains.items():
                    work = os.path.join(td, "work")
                    for member, is_seed, vh, rev in chain:
                        out = os.path.join(td, "patch")
                        with z.open(member) as f, open(out, "wb") as o:
                            shutil.copyfileobj(f, o, 1 << 20)
                        if is_seed:
                            os.replace(out, work)
                        else:
                            res = subprocess.run(["xdelta3", "-f", "-d", "-s", work, out, work + ".new"], capture_output=True)
                            if res.returncode or res.stderr.strip():
                                problems.append(f"{name}: {key[1]} at {rev}: xdelta3: {res.stderr.decode(errors='replace').strip()}")
                                break
                            os.replace(work + ".new", work)
                        if _sha1_file(work) != vh:
                            problems.append(f"{name}: {key[1]} at {rev}: SHA-1 is not VersionHash {vh}")
                            break
        if progress:
            progress(f"{name}: {info}")
    result = {"ok": not problems, "problems": problems, "repositories": repos}
    if reference:
        result["compare"] = compare(path, reference)
    src.close()
    return result


def compare(path, reference):
    """Entry-by-entry comparison with another seed: names, revlog bytes, content SHA-1 and zip entry attributes."""
    a, b = zipfile.ZipFile(path), zipfile.ZipFile(reference)
    ia = {i.filename: i for i in a.infolist()}
    ib = {i.filename: i for i in b.infolist()}
    out = {"only_in_built": sorted(set(ia) - set(ib))[:20], "only_in_reference": sorted(set(ib) - set(ia))[:20],
           "counts": {"built": len(ia), "reference": len(ib)}, "different_content": [], "different_attributes": []}
    for n in sorted(set(ia) & set(ib)):
        x, y = ia[n], ib[n]
        if x.CRC != y.CRC or x.file_size != y.file_size:
            out["different_content"].append(n)
        attrs = ("compress_type", "create_system", "create_version", "extract_version", "external_attr", "flag_bits")
        if any(getattr(x, k) != getattr(y, k) for k in attrs):
            out["different_attributes"].append(n)
    order_a = [n for n in (i.filename for i in a.infolist()) if n in ib]
    order_b = [n for n in (i.filename for i in b.infolist()) if n in ia]
    out["same_order"] = order_a == order_b
    out["different_content"] = out["different_content"][:20] + (["..."] if len(out["different_content"]) > 20 else [])
    out["different_attributes"] = out["different_attributes"][:20]
    a.close()
    b.close()
    return out
