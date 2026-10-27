"""Kiosk configuration (configuration.json) served through GetConfigChanges.

Every value belongs to a segment: All (rank 0), Market:<name> (rank 10) or Kiosk:<id> (rank 20). A kiosk
receives every value of its segments; the client picks the highest rank (KioskConfigurationSettingExtensions),
so a kiosk value beats a market value beats the default. Encryption is not used (EncryptionType null).

The version is global and bumped on every change. A kiosk is current when it reports that version and the
same ConfigurationVersionHash (computed exactly like ConfigurationService.ComputeConfigurationSettingValuesHashValue).
Otherwise it gets the whole document (RemoveAllExistingConfigurationSettingValues = true).
"""
import base64
import datetime as dt
import functools
import hashlib
import logging

from .. import config, db

log = logging.getLogger("kioskconfig")

# Defaults pointing every client at this backend. Host names follow config.py (original Redbox names).
API_KEY = "selfhosted"
DEFAULTS = {
    "KioskClientDefault": {
        "TransactionApiUrl": "https://transactionservice.kiosk.prodp.redbox.com",
        "TransactionApiKey": API_KEY,
        "InventoryApiUrl": "https://kioskinventory.kiosk.prod.redbox.com",
        "InventoryApiKey": API_KEY,
        "KioskDataServiceUrl": "https://dataservice.kiosk.prod.redbox.com",
        "KioskDataServiceAPIKey": API_KEY,
        "ProductReelsApiUrl": "https://reels.product.prod.redbox.com",
        "PricingActive": ("true", "bool"),
        "PlanogramSyncEnabled": ("true", "bool"),
    },
    "KioskClientProxyApi": {
        "ProxyApiUrl": "https://proxyservice.kiosk.prod.redbox.com",
        "ProxyApiKey": API_KEY,
    },
    "KioskClientAds": {
        "AdServerUrl": "https://adserver.kiosk.prod.redbox.com/api",
        "AdServerAPIKey": API_KEY,
        "AdsActive": ("true", "bool"),
    },
    "Operations": {
        "SyncTimestamp": ("true", "bool"),
        "SyncTimezone": ("false", "bool"),
        "SegmentUpdateEnabled": ("true", "bool"),
    },
    "FileSets": {"FileSetDownloadEnabled": ("true", "bool")},
    "KioskHealth": {"ReportFailedPingsEnabled": ("true", "bool")},
}


async def _segment(segmentation, key, name, rank):
    await db.execute("INSERT INTO cfg_segments (segmentation_name, key, name, rank) VALUES (%s,%s,%s,%s)"
                     " ON CONFLICT DO NOTHING", segmentation, key, name, rank)
    return await db.fetchval("SELECT id FROM cfg_segments WHERE segmentation_name=%s AND key=%s", segmentation, key)


async def current_version():
    return await db.fetchval("SELECT version FROM cfg_version WHERE id = 1")


async def bump_version():
    return await db.fetchval("UPDATE cfg_version SET version = version + 1 WHERE id = 1 RETURNING version")


async def set_value(category, setting, value, data_type="string", kiosk_id=None, market=None, rank=None,
                    bump=True):
    if kiosk_id:
        seg = await _segment("Kiosk", str(kiosk_id), f"Kiosk {kiosk_id}", 20 if rank is None else rank)
    elif market:
        seg = await _segment("Market", market, market, 10 if rank is None else rank)
    else:
        seg = await _segment("All", "*", "All kiosks", 0)
    sid = await db.fetchval("INSERT INTO cfg_settings (category, name, data_type) VALUES (%s,%s,%s)"
                            " ON CONFLICT (category, name) DO UPDATE SET data_type = EXCLUDED.data_type"
                            " RETURNING setting_id", category, setting, data_type)
    vid = await db.fetchval("INSERT INTO cfg_values (setting_id, segment_id, value) VALUES (%s,%s,%s)"
                            " ON CONFLICT (setting_id, segment_id) DO UPDATE SET value = EXCLUDED.value"
                            " RETURNING value_id", sid, seg, None if value is None else str(value))
    if bump:
        await bump_version()
    return vid


async def seed_defaults():
    if await db.fetchval("SELECT count(*) FROM cfg_values"):
        return
    for category, settings in DEFAULTS.items():
        for name, value in settings.items():
            value, dtype = value if isinstance(value, tuple) else (value, "string")
            await set_value(category, name, value, dtype, bump=False)
    await bump_version()
    log.info("seeded default kiosk configuration")


def _u(value):
    return value.strftime("%Y-%m-%d %H:%M:%SZ") if value else ""


def _cmp_name(a, b):
    # .NET string.CompareTo is culture-aware (case-insensitive first); close enough for setting names.
    ka, kb = (a.lower(), a.swapcase()), (b.lower(), b.swapcase())
    return (ka > kb) - (ka < kb)


def version_hash(doc):
    parts = [f"{doc['KioskId']},{doc['ConfigurationVersion']}"]
    for cat in sorted(doc["Categories"], key=functools.cmp_to_key(lambda a, b: _cmp_name(a["Name"], b["Name"]))):
        parts.append("," + cat["Name"])
        for s in sorted(cat["Settings"], key=functools.cmp_to_key(lambda a, b: _cmp_name(a["Name"], b["Name"]))):
            parts.append(f",{s['SettingId']},{s['Name']},{s['DataType']}")
            for v in sorted(s["SettingValues"], key=lambda v: v["ConfigurationSettingValueId"]):
                enc = f"{v['_enc_name']}," if v.get("EncryptionType") is not None else ""
                parts.append(f",{v['ConfigurationSettingValueId']},{enc}{v['Value'] or ''},{v['Rank']},"
                             f"{_u(v['_eff'])},{_u(v['_exp'])}")
    return base64.b64encode(hashlib.sha512("".join(parts).encode("utf-8")).digest()).decode()


