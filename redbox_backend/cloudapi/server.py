"""Run the cloud API app: `python -m redbox_backend cloudapi [--port N] [--no-tls]`."""
import argparse
import os

import uvicorn

from .. import config


def main(argv):
    ap = argparse.ArgumentParser(prog="cloudapi")
    ap.add_argument("--port", type=int, default=config.CLOUD_API_PORT)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--no-tls", action="store_true", help="serve plain HTTP (tests / reverse proxy)")
    args = ap.parse_args(argv)
    kwargs = {}
    if not args.no_tls:
        kwargs = {"ssl_certfile": os.path.join(config.CERT_DIR, "server.crt"),
                  "ssl_keyfile": os.path.join(config.CERT_DIR, "server.key")}
    uvicorn.run("redbox_backend.cloudapi.app:create_app", factory=True, host=args.host, port=args.port,
                log_level=config.LOG_LEVEL.lower(), access_log=False, proxy_headers=False, **kwargs)
