"""Releases of our own builds (updatesvc/releases.py): publish unreleased, staged rollout, rollback, CI API."""
import asyncio
import hashlib
import io
import json
import os
import sys
import uuid
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "kcs"))
import cloudclient as cc  # noqa: E402

pytestmark = pytest.mark.skipif(not cc.database_available(), reason="DATABASE_URL not reachable")

# The repository spells the path "redbox"; the build says "Redbox". Patch names hash the exact spelling, so the
# release must be published under the repository's spelling.
REPO_ROOT = "C:\\Program Files\\redbox\\REDS\\RelTest"
BUILD_ROOT = "C:\\Program Files\\Redbox\\REDS\\RelTest"


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


def release_zip(path, files, **meta):
    doc = {"Format": 1, "Component": "reltest", "Kind": "repository", "TargetRoot": BUILD_ROOT,
           "Source": {"Repository": "test", "Commit": "0" * 40},
           "Files": {k: hashlib.sha1(v).hexdigest().upper() for k, v in files.items()}} | meta
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("release.json", json.dumps(doc))
        for rel, data in files.items():
            z.writestr("files/" + rel, data)
    return path


async def _cleanup(name, stores):
    from redbox_backend import db
    await db.execute("DELETE FROM upd_repositories WHERE name=%s", name)
    await db.execute("DELETE FROM upd_snapshots WHERE repo=%s", name)
    await db.execute("DELETE FROM upd_rollout_log WHERE repo=%s", name)
    for s in stores:
        await db.execute("DELETE FROM upd_store_heads WHERE number=%s", s)
        await db.execute("DELETE FROM upd_stores WHERE number=%s", s)
        await db.execute("DELETE FROM kiosks WHERE kiosk_id=%s", int(s))


async def _report(store, name, head):
    from redbox_backend import db
    await db.execute("INSERT INTO upd_stores (number) VALUES (%s) ON CONFLICT DO NOTHING", store)
    await db.execute("INSERT INTO upd_store_heads (number, repo, head) VALUES (%s,%s,%s) ON CONFLICT (number, repo)"
                     " DO UPDATE SET head=EXCLUDED.head", store, name, head)


