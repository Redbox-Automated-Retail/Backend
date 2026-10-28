"""Import update stores as editions and rebuild the served history from them.

A source is either a kiosk's `.store` / `.store/.repository` directory or a Redbox installer seed
`data-<date>.zip` (the `data.zip` that the store installer activates on a new kiosk). Both hold
`<repo>/.revlog/<n>` and `<repo>/<rev>-<SHA1(path)>.patch`.

Findings from the 2014-2024 seeds that shape this module:
  * every repository has one linear history: a newer seed's revlog extends the older one's;
  * newer seeds trim old revisions (empty change lists, files re-seeded in a later revision), so a seed is
    self-consistent but older seeds still hold content the newer ones dropped;
  * `dvd-data` does not follow that rule (each seed carries its own short, re-seeded history) and is by far
    the largest part (12.5 of 13 GB of unique content across the 13 seeds).
So each source is stored as an edition; the served history for a repository is the newest edition's revlog
(`rebuild_canonical`), with missing content filled in from older editions. Older editions also let an old
revision be reconstructed and pinned (`repo.build_snapshot(..., head=...)`).
"""
import datetime as dt
import json
import logging
import pathlib
import re
import zipfile

from .. import blobs, db
from . import repo as repomod

log = logging.getLogger("editions")


def _json(data: bytes):
    return json.loads(data.decode("utf-8-sig"))


class ZipSource:
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.zip = zipfile.ZipFile(self.path)
        self.names = set(self.zip.namelist())
        self.prefix = ""
        if not any(re.match(r"[^/]+/\.revlog/\d+$", n) for n in self.names):
            # tolerate an extra top folder (".repository/<repo>/...")
            for n in self.names:
                m = re.match(r"(.+/)[^/]+/\.revlog/\d+$", n)
                if m:
                    self.prefix = m.group(1)
                    break

    def default_name(self):
        return self.path.stem

    def date(self):
        return edition_date(self.path.stem, max((dt.datetime(*i.date_time) for i in self.zip.infolist()),
                                                default=None))

    def repositories(self):
        repos = set()
        for n in self.names:
            m = re.match(re.escape(self.prefix) + r"([^/]+)/\.revlog/\d+$", n)
            if m:
                repos.add(m.group(1))
        return sorted(repos)

    def revlog(self, repo):
        entries = {}
        for n in self.names:
            m = re.match(re.escape(self.prefix + repo) + r"/\.revlog/(\d+)$", n)
            if m:
                entries[int(m.group(1))] = _json(self.zip.read(n))
        return [entries[i] for i in sorted(entries)]

    def open_patch(self, repo, name):
        n = f"{self.prefix}{repo}/{name}"
        return self.zip.open(n) if n in self.names else None

    def close(self):
        self.zip.close()


class DirSource:
    def __init__(self, path):
        p = pathlib.Path(path)
        self.root = p / ".repository" if (p / ".repository").is_dir() else p
        self.path = p

    def default_name(self):
        return f"store-{self.date():%Y%m%d}"

    def date(self):
        stamps = [f.stat().st_mtime for f in self.root.glob("*/.revlog/*")]
        return dt.date.fromtimestamp(max(stamps)) if stamps else dt.date.today()

    def repositories(self):
        return sorted(p.name for p in self.root.iterdir() if (p / ".revlog").exists())

    def revlog(self, repo):
        d = self.root / repo / ".revlog"
        if d.is_file():
            return _json(d.read_bytes())
        files = sorted((f for f in d.iterdir() if f.name.isdigit()), key=lambda f: int(f.name))
        return [_json(f.read_bytes()) for f in files]

    def open_patch(self, repo, name):
        p = self.root / repo / name
        return open(p, "rb") if p.is_file() else None

    def close(self):
        pass


def open_source(path):
    p = pathlib.Path(path)
    return ZipSource(p) if p.is_file() and zipfile.is_zipfile(p) else DirSource(p)


