"""Catalog import and build: profile.data + inventory.data <-> the catalog tables (schema/60_catalog.sql).

Import keeps everything needed to write both files again: every profile.data table with its VistaDB definition,
every row's Lua text in file order, the box art (into the blob store), inventory.data's header and records
(cloud_barcodes, plus catalog_barcode_dups for the barcodes a real file repeats). Building an unedited catalog
gives a profile.data that reads back the same as the imported one (vistadb3 writes a new file; the engine and
the VistaDB DLL see the same tables, rows and indexes) and an inventory.data identical byte for byte.
"""
import asyncio
import datetime as dt
import logging
import os
import pathlib

from psycopg.rows import tuple_row

from .. import blobs, db
from . import inventory, lua

log = logging.getLogger("catalog")

REPO = "dvd-data"
ENGINE_DATA = "C:\\Program Files\\Redbox\\REDS\\Kiosk Engine\\data"
PROFILE, INVENTORY = "profile.data", "inventory.data"
KV_TABLES_WITH_PRODUCTS = "ProductCatalog"
_CHAR_TYPES = ("Char", "NChar", "VarChar", "NVarChar", "VarBinary")


async def _noop(msg):
    pass


def _vistadb():
    try:
        import vistadb3  # noqa: F401
    except ImportError as e:  # the vendor/vistadb3-py submodule is not checked out / on the path
        raise RuntimeError("the vistadb3 package is missing: git submodule update --init vendor/vistadb3-py") from e
    from vistadb3 import Database, dumpjson, writer
    return Database, dumpjson, writer


# ------------------------------------------------------------------------------------------------ import