def test_publish_rollout_and_rollback(tmp_path):
    from redbox_backend import db
    from redbox_backend.updatesvc import releases, repo

    name = "reltest-" + uuid.uuid4().hex[:8]
    base = 9100000 + int(uuid.uuid4().int % 800000)
    a, b, c = str(base), str(base + 1), str(base + 2)
    market = "reltest-" + uuid.uuid4().hex[:6]
    v1 = tmp_path / "v1"
    (v1 / "bin").mkdir(parents=True)
    dll1 = os.urandom(30000)
    (v1 / "bin" / "App.dll").write_bytes(dll1)
    (v1 / "bin" / "appsettings.json").write_text('{"keep": true}')
    (v1 / "afteractivate.lua").write_text("kernel.log('x')\n")
    dll2 = dll1 + b"version 1.1"

    async def scenario():
        try:
            from redbox_backend.updatesvc import seedzip
            r1 = await repo.publish_revision(name, seedzip.files_from_dir(v1, REPO_ROOT), label="base")
            pub = await releases.publish_release(release_zip(tmp_path / "r.zip", {
                "bin/App.dll": dll2, "bin/Microsoft.Extensions.Logging.dll": b"framework", "bin/App.pdb": b"pdb"},
                Repository=name, Version="1.1.0"))
            r2 = pub["revision"]
            assert pub["files"] == 1 and pub["ignored"] == 2 and pub["label"] == f"{name}-1.1.0"
            ch = await db.fetchrow("SELECT * FROM upd_changes WHERE repo=%s AND rev_hash=%s", name, r2)
            assert ch["target_path"] == REPO_ROOT + "\\bin" and ch["target_name"] == "App.dll"
            assert not ch["is_seed"]                                   # a small change: an xdelta3 delta
            with pytest.raises(releases.ReleaseError, match="already has"):
                await releases.publish_release(release_zip(tmp_path / "r2.zip", {"bin/App.dll": dll2 + b"!"},
                                                           Repository=name, Version="1.1.0"))

            # Published, not served: nobody gets it.
            assert await repo.served_head(name) == r1 and await repo.served_head(name, a) == r1
            assert await repo.change_set_for(name, r1, a) is None

            # One store.
            await releases.rollout(name, "1.1.0", store=a)
            cs = await repo.change_set_for(name, r1, a)
            assert cs["Head"] == r2 and [i["TargetPath"] for i in cs["Items"]] == [REPO_ROOT + "\\bin"]
            assert await repo.change_set_for(name, r1, b) is None
            assert (await repo.change_set_for(name, None, a))["Items"][0]["Revision"] == r2   # fresh kiosk: snapshot

            # A market.
            await db.execute("INSERT INTO kiosks (kiosk_id, market) VALUES (%s,%s)", int(c), market)
            await releases.rollout(name, r2[:10], market=market)
            assert await repo.served_head(name, c) == r2

            # Store a took it; rolling it back publishes a revert (updatemgrd never moves back).
            await _report(a, name, r2)
            await _report(b, name, r1)
            res = await releases.rollback(name, store=a)
            rev = res["revert"]
            assert res["to"] == r1 and res["stores_ahead"] == [a] and rev
            files, head, problems = await repo.reconstruct_head(name, rev)
            assert not problems and files[(REPO_ROOT + "\\bin", "App.dll")][0] == hashlib.sha1(dll1).hexdigest().upper()
            assert (await repo.change_set_for(name, r2, a))["Revisions"] == [rev]
            assert await repo.change_set_for(name, r1, b) is None       # the revert is not released either

            # The market's kiosk never reported the release: it is simply pointed back.
            res = await releases.rollback(name, market=market)
            assert res["revert"] is None and await repo.served_head(name, c) == r1
            assert not await db.fetchval("SELECT 1 FROM upd_rollouts WHERE repo=%s AND target_type='market'", name)

            st = await releases.status(name)
            states = {s["store"]: s["state"] for s in st["stores"]}
            assert states[a] == "behind" and states[b] == "current"

            # Everyone: released, pinned, and store a's own rollout (older) folded in.
            await releases.rollout(name, rev)
            assert await repo.served_head(name) == rev and await repo.served_head(name, b) == rev
            assert await db.fetchval("SELECT bool_and(released) FROM upd_revisions WHERE repo=%s", name)
            assert not await db.fetchval("SELECT 1 FROM upd_rollouts WHERE repo=%s", name)
            res = await releases.rollback(name)          # everyone back to r1; only a has taken anything newer
            assert res["to"] == r1 and res["stores_ahead"] == [a] and res["revert"]
            assert await repo.served_head(name, b) == res["revert"]
        finally:
            await _cleanup(name, [a, b, c])

    run(scenario())


def test_bad_release_zips(tmp_path):
    from redbox_backend.updatesvc import releases

    good = {"bin/x.dll": b"x"}
    z = release_zip(tmp_path / "a.zip", good, Repository="r", Version="1.0")
    with zipfile.ZipFile(z, "a") as f:
        f.writestr("files/bin/extra.dll", b"not listed")
    with pytest.raises(releases.ReleaseError, match="differ"):
        releases.read_release(z)
    z = release_zip(tmp_path / "b.zip", good, Repository="r", Version="1.0",
                    Files={"bin/x.dll": "0" * 40})
    with pytest.raises(releases.ReleaseError, match="SHA-1"):
        releases.read_release(z)
    z = release_zip(tmp_path / "c.zip", {"../evil.dll": b"x"}, Repository="r", Version="1.0")
    with pytest.raises(releases.ReleaseError, match="bad path"):
        releases.read_release(z)
    z = release_zip(tmp_path / "d.zip", good, Repository="r", Version="1.0 beta")
    with pytest.raises(releases.ReleaseError, match="bad version"):
        releases.read_release(z)


