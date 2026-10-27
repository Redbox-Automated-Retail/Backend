"""Ad Server stand-in (KCS `AdServerClient` / `AdServerAnalyticsClient`, base `KioskClientAds.AdServerUrl`).

KCS builds `AdServerUrl.TrimEnd('/') + "/api/ads/..."`; the default AdServerUrl is
`http://localhost:5005/api`, which gives `/api/api/ads/...`. Both `/api/ads/...` and `/api/api/ads/...`
(and the same for analytics) are served, so AdServerUrl may be the bare host or end in `/api`.

Every ad handed out is an impression (`cloud_ad_impressions`, Ad.Id = impression id) that proof of play
or expiry closes. Creatives come from `cloud_assets` (generated placeholder PNG when no content).
"""
import logging
import random
import urllib.parse
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ... import db
from .. import media
from ..common import base_url, body_json, check_key, ci, kiosk_id, now, parse_dt, reply, standard, to_int, touch

log = logging.getLogger("cloudapi.ads")
router = APIRouter()
SOURCE = "adserver"
LOCATIONS = ["Unknown", "Welcome", "TitleDetail", "VSM", "ZipCode", "Pickup", "Return", "EmailNotification",
             "Vending", "PleaseWait", "Multiple"]
PREFIXES = ("/api", "/api/api")
ASSET_PATH = "/cloud-files/ads"


def _location_name(value):
    if value is None:
        return None
    n = to_int(value)
    if n is not None and 0 <= n < len(LOCATIONS):
        return LOCATIONS[n]
    for name in LOCATIONS:
        if name.lower() == str(value).lower():
            return name
    return None


async def _pick(location, kiosk):
    rows = await db.fetch(
        "SELECT a.*, s.mime_type FROM cloud_ads a JOIN cloud_assets s ON s.asset_id = a.asset_id "
        "WHERE a.active AND a.location = %s AND (a.kiosk_ids IS NULL OR %s = ANY(a.kiosk_ids)) "
        "AND (a.start_date IS NULL OR a.start_date <= now()) AND (a.end_date IS NULL OR a.end_date > now())",
        location, kiosk or 0)
    if not rows:
        return None
    return random.choices(rows, weights=[max(1, r["weight"]) for r in rows])[0]


async def _issue(request, ad, kiosk, via):
    imp = uuid.uuid4()
    await db.execute("INSERT INTO cloud_ad_impressions (id, ad_id, kiosk_id, location, via) VALUES (%s, %s, %s, %s, %s)",
                     imp, ad["id"], kiosk, ad["location"], via)
    ext = "jpg" if "jpeg" in (ad["mime_type"] or "") else "png"
    return {"Id": str(imp), "AssetId": ad["asset_id"], "Provider": ad["provider"], "Expirable": ad["expirable"],
            "AssetUrl": f"{base_url(request)}{ASSET_PATH}/{urllib.parse.quote(ad['asset_id'])}.{ext}",
            "Location": ad["location"], "MimeType": ad["mime_type"]}


def _locations(request):
    names = []
    for v in request.query_params.getlist("locations"):
        for part in v.split(","):
            n = _location_name(part.strip())
            if n and n not in ("Unknown", "Multiple") and n not in names:
                names.append(n)
    return names


async def get_ads(request, via):
    check_key(request, SOURCE)
    kiosk = kiosk_id(request)
    await touch(kiosk)
    out = []
    for loc in _locations(request) or [n for n in LOCATIONS if n not in ("Unknown", "Multiple")]:
        ad = await _pick(loc, kiosk)
        if ad:
            out.append(await _issue(request, ad, kiosk, via))
    return reply({"Data": out})


async def get_ad(location: str, request: Request):
    check_key(request, SOURCE)
    kiosk = kiosk_id(request)
    await touch(kiosk)
    name = _location_name(location)
    ad = await _pick(name, kiosk) if name else None
    if ad is None:
        return reply({"Error": f"No ad for location {location}"}, 404)
    return reply({"Data": await _issue(request, ad, kiosk, "get")})


async def proof_of_play(request: Request):
    check_key(request, SOURCE)
    body = await body_json(request, {}) or {}
    kiosk = kiosk_id(request, body)
    ad_id = ci(body, "AdvertisementId")
    shown = parse_dt(ci(body, "DisplayedTime")) or now()
    try:
        imp = uuid.UUID(str(ad_id))
    except ValueError:
        imp = None
    n = 0
    if imp:
        n = await db.execute("UPDATE cloud_ad_impressions SET played_at = %s, displayed_ms = %s WHERE id = %s",
                             shown, to_int(ci(body, "DisplayedMilliseconds")), imp)
    await db.record_event(kiosk, SOURCE, "proofofplay", body)
    if not n:
        return reply({"Error": f"Unknown advertisement {ad_id}"}, 404)
    return standard()


async def expire(ad_id: str, request: Request):
    check_key(request, SOURCE)
    kiosk = kiosk_id(request)
    try:
        imp = uuid.UUID(ad_id)
    except ValueError:
        return reply({"Error": f"Unknown advertisement {ad_id}"}, 404)
    n = await db.execute("UPDATE cloud_ad_impressions SET expired_at = COALESCE(expired_at, now()) WHERE id = %s", imp)
    if not n:
        return reply({"Error": f"Unknown advertisement {ad_id}"}, 404)
    await touch(kiosk)
    return standard()


async def analytics(kind: str, request: Request):
    check_key(request, SOURCE)
    body = await body_json(request, {})
    kiosk = kiosk_id(request, body if isinstance(body, dict) else None)
    await db.record_event(kiosk, SOURCE, "analytics_" + kind.lower(), body)
    await touch(kiosk)
    return standard(KinesisResponse=None)


async def list_ads(request: Request):
    return await get_ads(request, "list")


async def cache_ads(request: Request):
    return await get_ads(request, "cache")


for _p in PREFIXES:
    router.add_api_route(_p + "/ads", list_ads, methods=["GET"])
    router.add_api_route(_p + "/ads/cache", cache_ads, methods=["GET"])
    router.add_api_route(_p + "/ads/proofOfPlay", proof_of_play, methods=["POST"])
    router.add_api_route(_p + "/ads/proofofplay", proof_of_play, methods=["POST"])
    router.add_api_route(_p + "/ads/{ad_id}/expire", expire, methods=["POST"])
    router.add_api_route(_p + "/ads/{location}", get_ad, methods=["GET"])
    router.add_api_route(_p + "/analytics/{kind}", analytics, methods=["POST"])


@router.get(ASSET_PATH + "/{name}")
async def ad_asset(name: str):
    asset_id = name.rsplit(".", 1)[0]
    row = await db.fetchrow("SELECT * FROM cloud_assets WHERE asset_id = %s", asset_id)
    if row is None:
        return Response(status_code=404)
    if row["content"] is not None:
        return Response(bytes(row["content"]), media_type=row["mime_type"])
    return Response(media.png(row["width"], row["height"], row["color"]), media_type="image/png")
