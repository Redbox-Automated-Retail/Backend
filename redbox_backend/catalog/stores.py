"""Stores, banners and markets: the profile.data tables Store, Banner and Market (catalog_rows), edited here and
written into profile.data with the rest of the catalog on the next build.

The kiosk installer lists the stores offered to it (cloud_installer_kiosks, Proxy API /api/installer/getpending*:
state, then banner, then store) and the store picked gives the kiosk its id; the engine then reads its address,
tax rates, return time, market and banner from its Store row. An offer is made from the Store row, so the two agree.

Store rows look like the real data (field order and empty values as there):
  { store_id = 10000 , address = "6143 Peach St Erie, PA 16509-3441" , return_time = "09:00PM" , rental_tax_rate = 6 ,
    purchase_tax_rate = 6 , purchase_price = 5 , vendor_id = nil , market_id = 125 , banner_id = 1282 , sell_thru = true ,
    open_date = "20070610120000" , inv_sync = false , address1 = "6143 Peach St" , address2 = nil , city = "Erie" ,
    county = "Erie County" , state = "PA" , zip = "16509-3441" , collection_method = 6 , ... }
"""
import asyncio
import datetime as dt

from .. import blobs, db
from . import edit, lua

STORE, BANNER, MARKET = "Store", "Banner", "Market"
TABLES = (STORE, BANNER, MARKET)
# Field order of a Store row in the real data, with the default a new store gets.
STORE_TEMPLATE = [("store_id", None), ("address", ""), ("return_time", "09:00PM"), ("rental_tax_rate", 0),
                  ("purchase_tax_rate", 0), ("purchase_price", 5), ("vendor_id", None), ("market_id", None),
                  ("banner_id", None), ("sell_thru", True), ("open_date", None), ("inv_sync", False), ("address1", ""),
                  ("address2", None), ("city", None), ("county", None), ("state", None), ("zip", None),
                  ("collection_method", 6), ("digital_purchase_tax_rate", None), ("service_fee", None),
                  ("service_fee_tax_rate", 0), ("return_only_mode_date", None)]
NUMBERS = {"rental_tax_rate", "purchase_tax_rate", "purchase_price", "digital_purchase_tax_rate", "service_fee",
           "service_fee_tax_rate"}
INTEGERS = {"store_id", "vendor_id", "market_id", "banner_id", "collection_method"}
# The installer groups stores by state with numeric ids: a fixed numbering of the postal codes.
STATES = {"AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado",
          "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia",
          "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
          "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts",
          "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana",
          "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico",
          "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
          "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
          "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington",
          "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico", "VI": "Virgin Islands",
          "GU": "Guam"}
STATE_IDS = {code: i + 1 for i, code in enumerate(sorted(STATES))}


class StoreError(ValueError):
    pass


async def tables_present():
    return {r["name"] for r in await db.fetch("SELECT name FROM catalog_tables WHERE name = ANY(%s)", list(TABLES))}


def full_address(v):
    """The `address` field as in the real data: "6143 Peach St Erie, PA 16509-3441"."""
    street = " ".join(x for x in (v.get("address1"), v.get("address2")) if x)
    place = f"{v.get('state') or ''} {v.get('zip') or ''}".strip()
    city = f"{v['city']}, {place}" if v.get("city") and place else (v.get("city") or place)
    return " ".join(x for x in (street, city) if x)


def _clean(field, value):
    if value in ("", None):
        return None
    if field in INTEGERS:
        return int(value)
    if field in NUMBERS:
        n = float(value)
        return int(n) if n.is_integer() else n
    if field in ("sell_thru", "inv_sync"):
        return value if isinstance(value, bool) else str(value).lower() in ("1", "true", "yes", "on")
    if field in ("open_date", "return_only_mode_date"):
        s = "".join(c for c in str(value) if c.isdigit())
        if len(s) == 8:
            s += "000000"
        if len(s) != 14:
            raise StoreError(f"{field}: a date (yyyyMMdd), not {value!r}")
        return s
    if field == "state":
        return str(value).strip().upper()
    return str(value).strip()


