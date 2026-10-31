#!/usr/bin/env python3
"""Client for the backend's CI API (/api/ci/* on the admin dashboard), for CI jobs and release scripts.

Standard library only. The token comes from RB_API_TOKEN (`admin api-token create ci --scope publish`), the
dashboard URL from --url or RB_ADMIN_URL (default https://localhost:8443), and the CA that signs the
dashboard's certificate from --ca or RB_CA_FILE (`rb.ps1 export-ca` writes certs\\ca.crt).

  rb-release.py publish <release.zip> [--allow-new]
  rb-release.py status <repo>
  rb-release.py rollout <repo> <hash|label|version> (--store S | --market M | --all)
  rb-release.py rollback <repo> (--store S | --market M | --all) [--to REV]
  rb-release.py fileset-status <fs>
  rb-release.py fileset-deploy <fs> <revision> (--kiosk K | --market M | --all) [--channel ucs|legacy] [--window 01:00-05:00]

Exit status 0 on success; the server's JSON answer is printed either way.
"""
import argparse
import json
import os
import ssl
import sys
import urllib.error
import urllib.request
import uuid


def _request(args, method, path, body=None, files=None):
    url = args.url.rstrip("/") + path
    headers = {"Authorization": "Bearer " + args.token}
    data = None
    if files is not None:
        boundary = uuid.uuid4().hex
        parts = []
        for name, value in (body or {}).items():
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
        for name, path_ in files.items():
            with open(path_, "rb") as f:
                content = f.read()
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}";'
                         f' filename="{os.path.basename(path_)}"\r\nContent-Type: application/zip\r\n\r\n'.encode()
                         + content + b"\r\n")
        data = b"".join(parts) + f"--{boundary}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    ctx = None
    if url.startswith("https:"):
        ctx = ssl.create_default_context(cafile=args.ca) if args.ca else ssl.create_default_context()
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=args.timeout, context=ctx) as r:
            answer, status = r.read(), r.status
    except urllib.error.HTTPError as e:
        answer, status = e.read(), e.code
    try:
        print(json.dumps(json.loads(answer), indent=2))
    except ValueError:
        print(answer.decode(errors="replace"))
    return 0 if status < 400 else 1


def _target(args, kiosk_key="store"):
    value = getattr(args, kiosk_key)
    if not (value or args.market or args.all):
        sys.exit(f"give --{kiosk_key}, --market or --all")
    return {kiosk_key: value, "market": args.market, "all": bool(args.all)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=os.environ.get("RB_ADMIN_URL", "https://localhost:8443"))
    ap.add_argument("--ca", default=os.environ.get("RB_CA_FILE"))
    ap.add_argument("--timeout", type=int, default=600)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("publish"); p.add_argument("zip"); p.add_argument("--allow-new", action="store_true")
    p = sub.add_parser("status"); p.add_argument("repo")
    for name in ("rollout", "rollback"):
        p = sub.add_parser(name); p.add_argument("repo")
        if name == "rollout":
            p.add_argument("revision")
        else:
            p.add_argument("--to")
        g = p.add_mutually_exclusive_group(); g.add_argument("--store"); g.add_argument("--market")
        g.add_argument("--all", action="store_true")
    p = sub.add_parser("fileset-status"); p.add_argument("fs", type=int)
    p = sub.add_parser("fileset-deploy"); p.add_argument("fs", type=int); p.add_argument("revision", type=int)
    g = p.add_mutually_exclusive_group(); g.add_argument("--kiosk"); g.add_argument("--market")
    g.add_argument("--all", action="store_true")
    p.add_argument("--channel", default="ucs", choices=["ucs", "legacy"]); p.add_argument("--window")
    args = ap.parse_args(argv)
    args.token = os.environ.get("RB_API_TOKEN", "")
    if not args.token:
        sys.exit("set RB_API_TOKEN")

    if args.cmd == "publish":
        return _request(args, "POST", "/api/ci/releases", {"allow_new": str(args.allow_new).lower()}, {"file": args.zip})
    if args.cmd == "status":
        return _request(args, "GET", f"/api/ci/repos/{args.repo}/status")
    if args.cmd == "rollout":
        return _request(args, "POST", f"/api/ci/repos/{args.repo}/rollout", _target(args) | {"revision": args.revision})
    if args.cmd == "rollback":
        return _request(args, "POST", f"/api/ci/repos/{args.repo}/rollback", _target(args) | {"to": args.to})
    if args.cmd == "fileset-status":
        return _request(args, "GET", f"/api/ci/filesets/{args.fs}/status")
    return _request(args, "POST", f"/api/ci/filesets/{args.fs}/deploy", _target(args, "kiosk") | {
        "revision": args.revision, "channel": args.channel, "window": args.window})


if __name__ == "__main__":
    sys.exit(main())
