"""The kiosk catalog (redbox_backend/catalog): Lua values, inventory.data, import -> edit -> build.

The database test imports a small synthetic catalog in the real files' layout, which replaces the catalog: it is
skipped when a catalog that is not a test import is present. With RB_CATALOG_DIR (a folder with a real kiosk's
profile.data and inventory.data, read-only) every real value is also checked to round-trip.
"""
import asyncio
import hashlib
import os
import pathlib
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "kcs"))
import cloudclient as cc  # noqa: E402

from redbox_backend.catalog import inventory, lua  # noqa: E402

REAL = os.environ.get("RB_CATALOG_DIR")
JPEG = b"\xff\xd8\xff\xe0" + b"catalog test image" + b"\xff\xd9"


# ------------------------------------------------------------------------------------------- Lua values

@pytest.mark.parametrize("text", [
    '{ product_id = 1000 , stars = 1 , running_time = "1:35" , starring = { "Animated" } , rating_id = 7 }',
    '{ store_id = 1 , rental_tax_rate = 4.125 , purchase_price = 5 , banner_id = nil , sell_thru = true }',
    '{ sellthru_price = 0.00 , box_office_gross = 1.5 }',
    '{ directors = {  } , genres = { [1000] = true , [1004] = true } }',
    '{ product_family_id = 3 , genres = {} }',
    '{ product_group_id = 1000 , product_id_list = { 1878 , 1879 } }',
    '{ description = "Line one\\r\\n\\r\\nRated \\"PG\\"\\r\\n\\tTabbed\\\\ text" }',
    '{ name = "UPDATESVC01" }',
])
def test_lua_round_trip(text):
    assert lua.encode(lua.decode(text)) == text


def test_lua_values_and_engine_escapes():
    v = lua.decode('{ a = 1 , b = 2.50 , c = nil , d = { "x" , "y" } , e = { [7] = true } , f = "q\\"\\r\\n\\t" , g = {} , h = {  } }')
    assert v["a"] == 1 and str(v["b"]) == "2.50" and v["c"] is None and v["d"] == ["x", "y"]
    assert v["e"] == {"7": True} and v["g"] == {} and v["h"] == []
    assert v["f"] == 'q"\r\n\\t'   # only \" and \r\n are escapes, as in the engine's ParseLuaValue
    assert lua.plain(v)["b"] == 2.5
    assert lua.encode({"k": "a\nb"}) == '{ k = "a\\r\\nb" }'


def test_lua_rejects_what_the_engine_cannot_read():
    for bad in ("ends with \\", 'x\\"y'):
        with pytest.raises(lua.LuaError):
            lua.encode({"s": bad})
    with pytest.raises(lua.LuaError):
        lua.decode("{ a = 1 , b }x")


@pytest.mark.skipif(not REAL, reason="RB_CATALOG_DIR not set")
def test_real_profile_values_round_trip():
    from vistadb3 import Database
    bad, n = [], 0
    with Database(str(pathlib.Path(REAL) / "profile.data")) as d:
        for name, t in d.tables.items():
            if name == "Cache":
                continue
            for key, value in d.iter_rows(t):
                n += 1
                try:
                    lua.decode(value)
                except lua.LuaError:
                    bad.append((name, key))
    assert n > 1000 and not bad


def test_omdb_answer_to_title_fields():
    from redbox_backend.catalog.edit import omdb_fields
    answer = {"Title": "The Example", "Year": "2019", "Rated": "PG-13", "Released": "05 Jul 2019", "Runtime": "129 min",
              "Genre": "Action, Sci-Fi, Thriller", "Director": "A. Director", "Actors": "One, Two, Three",
              "Plot": "Things happen.", "BoxOffice": "$390,532,085", "imdbRating": "7.4", "Production": "N/A"}
    f = omdb_fields(answer, ratings={"PG13": 7, "NR": 10}, genres={"ACTION": 1103, "SCIFI & FANTASY": 1013,
                                                                    "SUSPENSE": 1016})
    assert f["long_name"] == "The Example" and f["sort_name"] == "Example"
    assert f["rating_id"] == 7 and f["running_time"] == "2:09" and f["release_date"] == "20190705000000"
    assert f["genres"] == [1013, 1016, 1103] and f["starring"] == ["One", "Two", "Three"]
    assert f["box_office_gross"] == 391 and f["stars"] == 4 and "studio" not in f
    assert f["description"].startswith("Things happen.\r\n\r\nRated PG-13")


# ---------------------------------------------------------------------------------------- inventory.data

def test_barcode_compression_matches_the_engine():
    assert inventory.compress("056548257") == (1, 56548257)
    assert inventory.compress("0") == (0, 0)
    assert inventory.compress("000") == (2, 0)    # Archive.CompressBarcode keeps one zero as the number
    assert inventory.decompress(*inventory.compress("000")) == "000"
    for bad in ("12a", "", "-1"):
        with pytest.raises(inventory.InventoryError):
            inventory.compress(bad)