def _read_profile(path):
    """Everything in profile.data except image bytes (those go straight to the blob store)."""
    Database, dumpjson, _ = _vistadb()
    out = {"tables": [], "rows": [], "images": []}
    with Database(str(path)) as vdb:
        out.update(page_size=vdb.page_size // 1024, lcid=vdb.lcid, case_sensitive=vdb.case_sensitive)
        for ti, t in enumerate(vdb.tables.values()):
            out["tables"].append((t.name, ti, dumpjson.table_schema(t)))
            if t.name == "Cache":
                for ord_, (name, data, typ, size) in enumerate(vdb.iter_rows(t)):
                    out["images"].append((name, ord_, blobs.put_bytes(data) if data is not None else None, typ, size))
                continue
            if [c.name for c in t.columns][:2] != ["Key", "Value"] or len(t.columns) != 2:
                raise ValueError(f"profile.data table {t.name} is not a [Key], [Value] table")
            for ord_, (key, value) in enumerate(vdb.iter_rows(t)):
                try:
                    parsed = lua.plain(lua.decode(value)) if value is not None else None
                except lua.LuaError as e:
                    log.warning("%s[%s]: not a Lua table (%s); kept as text", t.name, key, e)
                    parsed = None
                out["rows"].append((t.name, key, ord_, value if value is not None else "", parsed))
    return out


async def _copy_rows(cur, sql, rows):
    async with cur.copy(sql) as copy:
        for r in rows:
            await copy.write_row(r)


async def import_files(profile_path, inventory_path, source, progress=_noop):
    """Replace the catalog with these two files. Returns a summary dict."""
    await progress(f"reading {profile_path}")
    prof = await asyncio.to_thread(_read_profile, profile_path)
    await progress(f"profile.data: {len(prof['tables'])} tables, {len(prof['rows'])} rows, {len(prof['images'])} images")
    with open(inventory_path, "rb") as f:
        head = f.read(inventory.HEADER_SIZE)
    inventory.parse_header(head)

    async with db.pool().connection() as conn:
        async with conn.transaction():
            await conn.execute("INSERT INTO catalog_state (id) VALUES (1) ON CONFLICT DO NOTHING")
            await conn.execute("DELETE FROM catalog_tables")
            await conn.execute("DELETE FROM catalog_images")
            async with conn.cursor() as cur:
                await _copy_rows(cur, "COPY catalog_tables (name, ord, schema) FROM STDIN",
                                 [(n, o, db.jsonb(s)) for n, o, s in prof["tables"]])
                await _copy_rows(cur, "COPY catalog_rows (tbl, key, ord, lua, value) FROM STDIN",
                                 [(t, k, o, v, db.jsonb(p) if p is not None else None) for t, k, o, v, p in prof["rows"]])
                await _copy_rows(cur, "COPY catalog_images (name, ord, blob, type, size) FROM STDIN", prof["images"])
            await conn.execute("UPDATE catalog_state SET page_size=%s, lcid=%s, case_sensitive=%s, inventory_header=%s"
                               " WHERE id=1", (prof["page_size"], prof["lcid"], prof["case_sensitive"], head))
    await progress("profile.data stored; loading inventory.data")
    count = await _import_inventory(inventory_path, progress)
    await db.execute("UPDATE catalog_state SET source=%s, imported_at=now(), changed_at=NULL, stamped_at=now()"
                     " WHERE id=1", source)
    products = await refresh_products()
    summary = {"source": source, "tables": len(prof["tables"]), "rows": len(prof["rows"]),
               "images": len(prof["images"]), "barcodes": count, "products": products}
    await progress(f"imported: {summary}")
    return summary


class _Unsorted(Exception):
    pass


def _sorted_chunk(it, n, state):
    """Up to n records with repeats folded: the last record of a barcode is kept, earlier ones go to
    state["dups"]. Raises _Unsorted when the file is not in barcode order (the engine needs that order)."""
    out = []
    prev = state.get("prev")
    for rec in it:
        b = rec[0]
        if prev is not None:
            if b < prev[0]:
                raise _Unsorted(b)
            if b == prev[0]:
                state["dups"].append(prev)
                prev = rec
                continue
            out.append(prev)
        prev = rec
        if len(out) >= n:
            break
    else:
        state["done"] = True
    state["prev"] = prev
    if state.get("done") and prev is not None and (not out or out[-1] is not prev):
        out.append(prev)
        state["prev"] = None
    return out


def _tsv(records):
    return "".join(f"{b}\t{t}\t{s}\t{r}\tcatalog\n" for b, t, s, r in records).encode()


async def _import_inventory(path, progress):
    """inventory.data -> cloud_barcodes (source 'catalog') and catalog_barcode_dups.

    The file is in barcode order, so the records are copied straight into the emptied table (B-tree appends);
    rows added here or learned from kiosks are kept aside and put back over the catalog's. The table is
    locked for the duration (a few minutes for 26 million records)."""
    _, records = inventory.read(path)
    state = {"dups": []}
    n = 0
    async with db.pool().connection() as conn:
        async with conn.transaction():
            await conn.execute("CREATE TEMP TABLE catalog_keep ON COMMIT DROP AS"
                               " SELECT * FROM cloud_barcodes WHERE source <> 'catalog'")
            await conn.execute("TRUNCATE cloud_barcodes, catalog_barcode_dups")
            async with conn.cursor() as cur:
                async with cur.copy("COPY cloud_barcodes (barcode, product_id, status, rental_count, source)"
                                    " FROM STDIN") as copy:
                    it = iter(records)
                    while not state.get("done"):
                        try:
                            chunk = await asyncio.to_thread(_sorted_chunk, it, 500_000, state)
                        except _Unsorted as e:
                            raise ValueError(f"inventory.data is not in barcode order (at {e}); the engine's binary"
                                             " search would miss barcodes. Sort it first.") from None
                        if chunk:
                            await copy.write(await asyncio.to_thread(_tsv, chunk))
                        n += len(chunk)
                        if n and n % 5_000_000 < 500_000:
                            await progress(f"inventory.data: {n:,} barcodes")
            dups = state["dups"]
            seqs = {}
            rows = []
            for b, t, s, r in dups:
                seqs[b] = seqs.get(b, 0) + 1
                rows.append((b, seqs[b], t, s, r))
            async with conn.cursor() as cur:
                await cur.executemany("INSERT INTO catalog_barcode_dups (barcode, seq, title_id, status, rental_count)"
                                      " VALUES (%s,%s,%s,%s,%s)", rows)
            await conn.execute(
                "INSERT INTO cloud_barcodes SELECT * FROM catalog_keep ON CONFLICT (barcode) DO UPDATE SET"
                " product_id = EXCLUDED.product_id, status = EXCLUDED.status, rental_count = EXCLUDED.rental_count,"
                " source = EXCLUDED.source, updated_at = EXCLUDED.updated_at")
            await conn.execute("DELETE FROM catalog_barcode_dups d USING catalog_keep k WHERE k.barcode = d.barcode")
    await progress(f"inventory.data: {n:,} barcodes, {len(dups)} repeated records")
    return n + len(dups)


async def import_folder(folder, progress=_noop):
    folder = pathlib.Path(folder)
    return await import_files(folder / PROFILE, folder / INVENTORY, f"folder {folder}", progress)


async def import_repository(repo=REPO, revision=None, progress=_noop):
    """Import from the Update Service's copy of dvd-data (default its served head)."""
    from ..updatesvc import repo as repomod
    head = revision or await repomod.served_head(repo)
    if not head:
        raise ValueError(f"{repo} has no revisions (import a seed first: admin import-store)")
    files, head, problems = await repomod.reconstruct_head(repo, upto_hash=head)
    found = {name.lower(): blob for (_, name), (_, blob) in files.items()}
    missing = [n for n in (PROFILE, INVENTORY) if n not in found]
    if missing:
        raise ValueError(f"{repo}@{head}: cannot rebuild {', '.join(missing)}: {'; '.join(problems[:3]) or 'not in it'}")
    return await import_files(blobs.path(found[PROFILE]), blobs.path(found[INVENTORY]), f"{repo}@{head}", progress)


# ---------------------------------------------------------------------------------------- cloud_products

async def refresh_products():
    """Fill cloud_products from ProductCatalog / ProductGroup / ProductType (titles the cloud APIs answer for).

    title_id = product id; product_group_id = its title rollup group (ProductGroup type 1), or the title itself
    when it has none; product_ids = {product type name: product id} of every title in that group.
    """
    types = {r["key"]: (r["value"] or {}).get("product_type_name") for r in
             await db.fetch("SELECT key, value FROM catalog_rows WHERE tbl='ProductType'")}
    titles = await db.fetch("SELECT key, value->>'long_name' AS name, value->>'product_type_id' AS type"
                            " FROM catalog_rows WHERE tbl='ProductCatalog'")
    groups = {}
    for g in await db.fetch("SELECT key, value FROM catalog_rows WHERE tbl='ProductGroup'"
                            " AND value->>'product_group_type_id' = '1'"):
        for pid in (g["value"] or {}).get("product_id_list") or []:
            groups.setdefault(str(pid), int(g["key"]))
    by_id = {t["key"]: t for t in titles if t["key"].isdigit()}
    members = {}
    for pid in by_id:
        members.setdefault(groups.get(pid, int(pid)), []).append(pid)
    rows = []
    for pid, t in by_id.items():
        gid = groups.get(pid, int(pid))
        ids = {}
        for m in sorted(members[gid], key=int):
            ids.setdefault(types.get(by_id[m]["type"]) or f"type {by_id[m]['type']}", int(m))
        rows.append((int(pid), gid, db.jsonb(ids), t["name"]))
    async with db.pool().connection() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM cloud_products WHERE from_catalog")
            async with conn.cursor() as cur:
                await cur.executemany(
                    "INSERT INTO cloud_products (title_id, product_group_id, product_ids, name, from_catalog)"
                    " VALUES (%s,%s,%s,%s,true) ON CONFLICT (title_id) DO UPDATE SET product_group_id ="
                    " EXCLUDED.product_group_id, product_ids = EXCLUDED.product_ids, name = EXCLUDED.name,"
                    " from_catalog = true", rows)
    return len(rows)


# ------------------------------------------------------------------------------------------------- build

async def _stamp_if_changed():
    """After a change, give the files a new generated-on / created-on stamp (stored, so builds repeat)."""
    st = await db.fetchrow("SELECT * FROM catalog_state WHERE id=1")
    if st is None or st["imported_at"] is None:
        raise ValueError("no catalog imported (admin catalog-import)")
    if not st["changed_at"] or (st["stamped_at"] and st["changed_at"] <= st["stamped_at"]):
        return False
    now = dt.datetime.now(dt.timezone.utc)
    stamp = now.strftime("%Y%m%d%H%M%S")
    async with db.pool().connection() as conn:
        async with conn.transaction():
            row = await (await conn.execute("SELECT lua FROM catalog_rows WHERE tbl='Version' AND key='generated_on'")).fetchone()
            if row is not None:
                value = {"date": stamp}
                await conn.execute("UPDATE catalog_rows SET lua=%s, value=%s WHERE tbl='Version' AND key='generated_on'",
                                   (lua.encode(value), db.jsonb(value)))
            old = bytes(st["inventory_header"]) if st["inventory_header"] else None
            machine = inventory.parse_header(old)["origin_machine"] if old else "UPDATESVC01"
            await conn.execute("UPDATE catalog_state SET inventory_header=%s, stamped_at=now(), changed_at=now()"
                               " WHERE id=1", (inventory.header(machine, now),))
    return True


def _write_profile(path, state, tables, rows, images):
    _, _, writer = _vistadb()
    with writer.DatabaseWriter(str(path), page_size=state["page_size"], lcid=state["lcid"],
                               case_sensitive=state["case_sensitive"], overwrite=True) as w:
        for t in tables:
            s = t["schema"]
            cols = [writer.ColumnDef(c["name"], c["type"], c["max_length"] if c["type"] in _CHAR_TYPES else 0,
                                     c["allow_null"], c.get("code_page")) for c in s["columns"]]
            ixs = [writer.IndexDef(i["name"], [x.strip() for x in i["expression"].split(";")],
                                   i["unique"] or i["primary"], i["primary"]) for i in s["indexes"]]
            w.create_table(t["name"], cols, ixs)
            if t["name"] == "Cache":
                for name, blob, typ, size in images:
                    w.insert("Cache", [name, blobs.path(blob).read_bytes() if blob else None, typ, size])
            else:
                for key, text in rows.get(t["name"], ()):
                    w.insert(t["name"], [key, text])


async def build_profile(path):
    state = await db.fetchrow("SELECT * FROM catalog_state WHERE id=1")
    tables = await db.fetch("SELECT name, schema FROM catalog_tables ORDER BY ord")
    rows = {}
    async with db.pool().connection() as conn:
        cur = conn.cursor(row_factory=tuple_row)
        await cur.execute("SELECT tbl, key, lua FROM catalog_rows ORDER BY tbl, ord")
        for tbl, key, text in await cur.fetchall():
            rows.setdefault(tbl, []).append((key, text))
    images = [(r["name"], r["blob"], r["type"], r["size"]) for r in
              await db.fetch("SELECT name, blob, type, size FROM catalog_images ORDER BY ord")]
    await asyncio.to_thread(_write_profile, path, state, tables, rows, images)
    return {"tables": len(tables), "rows": sum(len(v) for v in rows.values()), "images": len(images)}


def _pack(f, records):
    buf = bytearray()
    for barcode, title, status, rents in records:
        p, v = inventory.compress(barcode)
        buf += inventory.RECORD.pack(p, v, title, status, rents)
    f.write(buf)


async def build_inventory(path):
    """cloud_barcodes (digits-only barcodes, ordinal order) with the repeated records before each barcode."""
    head = await db.fetchval("SELECT inventory_header FROM catalog_state WHERE id=1")
    dups = {}
    for d in await db.fetch("SELECT * FROM catalog_barcode_dups ORDER BY barcode, seq"):
        dups.setdefault(d["barcode"], []).append((d["barcode"], d["title_id"], d["status"], d["rental_count"]))
    n = skipped = 0
    with open(path, "wb") as f:
        f.write(bytes(head) if head else inventory.header())
        async with db.pool().connection() as conn:
            async with conn.transaction():
                async with conn.cursor(name="catalog_inventory", row_factory=tuple_row) as cur:
                    await cur.execute("SELECT barcode, product_id, status, rental_count FROM cloud_barcodes"
                                      " ORDER BY barcode")
                    while True:
                        rows = await cur.fetchmany(200_000)
                        if not rows:
                            break
                        out = []
                        for r in rows:
                            if not r[0].isdigit() or not r[0].isascii() or not 0 <= r[1] < 1 << 32:
                                skipped += 1
                                continue
                            out.extend(dups.get(r[0], ()))
                            out.append(r)
                        await asyncio.to_thread(_pack, f, out)
                        n += len(out)
    if skipped:
        log.warning("inventory.data: %d barcodes left out (not digits, or a product id over 32 bits)", skipped)
    return {"records": n, "skipped": skipped}


async def build(out_dir, progress=_noop):
    """Write profile.data and inventory.data from the catalog into out_dir. Returns a summary with SHA-1s."""
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamped = await _stamp_if_changed()
    await progress("building profile.data" + (" (new stamp)" if stamped else ""))
    p = await build_profile(out / PROFILE)
    await progress(f"profile.data: {p['rows']} rows, {p['images']} images; building inventory.data")
    i = await build_inventory(out / INVENTORY)
    summary = {"profile": dict(p, sha1=_sha1(out / PROFILE), bytes=os.path.getsize(out / PROFILE)),
               "inventory": dict(i, sha1=_sha1(out / INVENTORY), bytes=os.path.getsize(out / INVENTORY)),
               "stamped": stamped}
    await progress(f"built: profile.data {summary['profile']['sha1']}, inventory.data {summary['inventory']['sha1']}")
    return summary


def _sha1(path):
    import hashlib
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()
