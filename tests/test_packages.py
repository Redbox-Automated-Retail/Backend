"""Fixture tests on Redbox's real update-store seeds (data-<date>.zip), read-only.

Set RB_PACKAGES_DIR to the folder holding them (`rb.ps1 test` mounts PACKAGES_DIR there). The tests that need
the served history expect `rb.ps1 import-packages` to have run; they are skipped otherwise.
"""
import asyncio
import hashlib
import os
import pathlib
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "kcs"))
import cloudclient as cc  # noqa: E402

PKG = os.environ.get("RB_PACKAGES_DIR")
pytestmark = [pytest.mark.skipif(not PKG or not os.path.isdir(PKG), reason="RB_PACKAGES_DIR not set"),
              pytest.mark.skipif(not cc.database_available(), reason="DATABASE_URL not reachable")]
OLDEST = "data-02282014.zip"
SOFTWARE = ["kop-v2-engine", "kop-shared-bundles", "kop-rental-bundles", "kop-hal-install", "kop-tools",
            "kop-scripts", "kop-update-manager", "kop-ms-hal-tester"]


def run(coro):
    from redbox_backend import db

    async def wrapped():
        saved, db._pool = db._pool, None
        await db.init(max_size=2, apply_schema=False)
        try:
            return await coro
        finally:
            await db.close()
            db._pool = saved
    return asyncio.run(wrapped())


def test_edition_dates_from_file_names():
    from redbox_backend.updatesvc.editions import edition_date
    assert str(edition_date("data-02282014")) == "2014-02-28"
    assert str(edition_date("data-452019")) == "2019-04-05"
    assert str(edition_date("data-572024")) == "2024-05-07"
    names = sorted(p.stem for p in pathlib.Path(PKG).glob("data-*.zip"))
    assert names and all(edition_date(n).year in range(2010, 2030) for n in names)


@pytest.fixture(scope="module")
def oldest_edition():
    """Import the 2014 seed as its own edition (the served history is not touched)."""
    from redbox_backend.updatesvc import editions
    path = os.path.join(PKG, OLDEST)
    if not os.path.isfile(path):
        pytest.skip(f"{OLDEST} not present")
    from redbox_backend import db
    result = run(editions.import_edition(path, name="test-" + OLDEST[:-4], skip=("dvd-data",)))
    yield result
    run(db.execute("DELETE FROM upd_editions WHERE name=%s", result["edition"]))


def test_oldest_seed_reconstructs(oldest_edition):
    """Every file of the 2014 seed rebuilds to its head version with every SHA-1 checked."""
    from redbox_backend.updatesvc import repo

    async def check():
        out = {}
        for r in oldest_edition["repositories"]:
            files, head, problems = await repo.reconstruct_head(r, edition=oldest_edition["edition"])
            out[r] = (len(files), problems)
        return out
    result = run(check())
    assert result
    for r, (n, problems) in result.items():
        assert not problems, f"{r}: {problems[:3]}"


def _xdelta(src_key, patch_key):
    from redbox_backend import blobs
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "o")
        subprocess.run(["xdelta3", "-f", "-d", "-s", str(blobs.path(src_key)), str(blobs.path(patch_key)), out],
                       check=True, capture_output=True)
        return blobs.put_file(out)


def test_kiosk_set_up_from_2014_seed_is_patched_to_the_served_head(oldest_edition):
    """What updatemgrd does after the store installer activated the 2014 data file.

    The kiosk's repositories are at the 2014 heads. `u p` sends the later revisions of the served history;
    RepositoryService.MoveUpInternal then patches each changed file from the staged (2014) version, seed or
    VCDIFF chain, checking SHA-1 against VersionHash after every step. Emulated here with the same rules, then
    compared with the served snapshot.
    """
    from redbox_backend import blobs, db
    from redbox_backend.updatesvc import repo

    async def check():
        served = {r["name"] for r in await db.fetch("SELECT name FROM upd_repositories WHERE canonical_edition IS NOT NULL")}
        if not served:
            pytest.skip("run rb.ps1 import-packages first")
        report = {}
        for r in SOFTWARE:
            if r not in served or r not in oldest_edition["repositories"]:
                continue
            staged, _, problems = await repo.reconstruct_head(r, edition=oldest_edition["edition"])
            assert not problems
            kiosk_head = await db.fetchval("SELECT hash FROM upd_edition_revisions WHERE edition=%s AND repo=%s"
                                           " ORDER BY seq DESC LIMIT 1", oldest_edition["edition"], r)
            cs = await repo.change_set_for(r, kiosk_head)
            if cs is None:
                report[r] = "current"
                continue
            assert cs["IsArchive"] and cs["Revisions"][0] != kiosk_head
            state = {k: v[1] for k, v in staged.items()}
            patches = {(c["Revision"], c["TargetPath"], c["TargetName"]): c for c in cs["Items"]}
            rows = await db.fetch("SELECT c.*, v.seq FROM upd_changes c JOIN upd_revisions v ON v.repo=c.repo AND"
                                  " v.hash=c.rev_hash WHERE c.repo=%s AND c.rev_hash = ANY(%s) ORDER BY v.seq, c.idx",
                                  r, cs["Revisions"])
            steps = 0
            for c in rows:
                assert (c["rev_hash"], c["target_path"], c["target_name"]) in patches
                if c["composite"]:
                    continue
                key = (c["target_path"], c["target_name"])
                if c["is_seed"]:
                    assert blobs.sha1_hex(blobs.get_bytes(c["blob"])) == c["version_hash"]
                    state[key] = c["version_hash"]
                else:
                    assert key in state, f"{r}: delta for {key[1]} without a source version"
                    got = _xdelta(state[key], c["blob"])  # blob key = SHA-1 of the patched file
                    assert got == c["version_hash"], f"{r}: {key[1]} at {c['rev_hash']}"
                    state[key] = got
                steps += 1
            head = await repo.served_head(r)
            snap = {(s["target_path"], s["target_name"]): s["version_hash"]
                    for s in await db.fetch("SELECT * FROM upd_snapshots WHERE repo=%s AND head_hash=%s", r, head)}
            for k, v in snap.items():
                if k in state:
                    assert state[k] == v, f"{r}: {k[1]} differs from the served snapshot"
            report[r] = f"{len(cs['Revisions'])} revisions, {steps} patch steps"
        return report
    report = run(check())
    assert report, "no software repository in common with the served history"
    print(report)


def test_installer_start_reply_satisfies_installer_steps_lua():
    """Fields, types and values installer-steps.lua (store installer 3.1.0.48) checks."""
    import json
    from redbox_backend.updatesvc import commands, rcp

    async def call():
        reply = rcp.Reply()
        hash_ = await __import__("redbox_backend.db", fromlist=["db"]).fetchval(
            "SELECT heads->>'dvd-data' FROM upd_editions WHERE heads ? 'dvd-data' ORDER BY edition_date DESC LIMIT 1")
        await commands.cmd_installer("start", {"hash": hash_ or "0" * 40, "version": "3.1.0.48"}, reply)
        return json.loads(reply.lines[0])
    res = run(call())
    for k in ("DataFileExpiration", "InstallerExpiration", "CurrentDateTime", "URL", "HostEntries", "guid"):
        assert isinstance(res[k], str) and res[k], k
    assert int(res["DataFileExpiration"]) < 0 and int(res["InstallerExpiration"]) < 0  # never expired
    assert res["ErrorMessage"] == ""
