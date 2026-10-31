"""Catalog edits shared by the admin CLI and the dashboard: titles, product groups, box art, barcodes.

Every row is written as Lua text in Redbox's layout (lua.encode) with its parsed copy; the catalog tables'
triggers mark the catalog changed, so the next build stamps new files. Conventions follow the real data and the
operator scripts this replaces (add_new_item.py, add_new_barcode*.py, get_next_product_id.py):

  * a new title gets the next product id (highest ProductCatalog key + 1) and every field the other titles
    have, in their order, with an empty default (0, "", false, nil, { }) where none is given;
  * box art lives in the Cache table; the engine extracts each image once by name, so an image is named by the
    SHA-1 of its bytes (new content = new name) and the title's image_file points at it;
  * product groups: type 1 = title rollup (DVD / Blu-ray / 4K of one title), 3 = multi-disc, 6 = multi-disc
    workaround; their XREF tables (TitleRollupXREF, MultiDiscXREF, MultiDiscWorkaroundXREF) are kept in step;
  * barcodes are cloud_barcodes rows; ones added or changed here are source 'added' and survive a re-import.
"""
import datetime as dt
import decimal
import hashlib
import os
import re

from .. import blobs, db
from . import lua, store

TITLES = "ProductCatalog"
GROUPS = "ProductGroup"
GROUP_XREF = {1: "TitleRollupXREF", 3: "MultiDiscXREF", 6: "MultiDiscWorkaroundXREF"}
GROUP_TYPES = {1: "title rollup", 3: "multi-disc", 6: "multi-disc workaround"}
NEW_TITLE_DATES = ("release_date", "national_street_date", "sort_date", "merchandise_date")   # default: today
IMAGE_TYPES = {b"\xff\xd8": ".jpg", b"\x89P": ".png", b"GI": ".gif"}


class EditError(ValueError):
    pass


# ------------------------------------------------------------------------------------------- raw rows

async def get_row(tbl, key):
    row = await db.fetchrow("SELECT key, ord, lua, value FROM catalog_rows WHERE tbl=%s AND key=%s", tbl, str(key))
    if row is None:
        return None
    try:
        row["parsed"] = lua.decode(row["lua"])   # keeps key order and number text, unlike the jsonb copy
    except lua.LuaError:
        row["parsed"] = None
    return row


async def put_row(tbl, key, value=None, text=None):
    """Insert or replace a row from a value (dict/list) or from Lua text (checked by parsing it)."""
    if not await db.fetchval("SELECT 1 FROM catalog_tables WHERE name=%s", tbl):
        raise EditError(f"no table {tbl} in the catalog")
    if tbl == "Cache":
        raise EditError("Cache holds images: use put_image")
    if text is None:
        try:
            text = lua.encode(value)
        except lua.LuaError as e:
            raise EditError(f"{tbl}[{key}]: {e}")
    try:
        parsed = lua.decode(text)
    except lua.LuaError as e:
        raise EditError(f"{tbl}[{key}]: not a Lua table: {e}")
    if tbl == "Version" and len(text) > 64:
        raise EditError("Version values are VarChar(64)")
    await db.execute(
        "INSERT INTO catalog_rows (tbl, key, ord, lua, value) VALUES (%s, %s,"
        " (SELECT coalesce(max(ord), -1) + 1 FROM catalog_rows WHERE tbl=%s), %s, %s)"
        " ON CONFLICT (tbl, key) DO UPDATE SET lua = EXCLUDED.lua, value = EXCLUDED.value",
        tbl, str(key), tbl, text, db.jsonb(lua.plain(parsed)))
    return parsed


async def delete_row(tbl, key):
    return await db.execute("DELETE FROM catalog_rows WHERE tbl=%s AND key=%s", tbl, str(key))


async def _next_key(tbl):
    v = await db.fetchval("SELECT max(key::bigint) FROM catalog_rows WHERE tbl=%s AND key ~ '^[0-9]{1,18}$'", tbl)
    return (v or 999) + 1