async def save_store(fields):
    """Create or change a Store row from form fields. Returns the stored value."""
    if STORE not in await tables_present():
        raise StoreError("no Store table: import a catalog or the stores from seed data first")
    try:
        sid = int(fields["store_id"])
    except (KeyError, TypeError, ValueError):
        raise StoreError("store_id (a number) required")
    row = await edit.get_row(STORE, sid)
    value = row["parsed"] if row and isinstance(row["parsed"], dict) else {k: d for k, d in STORE_TEMPLATE}
    for k, _ in STORE_TEMPLATE:
        if k in fields and k not in ("store_id", "address"):
            value[k] = _clean(k, fields[k])
    for k in ("market_id", "banner_id"):
        tbl = MARKET if k == "market_id" else BANNER
        if value.get(k) is not None and not await edit.get_row(tbl, value[k]):
            raise StoreError(f"no {tbl.lower()} {value[k]}")
    value["store_id"] = sid
    value["address"] = full_address(value)
    await edit.put_row(STORE, sid, value)
    await sync_offer(sid)
    return value


async def save_named(tbl, key, name):
    """Create or rename a Banner or Market row; key None = the next free id. Returns the key."""
    field = "banner" if tbl == BANNER else "market"
    if tbl not in await tables_present():
        raise StoreError(f"no {tbl} table: import a catalog or the stores from seed data first")
    if not (name or "").strip():
        raise StoreError("a name is required")
    if key in (None, ""):
        key = await edit._next_key(tbl)
    key = int(key)
    await edit.put_row(tbl, key, {f"{field}_id": key, f"{field}_name": name.strip()})
    for sid in await db.fetch(f"SELECT key FROM catalog_rows WHERE tbl = 'Store' AND value->>'{field}_id' = %s"
                              " AND key::bigint IN (SELECT id FROM cloud_installer_kiosks)", str(key)):
        await sync_offer(int(sid["key"]))
    return key


async def delete_store(sid):
    await db.execute("DELETE FROM cloud_installer_kiosks WHERE id = %s", int(sid))
    return await edit.delete_row(STORE, sid)


async def _names(store):
    banner = await edit.get_row(BANNER, store.get("banner_id")) if store.get("banner_id") is not None else None
    market = await edit.get_row(MARKET, store.get("market_id")) if store.get("market_id") is not None else None
    return ((banner or {}).get("value") or {}).get("banner_name"), ((market or {}).get("value") or {}).get("market_name")


async def set_offer(sid, offered, due_time=None, kaseya_market=None):
    """Offer a store to the kiosk installer (or stop offering it). The entry is made from its Store row."""
    sid = int(sid)
    if not offered:
        await db.execute("UPDATE cloud_installer_kiosks SET pending = false WHERE id = %s", sid)
        return
    row = await edit.get_row(STORE, sid)
    if row is None:
        raise StoreError(f"no store {sid}")
    await db.execute("INSERT INTO cloud_installer_kiosks (id, state_id, state_name, banner_id, banner_name, pending)"
                     " VALUES (%s, 0, '', 0, '', true) ON CONFLICT (id) DO UPDATE SET pending = true", sid)
    if due_time is not None or kaseya_market is not None:
        await db.execute("UPDATE cloud_installer_kiosks SET due_time = coalesce(%s, due_time),"
                         " kaseya_market_name = coalesce(%s, kaseya_market_name) WHERE id = %s",
                         due_time, kaseya_market, sid)
    await sync_offer(sid)