def test_inventory_write_read(tmp_path):
    recs = [("0001", 5, 0, 3), ("0002", 5, 3, 0), ("123", 7, 0, 10)]
    p = tmp_path / "inventory.data"
    assert inventory.write(p, recs, origin_machine="TESTBOX", created_on=None) == 3
    data = p.read_bytes()
    assert len(data) == inventory.HEADER_SIZE + 3 * inventory.RECORD.size
    info, it = inventory.read(p)
    assert info["origin_machine"] == "TESTBOX" and list(it) == recs


def test_sorted_chunks_fold_repeats_and_reject_disorder():
    from redbox_backend.catalog.store import _Unsorted, _sorted_chunk
    recs = [("1", 1, 0, 0), ("2", 1, 0, 0), ("2", 2, 0, 0), ("3", 3, 0, 0)]
    state = {"dups": []}
    out = []
    it = iter(recs)
    while not state.get("done"):
        out += _sorted_chunk(it, 1, state)
    assert out == [("1", 1, 0, 0), ("2", 2, 0, 0), ("3", 3, 0, 0)] and state["dups"] == [("2", 1, 0, 0)]
    with pytest.raises(_Unsorted):
        _sorted_chunk(iter([("2", 1, 0, 0), ("1", 1, 0, 0)]), 10, {"dups": []})


# ------------------------------------------------------------------------------- import -> edit -> build

def run(coro):
    from redbox_backend import db

    async def go():
        await db.init(max_size=2)
        try:
            return await coro
        finally:
            await db.close()
    saved, db._pool = db._pool, None
    try:
        return asyncio.run(go())
    finally:
        db._pool = saved


KV = [("Key", "VarChar", 250, False), ("Value", "NText", 0, True)]
TABLES = {
    "Cache": None,
    "Market": [("1", '{ market_id = 1 , market_name = "Denver, CO" }')],
    "ProductCatalog": [
        ("1000", '{ product_id = 1000 , stars = 1 , running_time = "1:35" , starring = { "A" } , product_type_id = 1 , '
                 'rating_id = 7 , long_name = "Test Title" , sort_name = "Test Title" , image_file = "IMG1.jpg" , '
                 'description = "x\\r\\ny" , genres = { [1000] = true } , release_date = "20070815000100" , '
                 'sell_thru = true , directors = {  } , sellthru_price = 0.00 }'),
        ("1001", '{ product_id = 1001 , stars = 1 , running_time = "1:35" , starring = { "A" } , product_type_id = 2 , '
                 'rating_id = 7 , long_name = "Test Title (Blu-ray)" , sort_name = "Test Title (Blu-ray)" , '
                 'image_file = "IMG1.jpg" , description = "" , genres = {} , release_date = "20070815000100" , '
                 'sell_thru = false , directors = {  } , sellthru_price = 0.00 }')],
    "ProductGroup": [("2000", '{ product_group_id = 2000 , product_group_name = "Test Title" , product_group_type_id = 1 , '
                              'product_id_list = { 1000 , 1001 } }')],
    "ProductType": [("1", '{ product_type_id = 1 , product_type_name = "DVD" , product_family_id = 1 }'),
                    ("2", '{ product_type_id = 2 , product_type_name = "Blu-ray" , product_family_id = 1 }')],
    "TitleRollupXREF": [("1000", '{ product_id = 1000 , product_group_id = 2000 }'),
                        ("1001", '{ product_id = 1001 , product_group_id = 2000 }')],
    "Version": [("origin_machine", '{ name = "UPDATESVC01" }'), ("generated_on", '{ date = "20240723061824" }')],
}


def make_catalog(folder):
    from vistadb3.writer import ColumnDef, DatabaseWriter
    with DatabaseWriter(str(folder / "profile.data"), page_size=1, lcid=1033, case_sensitive=True) as w:
        for name, rows in TABLES.items():
            if name == "Cache":
                w.create_table("Cache", [ColumnDef("Name", "VarChar", 250, False), ColumnDef("Data", "Image"),
                                         ColumnDef("Type", "TinyInt"), ColumnDef("Size", "Int")],
                               primary_key="Name", primary_key_name="IX_Key")
                w.insert("Cache", ["IMG1.jpg", JPEG, 0, len(JPEG)])
                w.insert("Cache", ["EMPTY.jpg", None, 0, 0])
                continue
            cols = [ColumnDef("Key", "VarChar", 64, False), ColumnDef("Value", "VarChar", 64)] if name == "Version" \
                else [ColumnDef(n, t, m, a) for n, t, m, a in KV]
            w.create_table(name, cols, primary_key="Key", primary_key_name="IX_Key")
            for k, v in rows:
                w.insert(name, [k, v])
    recs = [("0000000001", 1000, 0, 1), ("0000000002", 1001, 0, 2), ("0000000002", 1000, 0, 3), ("500", 1000, 3, 0)]
    inventory.write(folder / "inventory.data", recs, origin_machine="UPDATESVC01")