async def document_for(kiosk_id, market):
    version = await current_version()
    rows = await db.fetch(
        "SELECT s.setting_id, s.category, s.name, s.data_type, v.value_id, v.value, v.encryption_type,"
        " v.effective_at, v.expires_at, g.id AS segment_id, g.name AS segment_name, g.segmentation_name, g.rank"
        " FROM cfg_values v JOIN cfg_settings s ON s.setting_id = v.setting_id JOIN cfg_segments g ON g.id = v.segment_id"
        " WHERE g.segmentation_name = 'All' OR (g.segmentation_name = 'Market' AND g.key = %s)"
        "    OR (g.segmentation_name = 'Kiosk' AND g.key = %s)"
        " ORDER BY s.category, s.name, v.value_id", market or "", str(kiosk_id))
    cats = {}
    for r in rows:
        cat = cats.setdefault(r["category"], {"Name": r["category"], "Settings": {}})
        st = cat["Settings"].setdefault(r["name"], {"Name": r["name"], "SettingId": r["setting_id"],
                                                    "DataType": r["data_type"], "SettingValues": []})
        st["SettingValues"].append({
            "ConfigurationSettingValueId": r["value_id"], "EncryptionType": r["encryption_type"],
            "_enc_name": "EncryptType1" if r["encryption_type"] == 0 else str(r["encryption_type"]),
            "Value": r["value"], "SegmentId": r["segment_id"], "SegmentName": r["segment_name"],
            "SegmentationName": r["segmentation_name"], "Rank": r["rank"],
            "EffectiveDateTime": r["effective_at"].strftime("%Y-%m-%dT%H:%M:%S"),
            "ExpireDateTime": r["expires_at"].strftime("%Y-%m-%dT%H:%M:%S") if r["expires_at"] else None,
            "_eff": r["effective_at"], "_exp": r["expires_at"]})
    doc = {"KioskId": int(kiosk_id), "ConfigurationVersion": version,
           "Categories": [{"Name": c["Name"], "Settings": list(c["Settings"].values())} for c in cats.values()]}
    doc["ConfigurationVersionHash"] = version_hash(doc)
    for c in doc["Categories"]:
        for s in c["Settings"]:
            for v in s["SettingValues"]:
                for k in ("_enc_name", "_eff", "_exp"):
                    v.pop(k, None)
    return doc


async def changes_response(request, market):
    kiosk_id = int(request.get("KioskId") or 0)
    doc = await document_for(kiosk_id, market)
    current = int(request.get("CurrentConfigurationVersionId") or 0)
    await db.execute("INSERT INTO cfg_kiosk_state (kiosk_id, reported_version) VALUES (%s,%s) ON CONFLICT (kiosk_id)"
                     " DO UPDATE SET reported_version = EXCLUDED.reported_version, updated_at = now()", kiosk_id, current)
    if current == doc["ConfigurationVersion"] and request.get("ConfigurationVersionHash") == doc["ConfigurationVersionHash"]:
        return {"PageNumber": None, "PageCount": None, "VersionIsCurrent": True,
                "ConfigurationVersionHash": doc["ConfigurationVersionHash"], "RemovedConfigurationSettingValueIds": [],
                "RemoveAllExistingConfigurationSettingValues": False, "OriginalConfigurationVersionId": current,
                "NewConfigurationSettingValues": None}
    return {"PageNumber": None, "PageCount": None, "VersionIsCurrent": False,
            "ConfigurationVersionHash": doc["ConfigurationVersionHash"], "RemovedConfigurationSettingValueIds": [],
            "RemoveAllExistingConfigurationSettingValues": True, "OriginalConfigurationVersionId": current,
            "NewConfigurationSettingValues": doc}


async def activated(request):
    kid = int(request.get("KioskId") or 0)
    await db.execute("INSERT INTO cfg_kiosk_state (kiosk_id, activated_version, activated_at) VALUES (%s,%s,now())"
                     " ON CONFLICT (kiosk_id) DO UPDATE SET activated_version = EXCLUDED.activated_version,"
                     " activated_at = now(), updated_at = now()", kid, request.get("ConfigurationVersionId"))


async def status(kiosk_id):
    row = await db.fetchrow("SELECT enable_updates FROM cfg_kiosk_status WHERE kiosk_id=%s", int(kiosk_id or 0))
    return {"ConfigurationStatus": {"EnableUpdates": True if row is None else row["enable_updates"]}}


async def segments(kiosk_id, market):
    rows = await db.fetch("SELECT * FROM cfg_segments WHERE segmentation_name='All' OR (segmentation_name='Market'"
                          " AND key=%s) OR (segmentation_name='Kiosk' AND key=%s) ORDER BY rank", market or "",
                          str(kiosk_id))
    return {"Segments": [{"Id": r["id"], "Name": r["name"], "Key": r["key"], "SegmentationName": r["segmentation_name"],
                          "Rank": r["rank"]} for r in rows]}