async def sync_offer(sid):
    """Bring a store's installer entry (if it has one) and its location in line with its Store row."""
    row = await edit.get_row(STORE, sid)
    if row is None or not isinstance(row["parsed"], dict):
        return
    v = row["parsed"]
    banner, market = await _names(v)
    state = (v.get("state") or "").upper()
    await db.execute(
        "UPDATE cloud_installer_kiosks SET state_id = %s, state_name = %s, banner_id = %s, banner_name = %s, address = %s,"
        " city = %s, zip = %s, market_name = %s WHERE id = %s",
        STATE_IDS.get(state, 0), STATES.get(state, state or "Unknown"), v.get("banner_id") or 0, banner or "(no banner)",
        " ".join(x for x in (v.get("address1"), v.get("address2")) if x) or v.get("address"), v.get("city"),
        v.get("zip"), market, int(sid))
    if await db.fetchval("SELECT 1 FROM cloud_installer_kiosks WHERE id = %s", int(sid)) or \
            await db.fetchval("SELECT 1 FROM kiosks WHERE kiosk_id = %s", int(sid)):
        # the installer's next call (getopsmarket) and the dashboard read the location
        await db.execute(
            "INSERT INTO cloud_kiosk_locations (kiosk_id, address, city, state, zip, ops_market) VALUES (%s,%s,%s,%s,%s,%s)"
            " ON CONFLICT (kiosk_id) DO UPDATE SET address = EXCLUDED.address, city = EXCLUDED.city, state = EXCLUDED.state,"
            " zip = EXCLUDED.zip, ops_market = coalesce(EXCLUDED.ops_market, cloud_kiosk_locations.ops_market)",
            int(sid), v.get("address1") or v.get("address"), v.get("city"), state or None, v.get("zip"), market)
        await db.execute("UPDATE kiosks SET market = coalesce(%s, market), banner = coalesce(%s, banner) WHERE kiosk_id = %s",
                         market, banner, int(sid))