def dump(path):
    from vistadb3 import Database
    with Database(str(path)) as d:
        return {n: list(d.iter_rows(t)) for n, t in d.tables.items()}


def sha1(p):
    return hashlib.sha1(pathlib.Path(p).read_bytes()).hexdigest()


@pytest.mark.skipif(not cc.database_available(), reason="DATABASE_URL not reachable")
def test_import_edit_build(tmp_path):
    from redbox_backend import db
    from redbox_backend.catalog import edit, store

    async def scenario():
        src = await db.fetchval("SELECT source FROM catalog_state WHERE id=1")
        if src and not src.startswith("test:"):
            pytest.skip(f"a real catalog is imported ({src}); not replacing it")
        (tmp_path / "in").mkdir()
        make_catalog(tmp_path / "in")
        res = await store.import_files(tmp_path / "in" / "profile.data", tmp_path / "in" / "inventory.data",
                                       "test:synthetic")
        assert res["rows"] == sum(len(r) for r in TABLES.values() if r) and res["images"] == 2
        assert res["barcodes"] == 4 and res["products"] == 2
        p = await db.fetchrow("SELECT * FROM cloud_products WHERE title_id = 1001")
        assert p["product_group_id"] == 2000 and p["product_ids"] == {"DVD": 1000, "Blu-ray": 1001}

        # unedited: the same tables and rows, and inventory.data byte for byte
        out1 = tmp_path / "out1"
        built = await store.build(out1)
        assert not built["stamped"]
        assert dump(out1 / "profile.data") == dump(tmp_path / "in" / "profile.data")
        assert (out1 / "inventory.data").read_bytes() == (tmp_path / "in" / "inventory.data").read_bytes()

        # edit: a title with box art, a barcode and a rollup group
        pid = await edit.save_title({"long_name": "Added Title", "product_type_id": 1, "rating_id": 7,
                                     "starring": "One, Two", "genres": [1000]})
        assert pid == 1002
        name = await edit.put_image(JPEG + b"2", pid)
        await edit.set_barcode("0000000003", pid)
        await edit.save_group(2000, None, 1, [1000, 1001, pid])
        with pytest.raises(edit.EditError):
            await edit.save_title({"release_date": "2024-01-01"}, pid)

        out2 = tmp_path / "out2"
        built = await store.build(out2)
        assert built["stamped"]
        rows = dump(out2 / "profile.data")
        title = dict(rows["ProductCatalog"])[str(pid)]
        assert 'long_name = "Added Title"' in title and 'starring = { "One" , "Two" }' in title
        assert f'image_file = "{name}"' in title and "release_date = \"" in title
        assert dict(rows["TitleRollupXREF"])[str(pid)] == f"{{ product_id = {pid} , product_group_id = 2000 }}"
        assert {r[0]: r[1] for r in rows["Cache"]}[name] == JPEG + b"2"
        assert dict(rows["Version"])["generated_on"] != dict(TABLES["Version"])["generated_on"]
        _, recs = inventory.read(out2 / "inventory.data")
        assert ("0000000003", pid, 0, 0) in list(recs)
        prod = await db.fetchrow("SELECT * FROM cloud_products WHERE title_id = %s", pid)
        assert prod["product_group_id"] == 2000 and prod["name"] == "Added Title"

        # no change since: the same bytes again
        out3 = tmp_path / "out3"
        await store.build(out3)
        assert sha1(out3 / "profile.data") == sha1(out2 / "profile.data")
        assert sha1(out3 / "inventory.data") == sha1(out2 / "inventory.data")

        # delete: groups, XREF, image and barcodes go with it
        await edit.delete_title(pid, barcodes=True)
        assert await edit.get_row("TitleRollupXREF", pid) is None
        assert not await db.fetchval("SELECT 1 FROM catalog_images WHERE name=%s", name)
        assert not await db.fetchval("SELECT 1 FROM cloud_barcodes WHERE barcode='0000000003'")

        await db.execute("DELETE FROM catalog_tables")
        await db.execute("DELETE FROM catalog_images")
        await db.execute("DELETE FROM catalog_barcode_dups")
        await db.execute("DELETE FROM cloud_barcodes WHERE source = 'catalog'")
        await db.execute("DELETE FROM cloud_products WHERE from_catalog")
        await db.execute("DELETE FROM catalog_state")
    run(scenario())
