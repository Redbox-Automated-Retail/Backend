"""Update Service process: srcp:// command port plus the HTTP content server BITS downloads from.

`python -m redbox_backend updatesvc [--no-tls]`
  * TLS command port UPDATE_SERVICE_PORT (7002) for `srcp://`; optional plain port UPDATE_SERVICE_PLAIN_PORT
    for `rcp://` (or `--no-tls` to make the main port plain);
  * HTTP on UPDATE_CONTENT_PORT (7080): /changesets/<repo>/<file>.tar, /blobs/<sha1>, /s3/<key> (legacy
    file sets). Starlette's FileResponse answers HEAD and Range requests, which BITS needs.
"""
import argparse
import asyncio
import logging
import os
import pathlib

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from .. import blobs, certs, config, db, filesets
from . import commands, rcp, repo

log = logging.getLogger("updatesvc")


def content_app():
    app = FastAPI(title="Update Service content", docs_url=None, redoc_url=None, openapi_url=None)

    def _send(p: pathlib.Path, root: pathlib.Path):
        p = p.resolve()
        if not str(p).startswith(str(root.resolve())) or not p.is_file():
            raise HTTPException(404)
        return FileResponse(p, media_type="application/octet-stream")

    @app.api_route("/changesets/{name}/{file}", methods=["GET", "HEAD"])
    async def changeset(name: str, file: str):
        return _send(repo.changeset_dir() / name / file, repo.changeset_dir())

    @app.api_route("/blobs/{key}", methods=["GET", "HEAD"])
    async def blob(key: str):
        return _send(blobs.path(key), blobs.root())

    @app.api_route("/s3/{key:path}", methods=["GET", "HEAD"])
    async def s3(key: str):
        return _send(filesets.s3_root() / key, filesets.s3_root())

    @app.get("/_health")
    async def health():
        return {"ok": True}

    return app


async def run(args):
    await db.init()
    certs.ensure_all()
    server = rcp.RcpServer(commands.handle, name="update-service")
    tls = None if args.no_tls or not config.UPDATE_SERVICE_TLS else rcp.server_ssl_context(
        os.path.join(config.CERT_DIR, "server.crt"), os.path.join(config.CERT_DIR, "server.key"))
    servers = [await server.serve("0.0.0.0", config.UPDATE_SERVICE_PORT, tls)]
    if config.UPDATE_SERVICE_PLAIN_PORT:
        servers.append(await server.serve("0.0.0.0", config.UPDATE_SERVICE_PLAIN_PORT, None))
    http = uvicorn.Server(uvicorn.Config(content_app(), host="0.0.0.0", port=config.UPDATE_CONTENT_PORT,
                                         log_level="warning", access_log=False))
    log.info("content server on :%s, base URL %s", config.UPDATE_CONTENT_PORT, config.UPDATE_CONTENT_BASE_URL)
    await asyncio.gather(http.serve(), *(s.serve_forever() for s in servers))


def main(argv):
    ap = argparse.ArgumentParser(prog="updatesvc")
    ap.add_argument("--no-tls", action="store_true", help="plain rcp:// on the main port")
    asyncio.run(run(ap.parse_args(argv)))