async def search(q=None, offered=None, limit=100, offset=0):
    where, args = ["s.tbl = 'Store'"], []
    if q and q.strip():
        q = q.strip()
        where.append("(s.key = %s OR s.value->>'address' ILIKE %s OR s.value->>'city' ILIKE %s OR b.value->>'banner_name' ILIKE %s"
                     " OR m.value->>'market_name' ILIKE %s OR s.value->>'state' = upper(%s))")
        args += [q, f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%", q]
    if offered is not None:
        where.append("i.pending" if offered else "coalesce(i.pending, false) = false")
    base = (" FROM catalog_rows s LEFT JOIN catalog_rows b ON b.tbl = 'Banner' AND b.key = s.value->>'banner_id'"
            " LEFT JOIN catalog_rows m ON m.tbl = 'Market' AND m.key = s.value->>'market_id'"
            " LEFT JOIN cloud_installer_kiosks i ON i.id::text = s.key"
            " LEFT JOIN kiosks k ON k.kiosk_id::text = s.key WHERE " + " AND ".join(where))
    rows = await db.fetch(
        "SELECT s.key::bigint AS store_id, s.value AS store, b.value->>'banner_name' AS banner, m.value->>'market_name' AS market,"
        " i.pending AS offered, i.due_time, i.kaseya_market_name, k.last_seen_at, s.edited_at" + base +
        " ORDER BY (k.last_seen_at IS NULL), coalesce(i.pending, false) DESC, s.edited_at DESC NULLS LAST,"
        " CASE WHEN s.key ~ '^[0-9]{1,18}$' THEN s.key::bigint END LIMIT %s OFFSET %s", *args, min(limit, 500), offset)
    return {"total": await db.fetchval("SELECT count(*)" + base, *args), "rows": rows}


async def lookups():
    async def named(tbl, field):
        return [{"id": int(r["key"]), "name": (r["value"] or {}).get(f"{field}_name")} for r in await db.fetch(
            "SELECT key, value FROM catalog_rows WHERE tbl = %s AND key ~ '^[0-9]{1,18}$' ORDER BY value->>%s",
            tbl, f"{field}_name")]
    return {"banners": await named(BANNER, "banner"), "markets": await named(MARKET, "market"),
            "states": [{"code": c, "name": n} for c, n in sorted(STATES.items(), key=lambda x: x[1])],
            "present": sorted(await tables_present())}


# ------------------------------------------------------------------------------------------ import

def _read_tables(profile_path, wanted):
    from .store import _vistadb
    Database, dumpjson, _ = _vistadb()
    out = {"tables": [], "rows": []}
    with Database(str(profile_path)) as vdb:
        for ti, t in enumerate(vdb.tables.values()):
            if t.name not in wanted:
                continue
            out["tables"].append((t.name, ti, dumpjson.table_schema(t)))
            for ord_, (key, value) in enumerate(vdb.iter_rows(t)):
                try:
                    parsed = lua.plain(lua.decode(value)) if value is not None else None
                except lua.LuaError:
                    parsed = None
                out["rows"].append((t.name, key, ord_, value if value is not None else "", parsed))
    return out


async def import_tables(revision=None, folder=None, tables=TABLES, progress=None):
    """Replace the Store / Banner / Market tables with those of a seed's profile.data: a dvd-data revision of the
    Update Service (default its served head; every imported data zip is one) or a folder with profile.data. The
    rest of the catalog is left alone. Stores offered to the installer are brought in line afterwards."""
    import pathlib
    from . import store as cstore
    progress = progress or cstore._noop
    if folder:
        path, source = pathlib.Path(folder) / cstore.PROFILE, f"folder {folder}"
    else:
        from ..updatesvc import repo as repomod
        head = revision or await repomod.served_head(cstore.REPO)
        if not head:
            raise StoreError(f"{cstore.REPO} has no revisions: import a data zip first (Data zips)")
        files, head, problems = await repomod.reconstruct_head(cstore.REPO, upto_hash=head)
        found = {name.lower(): blob for (_, name), (_, blob) in files.items()}
        if cstore.PROFILE not in found:
            raise StoreError(f"{cstore.REPO}@{head[:10]}: no {cstore.PROFILE} ({'; '.join(problems[:2]) or 'not in it'})")
        path, source = blobs.path(found[cstore.PROFILE]), f"{cstore.REPO}@{head[:10]}"
    await progress(f"reading {', '.join(tables)} from {source}")
    got = await asyncio.to_thread(_read_tables, path, set(tables))
    if not got["tables"]:
        raise StoreError(f"{source}: profile.data has none of {', '.join(tables)}")
    async with db.pool().connection() as conn:
        async with conn.transaction():
            await conn.execute("INSERT INTO catalog_state (id) VALUES (1) ON CONFLICT DO NOTHING")
            for name, ord_, schema in got["tables"]:
                # a catalog not imported yet: the tables go after the ones there
                await conn.execute(
                    "INSERT INTO catalog_tables (name, ord, schema) VALUES (%s, coalesce((SELECT max(ord) + 1 FROM"
                    " catalog_tables), %s), %s) ON CONFLICT (name) DO UPDATE SET schema = EXCLUDED.schema",
                    (name, ord_, db.jsonb(schema)))
                await conn.execute("DELETE FROM catalog_rows WHERE tbl = %s", (name,))
            async with conn.cursor() as cur:
                async with cur.copy("COPY catalog_rows (tbl, key, ord, lua, value) FROM STDIN") as copy:
                    for t, k, o, v, p in got["rows"]:
                        await copy.write_row((t, k, o, v, db.jsonb(p) if p is not None else None))
    counts = {n: sum(1 for r in got["rows"] if r[0] == n) for n, _, _ in got["tables"]}
    for r in await db.fetch("SELECT id FROM cloud_installer_kiosks"):
        await sync_offer(r["id"])
    await db.record_event(None, "dashboard", "stores-import", {"source": source, "rows": counts,
                                                               "at": dt.datetime.now(dt.timezone.utc).isoformat()})
    await progress(f"imported from {source}: " + ", ".join(f"{n} {k}" for k, n in counts.items()))
    return {"source": source, "rows": counts}
