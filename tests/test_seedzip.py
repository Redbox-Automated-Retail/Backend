"""Installer seeds built by updatesvc/seedzip.py: format, checker, and the round trip through the importer."""
import asyncio
import json
import os
import pathlib
import shutil
import sys
import uuid
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "kcs"))
import cloudclient as cc  # noqa: E402

pytestmark = pytest.mark.skipif(not cc.database_available(), reason="DATABASE_URL not reachable")


def run(coro):
    from redbox_backend import db

    async def go():
        await db.init(max_size=2)
        try:
            return await coro
        finally:
            await db.close()
    saved, db._pool = db._pool, None  # other modules may hold a pool bound to another event loop
    try:
        return asyncio.run(go())
    finally:
        db._pool = saved


def test_build_check_and_import_round_trip(tmp_path):
    from redbox_backend import blobs, db
    from redbox_backend.updatesvc import editions, repo, seedzip

    name = "seedtest-" + uuid.uuid4().hex[:8]
    edition = "seedtest-" + uuid.uuid4().hex[:8]
    tp = "C:\\Program Files\\Redbox\\REDS\\SeedTest"
    v1, v2 = tmp_path / "v1", tmp_path / "v2"
    (v1 / "sub").mkdir(parents=True)
    (v2 / "sub").mkdir(parents=True)
    big = os.urandom(40000)
    (v1 / "a.dll").write_bytes(big)
    (v1 / "sub" / "b.txt").write_text("first\n")
    (v1 / "c.txt").write_text("unchanged\n")
    (v2 / "a.dll").write_bytes(big + b"tail")          # small change: published as a delta
    (v2 / "sub" / "b.txt").write_text("second\n")

    async def scenario():
        try:
            await repo.publish_revision(name, seedzip.files_from_dir(v1, tp), label="r1")
            await repo.publish_revision(name, seedzip.files_from_dir(v2, tp), label="r2")
            new = tmp_path / "new"
            new.mkdir()
            (new / "a.dll").write_bytes(big + b"tail and more")
            out = tmp_path / "data-01022030.zip"
            summary = await seedzip.build(out, [(name, None, None)], additions=[
                (name, seedzip.files_from_dir(new, tp), "r3-local")], when=None)
            assert summary["repositories"][name]["revisions"] == 3

            # Layout and attributes as in Redbox's seeds
            z = zipfile.ZipFile(out)
            names = [i.filename for i in z.infolist()]
            assert names[:2] == [f"{name}/.active", f"{name}/.current"]
            assert z.read(f"{name}/.active") == b'"' + b"0" * 40 + b'"'
            assert names[-3:] == [f"{name}/.revlog/0", f"{name}/.revlog/1", f"{name}/.revlog/2"]
            assert {(i.create_system, i.external_attr, i.flag_bits, i.compress_type) for i in z.infolist()} == {(0, 0x20, 0, 8)}
            revs = [json.loads(z.read(f"{name}/.revlog/{i}")) for i in range(3)]
            assert list(revs[0]) == ["Hash", "Label", "Changes"] and revs[2]["Label"] == "r3-local"
            assert list(revs[0]["Changes"][0]) == list(seedzip.CHANGE_KEYS)
            # r1's b.txt and a.dll were changed later: placeholders; c.txt only ever in r1: a seed there
            r1 = {c["TargetName"]: c for c in revs[0]["Changes"]}
            assert r1["b.txt"]["Composite"] and r1["a.dll"]["Composite"] and not r1["c.txt"]["Composite"]
            assert r1["c.txt"]["IsSeed"]
            r2 = {c["TargetName"]: c for c in revs[1]["Changes"]}
            assert r2["b.txt"]["IsSeed"] and not r2["b.txt"]["Composite"]     # last change: seed
            assert r2["a.dll"]["IsSeed"] and not r2["a.dll"]["Composite"]     # delta in the store, re-seeded here
            r3 = revs[2]["Changes"]
            assert len(r3) == 1 and r3[0]["TargetName"] == "a.dll" and not r3[0]["IsSeed"]  # added: VCDIFF delta
            z.close()

            res = seedzip.check(out)
            assert res["ok"], res["problems"]
            assert res["repositories"][name]["deltas"] == 1

            # Same inputs, same seed (revision hashes are deterministic); content identical entry by entry
            again = tmp_path / "again.zip"
            await seedzip.build(again, [(name, None, None)], additions=[(name, seedzip.files_from_dir(new, tp), "r3-local")])
            cmp = seedzip.compare(again, out)
            assert not cmp["different_content"] and not cmp["only_in_built"] and cmp["same_order"]

            # Round trip: the importer reads it back, and every file rebuilds to the same content
            await editions.import_edition(out, name=edition)
            files, head, problems = await repo.reconstruct_head(name, edition=edition)
            assert not problems and head == revs[2]["Hash"]
            got = {k[1]: blobs.get_bytes(v[1]) for k, v in files.items()}
            assert got == {"a.dll": big + b"tail and more", "b.txt": b"second\n", "c.txt": b"unchanged\n"}
        finally:
            await db.execute("DELETE FROM upd_editions WHERE name=%s", edition)
            await db.execute("DELETE FROM upd_repositories WHERE name=%s", name)

    run(scenario())


def test_check_reports_broken_seeds(tmp_path):
    from redbox_backend.updatesvc import seedzip

    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("r/.active", b'"' + b"0" * 40 + b'"')
        entry = {"Hash": "A" * 40, "Label": None, "Changes": [
            {"VersionHash": "B" * 40, "ContentHash": "B" * 40, "TargetName": "x", "TargetPath": "C:\\t", "IsSeed": False, "Composite": False},
            {"VersionHash": "C" * 40, "ContentHash": "C" * 40, "TargetName": "y", "TargetPath": "C:\\t", "IsSeed": True, "Composite": True}]}
        z.writestr("r/.revlog/0", json.dumps(entry))
    res = seedzip.check(bad)
    assert not res["ok"]
    text = " ".join(res["problems"])
    assert "no .current" in text and "missing content file" in text and "placeholder" in text