# --------------------------------------------------------------------------------------------- titles

async def next_product_id():
    return await _next_key(TITLES)


async def _template():
    """Field order and empty defaults of a new title: the fields most titles have, in their usual order."""
    rows = await db.fetch("SELECT lua FROM catalog_rows WHERE tbl=%s ORDER BY ord", TITLES)
    parsed, counts = [], {}
    for r in rows:
        try:
            v = lua.decode(r["lua"])
        except lua.LuaError:
            continue
        if isinstance(v, dict):
            parsed.append(v)
            for k in v:
                counts[k] = counts.get(k, 0) + 1
    common = {k for k, n in counts.items() if n * 2 > len(parsed)}
    best = next((v for v in parsed if common <= set(v)), {})
    return {k: None if k.endswith("_date") else _empty(x) for k, x in best.items() if k in common}

def _empty(v):
    if isinstance(v, bool):
        return False
    if isinstance(v, int):
        return 0
    if isinstance(v, (float, decimal.Decimal)):
        return decimal.Decimal("0.00")
    if isinstance(v, str):
        return ""
    if isinstance(v, list):
        return []
    if isinstance(v, dict):
        return {}
    return None


def _coerce(fields):
    """Values from forms/JSON: genres as a list of ids -> {"<id>": true}; lists of names from text."""
    out = dict(fields)
    if isinstance(out.get("genres"), (list, tuple)):
        out["genres"] = {str(int(g)): True for g in out["genres"]}
    for k in ("starring", "directors"):
        if isinstance(out.get(k), str):
            out[k] = [x.strip() for x in out[k].split(",") if x.strip()]
    for k in ("product_type_id", "rating_id", "product_id", "coming_soon_days", "stars", "sub_platform_id",
              "disc_number"):
        if isinstance(out.get(k), str) and out[k].strip().lstrip("-").isdigit():
            out[k] = int(out[k])
    return out


async def save_title(fields, product_id=None):
    """Create (product_id None) or change a title; `fields` are ProductCatalog keys. Returns the product id."""
    fields = _coerce(fields)
    if product_id is None and fields.get("product_id") is not None:
        product_id = int(fields["product_id"])
    existing = await get_row(TITLES, product_id) if product_id is not None else None
    if existing is None:
        if not fields.get("long_name"):
            raise EditError("a new title needs long_name")
        if fields.get("product_type_id") is None:
            raise EditError("a new title needs product_type_id")
        pid = int(product_id) if product_id is not None else await next_product_id()
        value = await _template()
        value.update(product_id=pid, sort_name=fields.get("sort_name") or fields["long_name"])
    else:
        pid = int(product_id)
        value = existing["parsed"] if isinstance(existing["parsed"], dict) else {}
    for k, v in fields.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k):
            raise EditError(f"bad field name {k!r}")
        if k.endswith("_date"):
            # the engine reads dates with ParseExact("yyyyMMdd" / "yyyyMMddHHmmss"); anything else drops the title
            v = None if v in ("", None) else str(v)
            if v is not None and not re.fullmatch(r"\d{8}(\d{6})?", v):
                raise EditError(f"{k}: dates are yyyyMMdd or yyyyMMddHHmmss, not {v!r}")
        value[k] = v
    if existing is None:
        today = dt.date.today().strftime("%Y%m%d000000")
        for k in NEW_TITLE_DATES:
            if value.get(k) is None and k in value:
                value[k] = today
    value["product_id"] = pid
    if value.get("product_type_id") is not None and not await get_row("ProductType", value["product_type_id"]):
        raise EditError(f"no product type {value['product_type_id']} (see the ProductType table)")
    await put_row(TITLES, pid, value)
    await store.refresh_products()
    return pid


