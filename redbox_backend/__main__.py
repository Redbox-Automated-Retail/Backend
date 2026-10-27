"""Entry point: `python -m redbox_backend <service> [args]`."""
import logging
import sys

from . import config

SERVICES = {
    "updatesvc": "redbox_backend.updatesvc.server",
    "cloudapi": "redbox_backend.cloudapi.server",
    "iot": "redbox_backend.iot.server",
    "certs": "redbox_backend.certs",
    "admin": "redbox_backend.admin.cli",
}


def main(argv):
    if len(argv) < 1 or argv[0] in ("-h", "--help") or argv[0] not in SERVICES:
        print("usage: python -m redbox_backend {%s} [args]" % ",".join(SERVICES))
        return 0 if argv[:1] in (["-h"], ["--help"], []) else 2
    logging.basicConfig(level=getattr(logging, config.LOG_LEVEL.upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    module = __import__(SERVICES[argv[0]], fromlist=["main"])
    return module.main(argv[1:]) or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