def test_fileset_release(tmp_path):
    from redbox_backend import db
    from redbox_backend.updatesvc import releases

    fs_id = 900000 + int(uuid.uuid4().int % 90000)
    files = {"KioskClientService.API.dll": b"build 1", "sub/data.json": b"{}"}

    async def scenario():
        try:
            z = release_zip(tmp_path / "fs.zip", files, Kind="fileset", Version="1.0.300.0",
                            FileSet={"Id": fs_id, "Name": "Rel test"}, Destination="C:\\Program Files\\Redbox\\X\\bin")
            res = await releases.publish_release(z)
            dests = sorted(r["destination"] for r in await db.fetch("SELECT destination FROM fs_files WHERE fileset_id=%s",
                                                                     fs_id))
            assert dests == ["C:\\Program Files\\Redbox\\X\\bin\\KioskClientService.API.dll",
                             "C:\\Program Files\\Redbox\\X\\bin\\sub\\data.json"]
            assert res["revision"] and res["files"] == 2
            with pytest.raises(releases.ReleaseError, match="already has version"):
                await releases.publish_release(z)
            assert (await releases.fileset_status(fs_id))["revisions"][0]["version"] == "1.0.300.0"
        finally:
            await db.execute("DELETE FROM fs_filesets WHERE id=%s", fs_id)

    run(scenario())


def test_ci_api_tokens(tmp_path):
    from fastapi.testclient import TestClient
    from redbox_backend import apitokens, db
    from redbox_backend.dashboard import server
    from redbox_backend.updatesvc import repo, seedzip

    name = "reltest-" + uuid.uuid4().hex[:8]
    tokens = ["reltest-pub-" + uuid.uuid4().hex[:6], "reltest-read-" + uuid.uuid4().hex[:6]]
    src = tmp_path / "v1"
    src.mkdir()
    (src / "App.dll").write_bytes(b"one" * 1000)
    store = str(9100000 + int(uuid.uuid4().int % 800000))

    async def setup():
        await repo.publish_revision(name, seedzip.files_from_dir(src, BUILD_ROOT), label="base")
        return (await apitokens.create(tokens[0], ["publish", "deploy"]), await apitokens.create(tokens[1], ["read"]))
    pub, read = run(setup())
    server.app.state.plain = True
    saved, db._pool = db._pool, None
    try:
        with TestClient(server.app) as client:
            z = release_zip(tmp_path / "r.zip", {"App.dll": b"two" * 1000}, Repository=name, Version="2.0.0")
            upload = lambda tok: client.post("/api/ci/releases", files={"file": ("r.zip", z.read_bytes())},
                                             headers={"Authorization": f"Bearer {tok}"} if tok else {})
            assert upload(None).status_code == 401
            assert upload(read).status_code == 401                        # read-only token
            assert upload("rbt_" + "x" * 43).status_code == 401
            r = upload(pub)
            assert r.status_code == 200, r.text
            assert r.json()["version"] == "2.0.0" and r.json()["revision"]
            assert upload(pub).status_code == 400                          # same version again
            auth = {"Authorization": f"Bearer {pub}"}
            assert client.post(f"/api/ci/repos/{name}/rollout", json={"revision": "2.0.0"}, headers=auth).status_code == 400
            r = client.post(f"/api/ci/repos/{name}/rollout", json={"revision": "2.0.0", "store": store}, headers=auth)
            assert r.status_code == 200 and r.json()["head"] == r.json()["head"].upper()
            st = client.get(f"/api/ci/repos/{name}/status", headers={"Authorization": f"Bearer {read}"}).json()
            assert [o["target"] for o in st["rollouts"]] == [store]
            assert client.post(f"/api/ci/repos/{name}/rollback", json={"store": store},
                               headers={"Authorization": f"Bearer {read}"}).status_code == 401
            r = client.post(f"/api/ci/repos/{name}/rollback", json={"store": store}, headers=auth)
            assert r.status_code == 200 and r.json()["revert"] is None      # the store never reported it
            # The session cookie is not enough for /api/ci, and a token is not enough for the rest.
            assert client.get("/api/kiosks", headers=auth).status_code == 401
    finally:
        db._pool = saved

        async def cleanup():
            await _cleanup(name, [store])
            for t in tokens:
                await db.execute("DELETE FROM api_tokens WHERE name=%s", t)
        run(cleanup())