async def delete_title(product_id, barcodes=False):
    """Remove a title, its group memberships and XREF rows; its image if no other title uses it."""
    pid = int(product_id)
    row = await get_row(TITLES, pid)
    if row is None:
        raise EditError(f"no title {pid}")
    image = (row["value"] or {}).get("image_file")
    await delete_row(TITLES, pid)
    for g in await db.fetch("SELECT key FROM catalog_rows WHERE tbl=%s AND value->'product_id_list' @> %s",
                            GROUPS, db.jsonb([pid])):
        grp = (await get_row(GROUPS, g["key"]))["parsed"]
        ids = [x for x in grp.get("product_id_list") or [] if int(x) != pid]
        if ids:
            await save_group(int(g["key"]), grp.get("product_group_name"), grp.get("product_group_type_id"), ids)
        else:
            await delete_group(int(g["key"]))
    for xref in GROUP_XREF.values():
        await delete_row(xref, pid)
    if image and not await db.fetchval("SELECT 1 FROM catalog_rows WHERE tbl=%s AND value->>'image_file' = %s",
                                       TITLES, image):
        await db.execute("DELETE FROM catalog_images WHERE name=%s", image)
    removed = await db.execute("DELETE FROM cloud_barcodes WHERE product_id=%s", pid) if barcodes else 0
    await store.refresh_products()
    return {"product_id": pid, "barcodes_removed": removed,
            "barcodes_left": await db.fetchval("SELECT count(*) FROM cloud_barcodes WHERE product_id=%s", pid)}


async def search_titles(q=None, limit=50, offset=0):
    where, args = "", []
    if q:
        if q.strip().isdigit():
            where, args = " AND (key = %s OR value->>'long_name' ILIKE %s)", [q.strip(), f"%{q}%"]
        else:
            where, args = " AND (value->>'long_name' ILIKE %s OR value->>'sort_name' ILIKE %s)", [f"%{q}%", f"%{q}%"]
    base = f" FROM catalog_rows WHERE tbl='{TITLES}'{where}"
    rows = await db.fetch(
        "SELECT key::bigint AS product_id, value->>'long_name' AS long_name, value->>'product_type_id' AS product_type_id,"
        " value->>'release_date' AS release_date, value->>'image_file' AS image_file, value->>'studio' AS studio,"
        " value->>'rating_id' AS rating_id" + base +
        " ORDER BY CASE WHEN key ~ '^[0-9]{1,18}$' THEN key::bigint END DESC NULLS LAST LIMIT %s OFFSET %s",
        *args, limit, offset)
    return {"total": await db.fetchval("SELECT count(*)" + base, *args), "rows": rows}


# --------------------------------------------------------------------------------------------- groups

async def save_group(group_id, name, type_id, product_ids):
    """Create (group_id None) or replace a product group and keep its XREF table in step."""
    type_id = int(type_id)
    ids = []
    for p in product_ids:
        p = int(p)
        if not await get_row(TITLES, p):
            raise EditError(f"no title {p}")
        if p not in ids:
            ids.append(p)
    if not ids:
        raise EditError("a product group needs at least one title")
    gid = int(group_id) if group_id is not None else await _next_key(GROUPS)
    old = await get_row(GROUPS, gid)
    old_v = old["parsed"] if old and isinstance(old["parsed"], dict) else {}
    old_type, old_ids = old_v.get("product_group_type_id"), [int(x) for x in old_v.get("product_id_list") or []]
    value = {"product_group_id": gid, "product_group_name": name or old_v.get("product_group_name") or "",
             "product_group_type_id": type_id, "product_id_list": ids}
    await put_row(GROUPS, gid, value)
    if old_type in GROUP_XREF:
        for p in old_ids:
            if p not in ids or old_type != type_id:
                await _drop_xref(GROUP_XREF[old_type], p, gid)
    if type_id in GROUP_XREF:
        for p in ids:
            await put_row(GROUP_XREF[type_id], p, {"product_id": p, "product_group_id": gid})
    await store.refresh_products()
    return gid


async def _drop_xref(tbl, pid, gid):
    row = await get_row(tbl, pid)
    if row and isinstance(row["parsed"], dict) and int(row["parsed"].get("product_group_id") or 0) == gid:
        await delete_row(tbl, pid)


