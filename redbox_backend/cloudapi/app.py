"""FastAPI application serving every kiosk cloud HTTPS API on one host.

The kiosk reaches all of these through different host names (Proxy Service, Transaction API, Inventory
API, Kiosk Data Service, Ad Server, Product Reels, Bluefin, IoT certificate service, S3). Their paths do
not collide, so one flat route table serves them all whatever host name was used.

Routers live in `routers/`; each module exposes `router` (an APIRouter). Add new modules to ROUTERS.
"""
import importlib
import logging
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .. import db

log = logging.getLogger("cloudapi")

ROUTERS = [
    "redbox_backend.cloudapi.routers.iotcert",
    "redbox_backend.cloudapi.routers.s3",
    "redbox_backend.cloudapi.routers.admin",
]
# Kiosk API routers (transaction, inventory, proxy, kiosk data, ads, reels, bluefin, ...) register here.
OPTIONAL_ROUTERS = [
    "redbox_backend.cloudapi.routers.transaction",
    "redbox_backend.cloudapi.routers.inventory",
    "redbox_backend.cloudapi.routers.proxy",
    "redbox_backend.cloudapi.routers.kioskdata",
    "redbox_backend.cloudapi.routers.ads",
    "redbox_backend.cloudapi.routers.reels",
    "redbox_backend.cloudapi.routers.bluefin",
    "redbox_backend.cloudapi.routers.fraud",
    "redbox_backend.cloudapi.routers.tickets",
]


def create_app():
    app = FastAPI(title="Redbox self-hosted cloud APIs", docs_url="/_docs", openapi_url="/_openapi.json")

    @app.on_event("startup")
    async def _startup():
        await db.init()

    @app.on_event("shutdown")
    async def _shutdown():
        await db.close()

    @app.middleware("http")
    async def _log_requests(request: Request, call_next):
        start = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled error on %s %s", request.method, request.url.path)
            response = JSONResponse({"StatusCode": 500, "Message": "Internal Server Error"}, status_code=500)
        log.info("%s %s%s host=%s kiosk=%s -> %s (%.0f ms)", request.method, request.url.path,
                 ("?" + request.url.query) if request.url.query else "", request.headers.get("host"),
                 request.headers.get("x-redbox-kioskid"), response.status_code, (time.monotonic() - start) * 1000)
        return response

    @app.get("/_health")
    async def _health():
        return {"ok": True}

    for name in ROUTERS:
        app.include_router(importlib.import_module(name).router)
    for name in OPTIONAL_ROUTERS:
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as e:
            if e.name == name:
                continue
            raise
        app.include_router(module.router)
    return app
