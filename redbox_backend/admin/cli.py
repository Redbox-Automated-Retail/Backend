"""Administration CLI: `python -m redbox_backend admin <command> ...` (run inside any backend container).

Update Service (legacy updatemgrd):
  import-store <source>... [--repo NAME ...] [--dvd-data latest|all|none] [--no-snapshot] [--config-files]
               [--store-files] [--rewrite OLD=NEW ...]
                                     source = a kiosk .store directory, a data-<date>.zip installer seed, or a
                                     folder of seeds; each becomes an edition, the newest one is served
  editions                           list imported editions
  pin <repo> <hash|label|latest>     serve an older revision (fresh kiosks get its snapshot; nobody is moved back)
  snapshot <repo>|--all              rebuild the full-file seed snapshot of a repository head
  export <repo>... [--revision HASH|LABEL] [--edition NAME] > files.tar
                                     a repository's files at a revision (default: the served head) as a tar on
                                     stdout, paths relative to the drive root (to run kiosk programs off-kiosk)
  publish <repo> <dir> --target-path 'C:\\Program Files\\...' [--label L]
                                     add a revision from a local directory (deltas via xdelta3)
  build-seed <out.zip> [--repo NAME[@REV] ... | --all] [--edition NAME] [--add REPO DIR TARGET_PATH ...]
             [--label REPO=LABEL ...] [--no-deltas] [--retarget OLD=NEW ...] [--date MMDDYYYY]
                                     write an installer seed (data-<MMDDYYYY>.zip) of repositories at chosen
                                     revisions plus new content; format in docs/seed-zips.md
  check-seed <zip> [--compare REFERENCE.zip] [--no-apply]
                                     check a seed as updatemgrd would use it (any seed, real or built)
  repos                              list repositories and heads
  stores                             list stores and their reported heads
  store-set <number> [--enabled B] [--time-zone SERIALIZED] [--correct-time B]
  link <number> <repo> [--unlink]
  config-file-set <oid> <name> <kiosk dir> <json file>   new generation of a config file
  download-file <local file> --name N --dest-dir D [--store S] [--script lua_file]

Releases of our own builds (docs/releases.md):
  release-publish <release.zip> [--allow-new]
                                     a build from the Software repository's build/release.ps1: an unreleased
                                     revision of its repository, or a file-set revision
  rollout <repo> <hash|label|version> (--store S | --market M | --all)
                                     serve a revision to one store, a market, or every store
  rollback <repo> (--store S | --market M | --all) [--to REV]
                                     back to the previous revision; publishes a revert revision when stores
                                     already took the newer one (updatemgrd never moves back)
  release-status <repo> [--json]     revisions, rollouts, and what every store reports against what it is served
  fileset-status <fs> [--json]       file-set revisions, deployments and kiosk states
  api-token create <name> [--scope publish|deploy ...] | list | revoke <name>
                                     bearer tokens for the CI API (/api/ci/* on the dashboard)

File sets (Update Client and updatemgrd):
  fileset-create <id> <name> <version> <dir> --dest 'C:\\...' [--dep FS:MIN[:MAX]]
  fileset-deploy <fs> <rev> [--kiosk K | --market M] [--channel ucs|legacy] [--window 01:00-05:00]

Kiosk configuration (configuration.json via the Update Client):
  kconfig-set <Category> <Setting> <value> [--type string] [--kiosk K | --market M] [--rank R]
  kconfig-list [--kiosk K]

Kiosk reprovisioning (kiosk/reprovision, rb-reprovision apply <file> on the kiosk):
  kiosk-profile <kiosk id> (--address IP | --base-domain D) [--market M] [--rewrite OLD=NEW ...] [--no-ca]
               [--broker-port P] > kiosk-<id>.json

IoT:
  iot-send <kiosk|market:M|all> <command> [--version V] [--payload JSON]  (via the iot admin API)
  events [--kiosk K] [--limit N]
"""
import argparse
import asyncio
import json
import os
import pathlib
import sys
import urllib.request

from .. import apitokens, blobs, config, db, filesets, kioskprofile
from ..updatesvc import editions, releases, repo, seedzip


def _bool(s):
    return str(s).lower() in ("1", "true", "yes", "on")


