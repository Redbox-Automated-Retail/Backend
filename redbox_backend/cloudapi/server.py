"""Run the cloud API app: `python -m redbox_backend cloudapi [--port N] [--no-tls] [--http-port N]`.

Serves HTTPS on --port and, unless --http-port is 0, the same app on plain HTTP as well (one process, one event
loop, two uvicorn servers). Only the HTTPS server runs the app's startup/shutdown (database pool).
"""
import argparse
import asyncio
import os

import uvicorn

from .. import config
from .app import create_app


async def _serve(servers):
    main, *extra = servers
    task = asyncio.create_task(main.serve())
    while not main.started and not task.done():
        await asyncio.sleep(0.05)
    tasks = [task] + [asyncio.create_task(s.serve()) for s in extra]
    await asyncio.gather(*tasks)


def main(argv):
    ap = argparse.ArgumentParser(prog="cloudapi")
    ap.add_argument("--port", type=int, default=config.CLOUD_API_PORT)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--no-tls", action="store_true", help="serve plain HTTP (tests / reverse proxy)")
    ap.add_argument("--http-port", type=int, default=config.CLOUD_API_HTTP_PORT,
                    help="additional plain-HTTP port (0 = none)")
    args = ap.parse_args(argv)
    kwargs = {}
    if not args.no_tls:
        kwargs = {"ssl_certfile": os.path.join(config.CERT_DIR, "server.crt"),
                  "ssl_keyfile": os.path.join(config.CERT_DIR, "server.key")}
    common = dict(host=args.host, log_level=config.LOG_LEVEL.lower(), access_log=False, proxy_headers=False)
    app = create_app()
    servers = [uvicorn.Server(uvicorn.Config(app, port=args.port, **common, **kwargs))]
    if args.http_port and args.http_port != args.port:
        servers.append(uvicorn.Server(uvicorn.Config(app, port=args.http_port, lifespan="off", **common)))
    asyncio.run(_serve(servers))