def edition_date(stem, fallback=None):
    """data-MMDDYYYY with optional missing zeros (data-572024 = 5/7/2024, data-452019 = 4/5/2019)."""
    m = re.search(r"(\d{5,8})$", stem)
    if m:
        digits, year = m.group(1)[:-4], int(m.group(1)[-4:])
        candidates = []
        for cut in range(1, len(digits)):
            mm, dd = int(digits[:cut]), int(digits[cut:])
            if 1 <= mm <= 12 and 1 <= dd <= 31 and len(digits[:cut]) <= 2 and len(digits[cut:]) <= 2:
                candidates.append(dt.date(year, mm, dd))
        if len(candidates) == 1:
            return candidates[0]
        if candidates and fallback:
            fb = fallback.date() if isinstance(fallback, dt.datetime) else fallback
            return min(candidates, key=lambda c: abs((c - fb).days))
        if candidates:
            return candidates[0]
    if isinstance(fallback, dt.datetime):
        return fallback.date()
    return fallback or dt.date.today()


async def import_edition(path, name=None, repos=None, skip=(), progress=None):
    """Store one source as an edition (replacing an edition of the same name). Returns a summary dict."""
    src = open_source(path)
    try:
        name = name or src.default_name()
        date = src.date()
        await db.execute("DELETE FROM upd_editions WHERE name=%s", name)
        await db.execute("INSERT INTO upd_editions (name, source, edition_date) VALUES (%s,%s,%s)",
                         name, str(path), date)
        heads, counts, stored = {}, {}, 0
        for r in src.repositories():
            if (repos and r not in repos) or r in skip:
                continue
            entries = src.revlog(r)
            rev_rows, change_rows, seen = [], [], set()
            for seq, entry in enumerate(entries):
                h = entry["Hash"].upper()
                rev_rows.append((name, r, seq, h, entry.get("Label")))
                for ch in entry.get("Changes") or []:
                    k = (h, ch["TargetPath"], ch["TargetName"], ch["VersionHash"].upper())
                    if k in seen:  # repeated revision hash in a revlog: keep the first copy of a change
                        continue
                    seen.add(k)
                    blob = None
                    if not ch.get("Composite"):
                        f = src.open_patch(r, repomod.patch_name(h, ch["TargetPath"], ch["TargetName"]))
                        if f is not None:
                            with f:
                                blob = blobs.put_stream(f)
                            stored += 1
                    change_rows.append([name, r, h, ch["TargetName"], ch["TargetPath"], ch["VersionHash"].upper(),
                                        (ch.get("ContentHash") or "").upper() or None, bool(ch.get("IsSeed")),
                                        bool(ch.get("Composite")), blob])
            idx = {}
            rows = []
            for row in change_rows:
                i = idx.get(row[2], 0)
                idx[row[2]] = i + 1
                rows.append(tuple(row[:3] + [i] + row[3:]))
            await db.executemany("INSERT INTO upd_edition_revisions (edition, repo, seq, hash, label) VALUES"
                                 " (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", rev_rows)
            await db.executemany("INSERT INTO upd_edition_changes (edition, repo, rev_hash, idx, target_name,"
                                 " target_path, version_hash, content_hash, is_seed, composite, blob) VALUES"
                                 " (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", rows)
            if entries:
                heads[r] = entries[-1]["Hash"].upper()
                counts[r] = len(entries)
            if progress:
                await progress(f"{name}: {r} ({len(entries)} revisions)")
        await db.execute("UPDATE upd_editions SET heads=%s, revisions=%s WHERE name=%s", db.jsonb(heads),
                         db.jsonb(counts), name)
        log.info("edition %s (%s): %d repositories, %d content files", name, date, len(heads), stored)
        return {"edition": name, "date": str(date), "repositories": counts, "content_files": stored}
    finally:
        src.close()