def expand_sources(paths):
    """Directories that hold data-*.zip seeds expand to those zips; anything else is used as is."""
    out = []
    for p in map(pathlib.Path, paths):
        zips = sorted(p.glob("*.zip")) if p.is_dir() and not (p / ".repository").exists()             and not any(p.glob("*/.revlog")) else []
        out.extend(zips or [p])
    return out


async def import_store(args):
    sources = expand_sources(args.store)
    rewrites = [r.split("=", 1) for r in args.rewrite or []]

    async def progress(msg):
        print(msg, flush=True)
    for res in await editions.import_sources(sources, dvd_data=args.dvd_data, repos=args.repo,
                                             snapshots=not args.no_snapshot, progress=progress):
        print(json.dumps(res, default=str))
    store = sources[-1]
    if args.config_files and store.is_dir() and (store / ".configfiles").is_dir():
        for d in sorted((store / ".configfiles").iterdir()):
            cfg = d / ".config"
            if not cfg.is_file():
                continue
            text = cfg.read_text(encoding="utf-8-sig")
            for old, new in rewrites:
                text = text.replace(old, new)
            data = json.loads(text)
            await db.execute(
                "INSERT INTO upd_config_files (oid, name, path, generation, data) VALUES (%s,%s,%s,%s,%s)"
                " ON CONFLICT (oid) DO UPDATE SET name=EXCLUDED.name, path=EXCLUDED.path,"
                " generation=EXCLUDED.generation, data=EXCLUDED.data, updated_at=now()",
                int(data["OID"]), data["Name"], data["Path"], int(data["GenerationOID"]),
                db.jsonb(data["ConfigurationData"]))
            print(f"config file {data['OID']} {data['Name']} generation {data['GenerationOID']}")
    if args.store_files and store.is_dir() and (store / ".storefiles").is_dir():
        for state in sorted((store / ".storefiles").glob("*.state")):
            st = json.loads(state.read_text(encoding="utf-8-sig"))
            staged = state.with_suffix(".staged")
            if not staged.is_file():
                continue
            req = st["StoreFilePollRequest"]
            dest = st["Destination"]
            await db.execute(
                "INSERT INTO upd_store_files (id, name, path, data_id, blob) VALUES (%s,%s,%s,%s,%s)"
                " ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, path=EXCLUDED.path, data_id=EXCLUDED.data_id,"
                " blob=EXCLUDED.blob, deleted=false, modified_on=now()",
                int(req["StoreFile"]), dest.split("\\")[-1], dest, int(req["StoreFileData"]),
                blobs.put_file(staged))
            print(f"store file {req['StoreFile']} -> {dest}")


async def cmd_editions(args):
    for e in await db.fetch("SELECT * FROM upd_editions ORDER BY edition_date"):
        print(f"{e['name']:20} {e['edition_date']} {sum(e['revisions'].values()):6} revisions in {len(e['heads'])} repositories"
              f"  ({e['source']})")


async def cmd_pin(args):
    rows = await db.fetch("SELECT hash, label FROM upd_revisions WHERE repo=%s ORDER BY seq", args.repo)
    if args.revision == "latest":
        await db.execute("UPDATE upd_repositories SET serve_head=NULL WHERE name=%s", args.repo)
        print(f"{args.repo}: serving the newest revision")
        return
    match = [r for r in rows if r["hash"].startswith(args.revision.upper()) or r["label"] == args.revision]
    if not match:
        raise SystemExit(f"no revision or label {args.revision} in {args.repo}")
    head = match[-1]["hash"]
    await db.execute("UPDATE upd_repositories SET serve_head=%s WHERE name=%s", head, args.repo)
    res = await repo.build_snapshot(args.repo, head)
    print(f"{args.repo}: serving {head} ({match[-1]['label']}); snapshot {res['files']} files from {res['source']},"
          f" {len(res['problems'])} problems")


async def cmd_snapshot(args):
    names = [r["name"] for r in await db.fetch("SELECT name FROM upd_repositories ORDER BY name")] \
        if args.all else [args.repo]
    for n in names:
        res = await repo.build_snapshot(n)
        print(json.dumps({k: v for k, v in res.items() if k != "problems"} | {"problems": res["problems"][:10]}))


