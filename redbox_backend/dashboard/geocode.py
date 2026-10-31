"""Street address search for the dashboard's store forms, done server-side so no key reaches the browser.

  GOOGLE_MAPS_API_KEY  set: Google Places Autocomplete + Place Details (address predictions, US)
  otherwise            Photon, an OpenStreetMap geocoder made for search-as-you-type (GEOCODER_URL, default the
                       public https://photon.komoot.io/api; a self-hosted Photon works the same)

Both give {label, address1, city, county, state (postal code), zip, lat, lon}; a Google prediction has only label
and id until /api/geocode/details fills it in.
"""
import asyncio
import json
import os
import urllib.parse
import urllib.request

from fastapi import APIRouter, HTTPException

from ..catalog.stores import STATES
from . import server as srv

router = APIRouter()
STATE_CODES = {name.lower(): code for code, name in STATES.items()}
UA = "redbox-selfhosted-dashboard/1.0 (store address search)"


def _key():
    return (os.environ.get("GOOGLE_MAPS_API_KEY") or "").strip()


def _get(url, timeout=6):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _photon(q):
    base = os.environ.get("GEOCODER_URL") or "https://photon.komoot.io/api"
    data = _get(f"{base}?{urllib.parse.urlencode({'q': q, 'limit': 8, 'lang': 'en'})}")
    out = []
    for f in data.get("features") or []:
        p = f.get("properties") or {}
        if p.get("countrycode") not in (None, "US", "PR", "VI", "GU"):
            continue
        street = " ".join(x for x in (p.get("housenumber"), p.get("street")) if x) or (p.get("name") if p.get("type") == "house" else None)
        city = p.get("city") or p.get("town") or p.get("village") or p.get("district")
        state = STATE_CODES.get((p.get("state") or "").lower(), p.get("state"))
        lon, lat = (f.get("geometry") or {}).get("coordinates", [None, None])[:2]
        label = ", ".join(x for x in (street or p.get("name"), city, " ".join(y for y in (state, p.get("postcode")) if y)) if x)
        if label:
            out.append({"label": label, "address1": street, "city": city, "county": p.get("county"), "state": state,
                        "zip": p.get("postcode"), "lat": lat, "lon": lon})
    return out


def _google_search(q):
    params = {"input": q, "types": "address", "components": "country:us|country:pr", "key": _key()}
    data = _get("https://maps.googleapis.com/maps/api/place/autocomplete/json?" + urllib.parse.urlencode(params))
    if data.get("status") not in ("OK", "ZERO_RESULTS"):
        raise HTTPException(502, f"Google Maps: {data.get('status')} {data.get('error_message') or ''}".strip())
    return [{"label": p["description"], "id": p["place_id"]} for p in data.get("predictions") or []]


def _google_details(place_id):
    params = {"place_id": place_id, "fields": "address_component,geometry,formatted_address", "key": _key()}
    data = _get("https://maps.googleapis.com/maps/api/place/details/json?" + urllib.parse.urlencode(params))
    if data.get("status") != "OK":
        raise HTTPException(502, f"Google Maps: {data.get('status')}")
    r = data["result"]
    c = {t: comp for comp in r.get("address_components") or [] for t in comp.get("types") or []}
    name = lambda t, short=False: (c.get(t) or {}).get("short_name" if short else "long_name")  # noqa: E731
    loc = (r.get("geometry") or {}).get("location") or {}
    zip_ = name("postal_code")
    if zip_ and name("postal_code_suffix"):
        zip_ += "-" + name("postal_code_suffix")
    return {"label": r.get("formatted_address"), "address1": " ".join(x for x in (name("street_number"), name("route")) if x) or None,
            "city": name("locality") or name("sublocality") or name("postal_town"), "county": name("administrative_area_level_2"),
            "state": name("administrative_area_level_1", True), "zip": zip_, "lat": loc.get("lat"), "lon": loc.get("lng")}


@router.get("/api/geocode/search")
async def search(q: str):
    q = q.strip()
    if len(q) < 3:
        return srv.ok({"provider": "none", "results": []})
    provider = "Google Maps" if _key() else "OpenStreetMap"
    try:
        results = await asyncio.to_thread(_google_search if _key() else _photon, q)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"address search ({provider}) is unreachable: {e}")
    return srv.ok({"provider": provider, "results": results})


@router.get("/api/geocode/details")
async def details(id: str):
    if not _key():
        raise HTTPException(400, "details are only needed for Google Maps results")
    try:
        return srv.ok(await asyncio.to_thread(_google_details, id))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, f"Google Maps is unreachable: {e}")
