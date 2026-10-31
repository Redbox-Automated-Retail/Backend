-- The kiosk catalog (docs/catalog.md): profile.data (VistaDB 3.5, every table a list of [Key] -> Lua table literal,
-- plus the Cache table of box art) and inventory.data (barcode -> title), imported, edited here, and rebuilt
-- into a dvd-data revision. Everything needed to write both files again is kept.

-- One row: the imported files' database settings and stamps.
CREATE TABLE IF NOT EXISTS catalog_state (
    id                int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    source            text,                    -- what was imported (dvd-data@<rev>, a folder)
    imported_at       timestamptz,
    changed_at        timestamptz,             -- last edit; a build after an edit stamps the files anew
    page_size         int NOT NULL DEFAULT 1,  -- KiB
    lcid              int NOT NULL DEFAULT 1033,
    case_sensitive    boolean NOT NULL DEFAULT true,
    inventory_header  bytea,                   -- inventory.data's 312-byte header as imported
    last_build        jsonb
);

-- profile.data tables in file order, with their VistaDB definition (vistadb3 table_schema: columns, indexes).
CREATE TABLE IF NOT EXISTS catalog_tables (
    name    text PRIMARY KEY,
    ord     int NOT NULL,
    schema  jsonb NOT NULL
);

-- Key/value rows of every table but Cache. `lua` is the stored text (written back as is); `value` is it parsed
-- (catalog/lua.py), for search and display. `ord` keeps the file's row order.
CREATE TABLE IF NOT EXISTS catalog_rows (
    tbl     text NOT NULL REFERENCES catalog_tables(name) ON DELETE CASCADE,
    key     text NOT NULL,
    ord     bigint NOT NULL,
    lua     text NOT NULL,
    value   jsonb,
    PRIMARY KEY (tbl, key)
);
CREATE INDEX IF NOT EXISTS catalog_rows_ord_idx ON catalog_rows (tbl, ord);
CREATE INDEX IF NOT EXISTS catalog_rows_name_idx ON catalog_rows ((lower(value->>'long_name'))) WHERE tbl = 'ProductCatalog';

-- The Cache table: box art the engine extracts to data\ProfileImages\<name> (only names it does not have yet,
-- so new content needs a new name: catalog/edit.py names images by their SHA-1).
CREATE TABLE IF NOT EXISTS catalog_images (
    name    text PRIMARY KEY,
    ord     bigint NOT NULL,
    blob    text,                   -- blob store key (SHA-1 of the bytes); NULL = no data (real files have such rows)
    type    smallint,
    size    int
);

-- inventory.data is cloud_barcodes (one row per barcode). The ordinal ("C") collation is the order the engine's
-- binary search needs. source: catalog (imported), added (edited here), learned (from kiosk transactions).
ALTER TABLE cloud_barcodes ADD COLUMN IF NOT EXISTS status smallint NOT NULL DEFAULT 0;   -- InventoryStatusCode
ALTER TABLE cloud_barcodes ADD COLUMN IF NOT EXISTS rental_count bigint NOT NULL DEFAULT 0;
ALTER TABLE cloud_barcodes ADD COLUMN IF NOT EXISTS source text NOT NULL DEFAULT 'learned';
DO $$ BEGIN
    IF (SELECT collation_name FROM information_schema.columns WHERE table_schema = 'public'
        AND table_name = 'cloud_barcodes' AND column_name = 'barcode') IS DISTINCT FROM 'C' THEN
        ALTER TABLE cloud_barcodes ALTER COLUMN barcode TYPE text COLLATE "C";
    END IF;
END $$;

-- Barcodes of a title (dashboard, deleting a title). catalog-import drops it while it loads and builds it again.
CREATE INDEX IF NOT EXISTS cloud_barcodes_product_idx ON cloud_barcodes (product_id);
-- The few barcodes added here or learned from kiosks (dashboard list, counts), without scanning the catalog's.
CREATE INDEX IF NOT EXISTS cloud_barcodes_local_idx ON cloud_barcodes (updated_at) WHERE source <> 'catalog';

-- Real inventory.data files repeat a few barcodes; the earlier records are kept here so an unedited catalog is
-- written back byte for byte (they come before the cloud_barcodes record of the same barcode).
CREATE TABLE IF NOT EXISTS catalog_barcode_dups (
    barcode       text COLLATE "C" NOT NULL,
    seq           int NOT NULL,
    title_id      bigint NOT NULL,
    status        smallint NOT NULL,
    rental_count  bigint NOT NULL,
    PRIMARY KEY (barcode, seq)
);

-- cloud_products rows filled from the catalog (catalog.refresh_products); others are left alone.
ALTER TABLE cloud_products ADD COLUMN IF NOT EXISTS from_catalog boolean NOT NULL DEFAULT false;

-- Any change to the catalog marks it changed. The next build then stamps the files anew (Version generated_on,
-- inventory.data's created-on) and records stamped_at, so builds without changes in between give the same files.
ALTER TABLE catalog_state ADD COLUMN IF NOT EXISTS stamped_at timestamptz;
CREATE OR REPLACE FUNCTION catalog_mark_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE catalog_state SET changed_at = now() WHERE id = 1;
    RETURN NULL;
END $$;
CREATE OR REPLACE TRIGGER catalog_rows_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON catalog_rows
    FOR EACH STATEMENT EXECUTE FUNCTION catalog_mark_change();
CREATE OR REPLACE TRIGGER catalog_images_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON catalog_images
    FOR EACH STATEMENT EXECUTE FUNCTION catalog_mark_change();
CREATE OR REPLACE TRIGGER cloud_barcodes_catalog_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON cloud_barcodes
    FOR EACH STATEMENT EXECUTE FUNCTION catalog_mark_change();