async def cmd_export(args):
    import tarfile
    out = tarfile.open(fileobj=sys.stdout.buffer, mode="w|")
    for name in args.repo:
        upto = None
        if args.revision:
            upto = await db.fetchval("SELECT hash FROM upd_revisions WHERE repo = %s AND (hash ILIKE %s OR label = %s)"
                                     " ORDER BY seq DESC LIMIT 1", name, args.revision + "%", args.revision)
            if not upto:
                sys.exit(f"{name}: no revision {args.revision}")
        elif not args.edition:
            upto = await repo.served_head(name)
        files, head, problems = await repo.reconstruct_head(name, upto_hash=upto, edition=args.edition)
        for (tpath, tname), (_, key) in sorted(files.items()):
            member = tarfile.TarInfo((tpath.replace("\\", "/").split(":/", 1)[-1] + "/" + tname).lstrip("/"))
            member.size = blobs.size(key)
            with open(blobs.path(key), "rb") as f:
                out.addfile(member, f)
        print(f"{name} @ {head}: {len(files)} files" + (f", problems: {problems[:5]}" if problems else ""), file=sys.stderr)
    out.close()


async def cmd_publish(args):
    root = pathlib.Path(args.dir)
    files = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root)
            sub = "\\".join(rel.parts[:-1])
            tp = args.target_path.rstrip("\\") + ("\\" + sub if sub else "")
            files.append((tp, rel.parts[-1], p))
    rev = await repo.publish_revision(args.repo, files, label=args.label, use_deltas=not args.no_deltas)
    print(rev or "no changes")


async def cmd_repos(args):
    for r in await db.fetch("SELECT r.name, r.auto_subscribe, count(v.*) AS revisions,"
                            " (SELECT hash FROM upd_revisions x WHERE x.repo=r.name ORDER BY seq DESC LIMIT 1) AS head,"
                            " (SELECT count(*) FROM upd_snapshots s WHERE s.repo=r.name) AS snapshot_files"
                            " FROM upd_repositories r LEFT JOIN upd_revisions v ON v.repo=r.name GROUP BY r.name ORDER BY r.name"):
        print(f"{r['name']:24} revs={r['revisions']:<5} head={r['head']} snapshot_files={r['snapshot_files']}"
              f" auto={r['auto_subscribe']}")


async def cmd_stores(args):
    for s in await db.fetch("SELECT * FROM upd_stores ORDER BY number"):
        print(f"store {s['number']} enabled={s['enabled']} last_check_in={s['last_check_in']}")
        for h in await db.fetch("SELECT * FROM upd_store_heads WHERE number=%s ORDER BY repo", s["number"]):
            print(f"   {h['repo']:24} {h['head']} ({h['reported_at']:%Y-%m-%d %H:%M})")


async def cmd_store_set(args):
    await db.execute("INSERT INTO upd_stores (number) VALUES (%s) ON CONFLICT DO NOTHING", args.number)
    if args.enabled is not None:
        await db.execute("UPDATE upd_stores SET enabled=%s WHERE number=%s", _bool(args.enabled), args.number)
    if args.time_zone is not None:
        await db.execute("UPDATE upd_stores SET time_zone=%s WHERE number=%s", args.time_zone or None, args.number)
    if args.correct_time is not None:
        await db.execute("UPDATE upd_stores SET correct_time=%s WHERE number=%s", _bool(args.correct_time), args.number)
    print(await db.fetchrow("SELECT * FROM upd_stores WHERE number=%s", args.number))


async def cmd_link(args):
    await db.execute("INSERT INTO upd_stores (number) VALUES (%s) ON CONFLICT DO NOTHING", args.number)
    await db.execute("INSERT INTO upd_store_repos (number, repo, linked) VALUES (%s,%s,%s) ON CONFLICT (number, repo)"
                     " DO UPDATE SET linked=EXCLUDED.linked", args.number, args.repo, not args.unlink)
    print(await repo.repositories_for_store(args.number))


