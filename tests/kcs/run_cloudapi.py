"""Run the cloud API on plain HTTP for local tests, also on Windows.

psycopg's async mode cannot use Windows' default ProactorEventLoop, so this runs uvicorn on a
SelectorEventLoop. Usage (cwd = repository root):  python tests/kcs/run_cloudapi.py [port]
"""
import asyncio
import logging
import os
import selectors
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

import uvicorn  # noqa: E402


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18080
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = uvicorn.Config("redbox_backend.cloudapi.app:create_app", factory=True, host="127.0.0.1", port=port,
                            log_level="info", access_log=False)
    server = uvicorn.Server(config)
    if sys.platform == "win32":
        asyncio.run(server.serve(), loop_factory=lambda: asyncio.SelectorEventLoop(selectors.SelectSelector()))
    else:
        asyncio.run(server.serve())


if __name__ == "__main__":
    main()
