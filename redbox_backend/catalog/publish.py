"""Build the catalog into a dvd-data revision and serve it to every kiosk, a market or one kiosk.

The revision goes into the Update Service's dvd-data repository like any published revision (updatesvc/repo.py:
VCDIFF deltas against the previous profile.data / inventory.data when smaller, a new snapshot for kiosks without
the repository). For a market or a kiosk it is published unreleased and rolled out to that target only
(updatesvc/releases.py), so other kiosks stay on what they have.
"""
import datetime as dt
import pathlib
import shutil

from .. import config, db
from ..updatesvc import repo as repomod
from . import store

ENGINE_DATA = store.ENGINE_DATA


async def _noop(msg):
    pass


async def target_path(repo=store.REPO):
    """The kiosk folder the repository installs profile.data to (the engine's data folder by default)."""
    return await db.fetchval(
        "SELECT c.target_path FROM upd_changes c JOIN upd_revisions r ON r.repo = c.repo AND r.hash = c.rev_hash"
        " WHERE c.repo = %s AND lower(c.target_name) = 'profile.data' ORDER BY r.seq DESC LIMIT 1", repo) or ENGINE_DATA


def work_dir():
    p = pathlib.Path(config.DATA_DIR) / "catalog" / dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    p.mkdir(parents=True, exist_ok=True)
    return p


async def publish(scope="all", target=None, label=None, repo=store.REPO, progress=_noop, actor=None):
    """Build and publish. scope: all | market | store (target = market name / store number).

    Returns {"revision": hash or None (nothing changed), "build": build summary, "rollout": ...}."""
    if scope not in ("all", "market", "store"):
        raise ValueError("scope is all, market or store")
    if scope != "all" and not target:
        raise ValueError(f"name the {scope}")
    work = work_dir()
    try:
        built = await store.build(work, progress)
        tp = await target_path(repo)
        files = [(tp, store.PROFILE, work / store.PROFILE), (tp, store.INVENTORY, work / store.INVENTORY)]
        label = label or "catalog-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M")
        await progress(f"publishing to {repo} ({tp}) for {scope} {target or ''}".rstrip())
        rev = await repomod.publish_revision(repo, files, label=label, released=scope == "all")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    rollout = None
    if rev and scope != "all":
        from ..updatesvc import releases
        rollout = await releases.rollout(repo, rev, store=target if scope == "store" else None,
                                         market=target if scope == "market" else None, actor=actor)
    result = {"revision": rev, "label": label if rev else None, "scope": scope, "target": target,
              "build": built, "rollout": rollout,
              "at": dt.datetime.now(dt.timezone.utc).isoformat()}
    await db.execute("UPDATE catalog_state SET last_build=%s WHERE id=1", db.jsonb(result))
    await progress(f"revision {rev}" if rev else "nothing changed since the last published revision")
    return result


async def build_for_seed(out_dir, progress=_noop):
    """profile.data and inventory.data for admin build-seed --catalog: (repo, files, label)."""
    built = await store.build(out_dir, progress)
    tp = await target_path()
    return [(tp, store.PROFILE, pathlib.Path(out_dir) / store.PROFILE),
            (tp, store.INVENTORY, pathlib.Path(out_dir) / store.INVENTORY)], built