async def cmd_config_file_set(args):
    data = json.loads(pathlib.Path(args.json_file).read_text(encoding="utf-8-sig"))
    if isinstance(data, dict) and "ConfigurationData" in data:
        data = data["ConfigurationData"]
    gen = await db.fetchval("SELECT coalesce(max(generation), 0) + 1 FROM upd_config_files")
    await db.execute("INSERT INTO upd_config_files (oid, name, path, generation, data) VALUES (%s,%s,%s,%s,%s)"
                     " ON CONFLICT (oid) DO UPDATE SET name=EXCLUDED.name, path=EXCLUDED.path,"
                     " generation=EXCLUDED.generation, data=EXCLUDED.data, updated_at=now()",
                     args.oid, args.name, args.kiosk_dir, gen, db.jsonb(data))
    print(f"config file {args.oid} generation {gen}")


async def cmd_download_file(args):
    key = blobs.put_file(args.file)
    script = pathlib.Path(args.script).read_text() if args.script else None
    row = await db.fetchrow("INSERT INTO upd_download_files (number, name, type, file_name, destination_path, blob,"
                            " activate_script) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                            args.store, args.name, 0, pathlib.Path(args.file).name, args.dest_dir, key, script)
    print(f"download file {row['id']} queued")


async def cmd_fileset_create(args):
    await filesets.ensure_fileset(args.id, args.name)
    root = pathlib.Path(args.dir)
    files = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            files.append((args.dest.rstrip("\\") + "\\" + "\\".join(p.relative_to(root).parts), p.read_bytes()))
    deps = []
    for d in args.dep or []:
        bits = d.split(":")
        deps.append({"DependsOnFileSetId": int(bits[0]), "DependencyType": 0, "MinimumVersion": bits[1],
                     "MaximumVersion": bits[2] if len(bits) > 2 else None})
    rev = await filesets.create_revision(args.id, args.name, args.version, files, deps)
    print(f"file set {args.id} revision {rev}")


async def cmd_fileset_deploy(args):
    ttype, target = ("kiosk", args.kiosk) if args.kiosk else ("market", args.market) if args.market else ("all", None)
    start = end = None
    if args.window:
        start, end = args.window.split("-")
    dep = await filesets.deploy(args.fs, None if args.rev == 0 else args.rev, ttype, target, args.channel,
                                activate_start=start, activate_end=end)
    print(f"deployment {dep['id']}: file set {args.fs} revision {args.rev} -> {ttype} {target or ''} ({args.channel})")
    if args.channel == "ucs" and not args.no_push:
        _iot_post("/push/deploy", {"deploymentId": dep["id"]})


async def cmd_kconfig_set(args):
    from ..iot import kioskconfig
    vid = await kioskconfig.set_value(args.category, args.setting, args.value, data_type=args.type,
                                      kiosk_id=args.kiosk, market=args.market, rank=args.rank)
    print(f"value {vid} set; configuration version {await kioskconfig.current_version()}")
    if not args.no_push:
        _iot_post("/push/config", {"kioskId": args.kiosk, "market": args.market})


async def cmd_kconfig_list(args):
    from ..iot import kioskconfig
    doc = await kioskconfig.document_for(args.kiosk or 0, None)
    print(json.dumps(doc, indent=2, default=str))


def _iot_post(path, body):
    url = os.environ.get("IOT_ADMIN_URL", f"http://rb-iot:{config.IOT_ADMIN_PORT}") + path
    try:
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            print(r.read().decode())
    except Exception as e:
        print(f"(could not notify iot service at {url}: {e})", file=sys.stderr)


async def cmd_iot_send(args):
    _iot_post("/send", {"target": args.target, "command": args.command, "version": args.version,
                        "payload": json.loads(args.payload) if args.payload else None})


async def cmd_events(args):
    rows = await db.fetch("SELECT * FROM events WHERE (%s::bigint IS NULL OR kiosk_id = %s) ORDER BY id DESC LIMIT %s",
                          args.kiosk, args.kiosk, args.limit)
    for r in reversed(rows):
        print(f"{r['created_at']:%Y-%m-%d %H:%M:%S} {r['kiosk_id'] or '-':>7} {r['source']}/{r['kind']} "
              f"{json.dumps(r['payload'], default=str)[:300]}")


async def cmd_build_seed(args):
    import datetime as dt
    if args.all:
        if args.edition:
            names = [r["repo"] for r in await db.fetch("SELECT DISTINCT repo FROM upd_edition_revisions WHERE edition=%s"
                                                       " ORDER BY repo", args.edition)]
        else:
            names = [r["repo"] for r in await db.fetch("SELECT DISTINCT repo FROM upd_revisions ORDER BY repo")]
        specs = [(n, None, args.edition) for n in names]
    else:
        specs = [(s.split("@", 1)[0], s.split("@", 1)[1] if "@" in s else None, args.edition) for s in args.repo or []]
    labels = dict(x.split("=", 1) for x in args.label or [])
    additions = [(r, seedzip.files_from_dir(d, tp), labels.get(r)) for r, d, tp in args.add or []]
    when = dt.datetime.strptime(args.date, "%m%d%Y") if args.date else None
    out = pathlib.Path(args.out)
    if out.is_dir():
        out = out / seedzip.default_name(when.date() if when else None)
    try:
        summary = await seedzip.build(out, specs, additions, when=when, retarget=[r.split("=", 1) for r in args.retarget or []],
                                      deltas=not args.no_deltas, allow_placeholders=args.allow_placeholders,
                                      progress=lambda m: print(m, file=sys.stderr, flush=True))
    except seedzip.SeedError as e:
        sys.exit(str(e))
    print(json.dumps(summary, indent=2))


async def cmd_check_seed(args):
    res = seedzip.check(args.zip, reference=args.compare, apply=not args.no_apply,
                        progress=lambda m: print(m, file=sys.stderr, flush=True))
    print(json.dumps(res, indent=2))
    if not res["ok"]:
        sys.exit(1)


def _print(obj):
    print(json.dumps(obj, indent=2, default=str))


def _release_target(args):
    if not (args.store or args.market or args.all):
        sys.exit("give --store, --market or --all")
    return args.store, args.market


async def cmd_release_publish(args):
    try:
        _print(await releases.publish_release(args.zip, allow_new=args.allow_new, actor="admin-cli"))
    except releases.ReleaseError as e:
        sys.exit(str(e))


async def cmd_rollout(args):
    store, market = _release_target(args)
    try:
        _print(await releases.rollout(args.repo, args.revision, store=store, market=market, actor="admin-cli"))
    except releases.ReleaseError as e:
        sys.exit(str(e))


async def cmd_rollback(args):
    store, market = _release_target(args)
    try:
        res = await releases.rollback(args.repo, store=store, market=market, to=args.to, actor="admin-cli")
    except releases.ReleaseError as e:
        sys.exit(str(e))
    _print(res)
    for f in res.get("not_removed") or []:
        print(f"warning: {f} was added after {res['to']} and stays on the kiosks", file=sys.stderr)


async def cmd_release_status(args):
    st = await releases.status(args.repo, limit=args.limit)
    if args.json:
        return _print(st)
    print(f"{args.repo}: default head {st['default_head']}")
    print("revisions (newest first):")
    for r in st["revisions"]:
        print(f"  {r['seq']:5} {r['hash'][:12]} {'released' if r['released'] else 'unreleased':10}"
              f" {r['version'] or '':16} {r['label'] or ''}")
    if st["rollouts"]:
        print("rollouts:")
        for o in st["rollouts"]:
            print(f"  {o['target_type']:6} {o['target']:12} -> {o['head'][:12]}  since {o['created_at']:%Y-%m-%d %H:%M}")
    print("stores:")
    for s in st["stores"]:
        print(f"  {s['store']:10} {s['market'] or '-':12} {s['state']:12} reports {(s['reported'] or '-')[:12]}"
              f" ({s['reported_label'] or '-'}), served {(s['served'] or '-')[:12]} ({s['served_label'] or '-'})")


async def cmd_fileset_status(args):
    st = await releases.fileset_status(args.fs)
    if args.json:
        return _print(st)
    for r in st["revisions"]:
        print(f"revision {r['id']:5} {r['version']:16} {r['name']}")
    for d in st["deployments"]:
        print(f"deployment {d['id']:5} revision {d['revision_id']} -> {d['target_type']} {d['target'] or ''}"
              f" ({d['channel']}) {d['created_at']:%Y-%m-%d %H:%M}")
    for k in st["kiosks"]:
        print(f"kiosk {k['kiosk_id']} ({k['channel']}): revision {k['revision_id']} {k['version'] or ''}"
              f" state {k['state']} at {k['updated_at']:%Y-%m-%d %H:%M}")


async def cmd_api_token(args):
    if args.action == "create":
        if not args.name:
            sys.exit("api-token create <name>")
        try:
            token = await apitokens.create(args.name, args.scope or ["publish"])
        except ValueError as e:
            sys.exit(str(e))
        print(token)
        print("(shown once; store it as a CI secret, e.g. RB_API_TOKEN)", file=sys.stderr)
    elif args.action == "list":
        for t in await apitokens.list_all():
            print(f"{t['name']:24} {','.join(t['scopes']):24} created {t['created_at']:%Y-%m-%d}"
                  f" last used {t['last_used_at'] or '-'}{'  REVOKED' if t['revoked_at'] else ''}")
    else:
        print(f"revoked {await apitokens.revoke(args.name)} token(s)")


async def cmd_kiosk_profile(args):
    if not args.address and not args.base_domain:
        sys.exit("give --address (hosts-file mapping of the Redbox names) or --base-domain (your own DNS names)")
    doc = kioskprofile.profile(args.kiosk_id, address=args.address, market=args.market, base_domain=args.base_domain,
                               rewrites=dict(r.split("=", 1) for r in args.rewrite or []), broker_port=args.broker_port,
                               include_ca=not args.no_ca)
    for w in doc.get("Warnings", []):
        print("warning: " + w, file=sys.stderr)
    print(json.dumps(doc, indent=2))


def parser():
    ap = argparse.ArgumentParser(prog="admin", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("import-store"); p.add_argument("store", nargs="+"); p.add_argument("--repo", action="append")
    p.add_argument("--dvd-data", choices=["latest", "all", "none"], default="latest",
                   help="which sources' dvd-data to import (it is ~1 GB per seed)")
    p.add_argument("--no-snapshot", action="store_true"); p.add_argument("--config-files", action="store_true")
    p.add_argument("--store-files", action="store_true"); p.add_argument("--rewrite", action="append")
    p.set_defaults(fn=import_store)
    p = sub.add_parser("snapshot"); p.add_argument("repo", nargs="?"); p.add_argument("--all", action="store_true")
    p.set_defaults(fn=cmd_snapshot)
    p = sub.add_parser("export"); p.add_argument("repo", nargs="+"); p.add_argument("--revision"); p.add_argument("--edition")
    p.set_defaults(fn=cmd_export)
    p = sub.add_parser("publish"); p.add_argument("repo"); p.add_argument("dir"); p.add_argument("--target-path", required=True)
    p.add_argument("--label"); p.add_argument("--no-deltas", action="store_true"); p.set_defaults(fn=cmd_publish)
    p = sub.add_parser("build-seed"); p.add_argument("out", help="zip file, or a folder for data-<MMDDYYYY>.zip")
    p.add_argument("--repo", action="append", help="NAME or NAME@REVISION (hash prefix or label)")
    p.add_argument("--all", action="store_true"); p.add_argument("--edition")
    p.add_argument("--add", nargs=3, action="append", metavar=("REPO", "DIR", "TARGET_PATH"))
    p.add_argument("--label", action="append", help="REPO=LABEL for the revision --add creates")
    p.add_argument("--no-deltas", action="store_true"); p.add_argument("--retarget", action="append")
    p.add_argument("--date", help="MMDDYYYY: entry timestamps and default file name (default now)")
    p.add_argument("--allow-placeholders", action="store_true"); p.set_defaults(fn=cmd_build_seed)
    p = sub.add_parser("check-seed"); p.add_argument("zip"); p.add_argument("--compare")
    p.add_argument("--no-apply", action="store_true"); p.set_defaults(fn=cmd_check_seed)
    sub.add_parser("repos").set_defaults(fn=cmd_repos)
    sub.add_parser("editions").set_defaults(fn=cmd_editions)
    p = sub.add_parser("pin"); p.add_argument("repo"); p.add_argument("revision", help="hash prefix, label or 'latest'")
    p.set_defaults(fn=cmd_pin)
    sub.add_parser("stores").set_defaults(fn=cmd_stores)
    p = sub.add_parser("store-set"); p.add_argument("number"); p.add_argument("--enabled")
    p.add_argument("--time-zone"); p.add_argument("--correct-time"); p.set_defaults(fn=cmd_store_set)
    p = sub.add_parser("link"); p.add_argument("number"); p.add_argument("repo"); p.add_argument("--unlink", action="store_true")
    p.set_defaults(fn=cmd_link)
    p = sub.add_parser("config-file-set"); p.add_argument("oid", type=int); p.add_argument("name")
    p.add_argument("kiosk_dir"); p.add_argument("json_file"); p.set_defaults(fn=cmd_config_file_set)
    p = sub.add_parser("download-file"); p.add_argument("file"); p.add_argument("--name", required=True)
    p.add_argument("--dest-dir", required=True); p.add_argument("--store"); p.add_argument("--script")
    p.set_defaults(fn=cmd_download_file)
    p = sub.add_parser("release-publish"); p.add_argument("zip"); p.add_argument("--allow-new", action="store_true")
    p.set_defaults(fn=cmd_release_publish)
    for name, fn in (("rollout", cmd_rollout), ("rollback", cmd_rollback)):
        p = sub.add_parser(name); p.add_argument("repo")
        if name == "rollout":
            p.add_argument("revision", help="hash prefix, label or version")
        else:
            p.add_argument("--to", help="hash prefix, label or version (default: what the target had before)")
        g = p.add_mutually_exclusive_group(); g.add_argument("--store"); g.add_argument("--market")
        g.add_argument("--all", action="store_true"); p.set_defaults(fn=fn)
    p = sub.add_parser("release-status"); p.add_argument("repo"); p.add_argument("--limit", type=int, default=10)
    p.add_argument("--json", action="store_true"); p.set_defaults(fn=cmd_release_status)
    p = sub.add_parser("fileset-status"); p.add_argument("fs", type=int); p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_fileset_status)
    p = sub.add_parser("api-token"); p.add_argument("action", choices=["create", "list", "revoke"])
    p.add_argument("name", nargs="?"); p.add_argument("--scope", action="append", choices=list(apitokens.SCOPES))
    p.set_defaults(fn=cmd_api_token)
    p = sub.add_parser("fileset-create"); p.add_argument("id", type=int); p.add_argument("name"); p.add_argument("version")
    p.add_argument("dir"); p.add_argument("--dest", required=True); p.add_argument("--dep", action="append")
    p.set_defaults(fn=cmd_fileset_create)
    p = sub.add_parser("fileset-deploy"); p.add_argument("fs", type=int); p.add_argument("rev", type=int)
    p.add_argument("--kiosk"); p.add_argument("--market"); p.add_argument("--channel", default="ucs")
    p.add_argument("--window"); p.add_argument("--no-push", action="store_true"); p.set_defaults(fn=cmd_fileset_deploy)
    p = sub.add_parser("kconfig-set"); p.add_argument("category"); p.add_argument("setting"); p.add_argument("value")
    p.add_argument("--type", default="string"); p.add_argument("--kiosk", type=int); p.add_argument("--market")
    p.add_argument("--rank", type=int); p.add_argument("--no-push", action="store_true"); p.set_defaults(fn=cmd_kconfig_set)
    p = sub.add_parser("kconfig-list"); p.add_argument("--kiosk", type=int); p.set_defaults(fn=cmd_kconfig_list)
    p = sub.add_parser("kiosk-profile"); p.add_argument("kiosk_id", type=int); p.add_argument("--address")
    p.add_argument("--base-domain"); p.add_argument("--market"); p.add_argument("--rewrite", action="append")
    p.add_argument("--broker-port", type=int, default=7890); p.add_argument("--no-ca", action="store_true")
    p.set_defaults(fn=cmd_kiosk_profile)
    p = sub.add_parser("iot-send"); p.add_argument("target"); p.add_argument("command")
    p.add_argument("--version", type=int, default=1); p.add_argument("--payload"); p.set_defaults(fn=cmd_iot_send)
    p = sub.add_parser("events"); p.add_argument("--kiosk", type=int); p.add_argument("--limit", type=int, default=50)
    p.set_defaults(fn=cmd_events)
    return ap


async def _run(args):
    await db.init(max_size=2)
    try:
        await args.fn(args)
    finally:
        await db.close()


def main(argv):
    asyncio.run(_run(parser().parse_args(argv)))