async def delete_group(group_id):
    gid = int(group_id)
    row = await get_row(GROUPS, gid)
    if row is None:
        raise EditError(f"no product group {gid}")
    v = row["parsed"] if isinstance(row["parsed"], dict) else {}
    if v.get("product_group_type_id") in GROUP_XREF:
        for p in v.get("product_id_list") or []:
            await _drop_xref(GROUP_XREF[v["product_group_type_id"]], int(p), gid)
    await delete_row(GROUPS, gid)
    await store.refresh_products()


async def groups_of(product_id):
    return await db.fetch("SELECT key::bigint AS product_group_id, value FROM catalog_rows WHERE tbl=%s"
                          " AND value->'product_id_list' @> %s ORDER BY key", GROUPS, db.jsonb([int(product_id)]))


# ---------------------------------------------------------------------------------------------- images

def image_name(data):
    ext = next((e for magic, e in IMAGE_TYPES.items() if data.startswith(magic)), None)
    if ext is None:
        raise EditError("box art must be a JPEG, PNG or GIF image")
    return hashlib.sha1(data).hexdigest().upper() + ext


async def put_image(data, product_id=None):
    """Store box art in the Cache table (named by its SHA-1) and point the title at it. Returns the name."""
    name = image_name(data)
    key = blobs.put_bytes(data)
    await db.execute("INSERT INTO catalog_images (name, ord, blob, type, size) VALUES (%s,"
                     " (SELECT coalesce(max(ord), -1) + 1 FROM catalog_images), %s, 0, %s) ON CONFLICT (name) DO NOTHING",
                     name, key, len(data))
    if product_id is not None:
        await save_title({"image_file": name}, product_id)
    return name


async def image_bytes(name):
    row = await db.fetchrow("SELECT blob FROM catalog_images WHERE name=%s", name)
    return blobs.get_bytes(row["blob"]) if row and blobs.exists(row["blob"]) else None


# -------------------------------------------------------------------------------------------- barcodes

async def set_barcode(barcode, product_id, status=0, rental_count=None):
    from . import inventory
    barcode = str(barcode).strip()
    inventory.compress(barcode)   # inventory.data only holds digits
    if not await get_row(TITLES, int(product_id)):
        raise EditError(f"no title {product_id}")
    await db.execute(
        "INSERT INTO cloud_barcodes (barcode, product_id, status, rental_count, source) VALUES (%s,%s,%s,%s,'added')"
        " ON CONFLICT (barcode) DO UPDATE SET product_id = EXCLUDED.product_id, status = EXCLUDED.status,"
        " rental_count = coalesce(%s, cloud_barcodes.rental_count), source = 'added', updated_at = now()",
        barcode, int(product_id), int(status), int(rental_count or 0), rental_count)
    await db.execute("DELETE FROM catalog_barcode_dups WHERE barcode=%s", barcode)
    return await db.fetchrow("SELECT * FROM cloud_barcodes WHERE barcode=%s", barcode)


async def delete_barcode(barcode):
    await db.execute("DELETE FROM catalog_barcode_dups WHERE barcode=%s", str(barcode))
    return await db.execute("DELETE FROM cloud_barcodes WHERE barcode=%s", str(barcode))


# ------------------------------------------------------------------------------------------------ OMDb

# OMDb genres with other names in the Genres table; ratings are matched on ProductRating's name without "-".
GENRE_ALIASES = {"Sci-Fi": "Sci-Fi & Fantasy", "Fantasy": "Sci-Fi & Fantasy", "Thriller": "Suspense",
                 "Mystery": "Suspense", "Music": "Musical", "Animation": "Animation"}


async def _names(tbl, id_key, name_key):
    rows = await db.fetch("SELECT value FROM catalog_rows WHERE tbl=%s ORDER BY ord", tbl)
    out = {}
    for r in rows:
        v = r["value"] or {}
        if v.get(name_key) is not None and v.get(id_key) is not None:
            out.setdefault(str(v[name_key]).upper().replace("-", ""), int(v[id_key]))
    return out