async def rebuild_canonical(repo, force=False):
    """Serve the newest edition's history for this repository (content filled in from older editions)."""
    best = await db.fetchrow(
        "SELECT e.name FROM upd_editions e JOIN upd_edition_revisions r ON r.edition = e.name AND r.repo = %s"
        " GROUP BY e.name, e.edition_date ORDER BY e.edition_date DESC, count(*) DESC LIMIT 1", repo)
    if best is None:
        return None
    edition = best["name"]
    published = await db.fetchval("SELECT count(*) FROM upd_revisions WHERE repo=%s AND origin='published'", repo)
    if published and not force:
        log.warning("%s has %d published revisions; not replacing its history (use force)", repo, published)
        return {"repository": repo, "edition": edition, "skipped": "has published revisions"}
    # Keep the repository row (store links hang off it); replace its history.
    await db.execute("INSERT INTO upd_repositories (name) VALUES (%s) ON CONFLICT DO NOTHING", repo)
    await db.execute("UPDATE upd_repositories SET canonical_edition=%s WHERE name=%s", edition, repo)
    await db.execute("DELETE FROM upd_revisions WHERE repo=%s", repo)  # cascades to upd_changes
    await db.execute("DELETE FROM upd_snapshots WHERE repo=%s", repo)
    await db.execute("INSERT INTO upd_revisions (repo, seq, hash, label, origin) SELECT repo, seq, hash, label, 'import'"
                     " FROM upd_edition_revisions WHERE edition=%s AND repo=%s ORDER BY seq ON CONFLICT DO NOTHING",
                     edition, repo)
    await db.execute(
        "INSERT INTO upd_changes (repo, rev_hash, idx, target_name, target_path, version_hash, content_hash, is_seed,"
        " composite, blob)"
        " SELECT c.repo, c.rev_hash, c.idx, c.target_name, c.target_path, c.version_hash, c.content_hash, c.is_seed,"
        "  c.composite, coalesce(c.blob, CASE WHEN NOT c.composite THEN (SELECT o.blob FROM upd_edition_changes o"
        "   WHERE o.repo = c.repo AND o.rev_hash = c.rev_hash AND o.target_path = c.target_path"
        "   AND o.target_name = c.target_name AND o.version_hash = c.version_hash AND o.is_seed = c.is_seed"
        "   AND o.blob IS NOT NULL LIMIT 1) END)"
        " FROM upd_edition_changes c WHERE c.edition=%s AND c.repo=%s"
        " AND EXISTS (SELECT 1 FROM upd_revisions v WHERE v.repo = c.repo AND v.hash = c.rev_hash)"
        " ON CONFLICT DO NOTHING", edition, repo)
    n = await db.fetchval("SELECT count(*) FROM upd_revisions WHERE repo=%s", repo)
    missing = await db.fetchval("SELECT count(*) FROM upd_changes WHERE repo=%s AND NOT composite AND blob IS NULL", repo)
    log.info("%s: serving %d revisions from %s (%d changes without content)", repo, n, edition, missing)
    return {"repository": repo, "edition": edition, "revisions": n, "missing_content": missing}


async def editions_containing(repo, rev_hash):
    """Editions whose revlog has this revision, oldest first (they keep the most content for old revisions)."""
    return [r["edition"] for r in await db.fetch(
        "SELECT r.edition FROM upd_edition_revisions r JOIN upd_editions e ON e.name = r.edition"
        " WHERE r.repo=%s AND r.hash=%s ORDER BY e.edition_date", repo, rev_hash)]


async def import_sources(paths, dvd_data="latest", repos=None, snapshots=True, progress=None):
    """Import several sources (oldest first), rebuild the served histories and their snapshots."""
    opened = []
    for p in paths:
        src = open_source(p)
        opened.append((src.date(), str(p)))
        src.close()
    opened.sort()
    results = []
    touched = set()
    for i, (date, p) in enumerate(opened):
        newest = i == len(opened) - 1
        skip = () if dvd_data == "all" or (dvd_data == "latest" and newest) else ("dvd-data",)
        res = await import_edition(p, repos=repos, skip=skip, progress=progress)
        touched.update(res["repositories"])
        results.append(res)
    for r in sorted(touched):
        c = await rebuild_canonical(r)
        results.append(c)
        if progress:
            await progress(f"serving {r}: {c}")
        if snapshots and c and not c.get("skipped"):
            s = await repomod.build_snapshot(r)
            results.append({k: v for k, v in s.items() if k != "problems"} | {"problems": len(s["problems"])})
            if progress:
                await progress(f"snapshot {r}: {s.get('files')} files, {len(s['problems'])} problems")
    return results