_MONTHS = {m: i for i, m in enumerate("Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split(), 1)}


def omdb_fields(o, ratings, genres):
    """ProductCatalog fields from an OMDb answer. ratings / genres: {NAME (upper case, no "-"): id} from the
    ProductRating and Genres tables. The caller adds product_type_id and anything else."""
    def date(s):
        parts = (s or "").split()
        if len(parts) == 3 and parts[1] in _MONTHS:
            return f"{parts[2]}{_MONTHS[parts[1]]:02d}{int(parts[0]):02d}000000"
        return f"{o.get('Year', '')[:4]}0101000000" if (o.get("Year") or "")[:4].isdigit() else None
    minutes = int(re.sub(r"\D", "", o.get("Runtime") or "") or 0)
    released = date(o.get("Released"))
    rated = (o.get("Rated") or "N/A").upper().replace("-", "")
    plot = (o.get("Plot") or "").replace("N/A", "")
    gross = re.sub(r"[^\d.]", "", o.get("BoxOffice") or "")
    fields = {"long_name": o.get("Title"), "sort_name": re.sub(r"^(The|A|An) ", "", o.get("Title") or ""),
              "description": plot + (f"\r\n\r\nRated {o.get('Rated')} by the Motion Picture Association of America.\r\n"
                                     if o.get("Rated") not in (None, "N/A") else ""),
              "running_time": f"{minutes // 60}:{minutes % 60:02d}" if minutes else "",
              "starring": [x.strip() for x in (o.get("Actors") or "").split(",") if x.strip() and x.strip() != "N/A"],
              "directors": [x.strip() for x in (o.get("Director") or "").split(",") if x.strip() and x.strip() != "N/A"],
              "rating_id": ratings.get(rated, ratings.get("NR")),
              "genres": sorted({genres[n] for n in (GENRE_ALIASES.get(g.strip(), g.strip()).upper().replace("-", "")
                                                   for g in (o.get("Genre") or "").split(",")) if n in genres}),
              "box_office_gross": round(float(gross) / 1e6) if gross else 0,
              "stars": round(float(o["imdbRating"]) / 2) if (o.get("imdbRating") or "N/A") != "N/A" else 0}
    if o.get("Production") not in (None, "N/A"):
        fields["studio"] = o["Production"]
    if released:
        fields.update(release_date=released, national_street_date=released, sort_date=released,
                      merchandise_date=released)
    return fields


async def omdb_lookup(title=None, year=None, imdb_id=None, api_key=None):
    """Look a movie up on OMDb (OMDB_API_KEY). Returns {"omdb": answer, "fields": ..., "poster": url}."""
    import httpx
    key = api_key or os.environ.get("OMDB_API_KEY")
    if not key:
        raise EditError("set OMDB_API_KEY to look titles up on OMDb")
    params = {"apikey": key, "r": "json", "plot": "full"}
    if imdb_id:
        params["i"] = imdb_id
    else:
        params.update(t=title, type="movie")
        if year:
            params["y"] = str(year)
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get("https://www.omdbapi.com/", params=params)
    data = r.json() if r.status_code == 200 else {}
    if data.get("Response") != "True":
        raise EditError(f"OMDb: {data.get('Error') or r.status_code}")
    poster = data.get("Poster") if (data.get("Poster") or "N/A") != "N/A" else None
    fields = omdb_fields(data, await _names("ProductRating", "rating_id", "name"),
                         await _names("Genres", "genre_id", "genre_name"))
    return {"omdb": data, "fields": fields, "poster": poster}


async def fetch_image(url):
    import httpx
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        r = await c.get(url)
    r.raise_for_status()
    if len(r.content) > 8 << 20:
        raise EditError("image larger than 8 MB")
    image_name(r.content)
    return r.content
