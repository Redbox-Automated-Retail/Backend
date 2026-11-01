-- Admin dashboard accounts (dashboard/auth.py). The ADMIN_USERNAME / ADMIN_PASSWORD_HASH login from .env is always
-- an admin and is not stored here; these are the extra accounts made on the Users & access page.
--   admin     everything, including accounts and API tokens
--   operator  every change except accounts and API tokens
--   viewer    read only
CREATE TABLE IF NOT EXISTS dash_users (
    username             text PRIMARY KEY,
    display_name         text,
    password_hash        text NOT NULL,          -- pbkdf2_sha256$<iterations>$<salt b64>$<hash b64>
    role                 text NOT NULL DEFAULT 'viewer' CHECK (role IN ('admin', 'operator', 'viewer')),
    enabled              boolean NOT NULL DEFAULT true,
    created_at           timestamptz NOT NULL DEFAULT now(),
    created_by           text,
    password_changed_at  timestamptz NOT NULL DEFAULT now(),
    last_login_at        timestamptz
);

CREATE INDEX IF NOT EXISTS events_created_idx ON events (created_at DESC);

-- When a catalog row was last edited here (catalog/edit.py put_row); NULL for imported rows. The dashboard's catalog
-- shows the titles changed since the last build (catalog_state.stamped_at).
ALTER TABLE catalog_rows ADD COLUMN IF NOT EXISTS edited_at timestamptz;

-- Store management: stores by banner, market and state (the dashboard's filters and per-banner / per-market counts).
CREATE INDEX IF NOT EXISTS catalog_store_banner_idx ON catalog_rows ((value->>'banner_id')) WHERE tbl = 'Store';
CREATE INDEX IF NOT EXISTS catalog_store_market_idx ON catalog_rows ((value->>'market_id')) WHERE tbl = 'Store';
CREATE INDEX IF NOT EXISTS catalog_store_state_idx ON catalog_rows ((value->>'state')) WHERE tbl = 'Store';

-- Columns the dashboard sorts and filters on, kept by Postgres from each row's key and value so a list of 76,000
-- stores or 11,000 titles never has to unpack the (large, TOASTed) values. Imports and edits need no change.
ALTER TABLE catalog_rows ADD COLUMN IF NOT EXISTS key_num bigint
    GENERATED ALWAYS AS (CASE WHEN key ~ '^[0-9]{1,18}$' THEN key::bigint END) STORED;
ALTER TABLE catalog_rows ADD COLUMN IF NOT EXISTS t_name text GENERATED ALWAYS AS (value->>'long_name') STORED;
ALTER TABLE catalog_rows ADD COLUMN IF NOT EXISTS t_type int
    GENERATED ALWAYS AS (CASE WHEN value->>'product_type_id' ~ '^[0-9]{1,9}$' THEN (value->>'product_type_id')::int END) STORED;
ALTER TABLE catalog_rows ADD COLUMN IF NOT EXISTS t_release text GENERATED ALWAYS AS (value->>'release_date') STORED;
ALTER TABLE catalog_rows ADD COLUMN IF NOT EXISTS t_image text GENERATED ALWAYS AS (value->>'image_file') STORED;
CREATE INDEX IF NOT EXISTS catalog_rows_num_idx ON catalog_rows (tbl, key_num);
CREATE INDEX IF NOT EXISTS catalog_titles_type_idx ON catalog_rows (t_type) WHERE tbl = 'ProductCatalog';
CREATE INDEX IF NOT EXISTS catalog_titles_release_idx ON catalog_rows (t_release) WHERE tbl = 'ProductCatalog';

-- Substring search ("ILIKE %erie%") over 76,000 store addresses and 11,000 titles without reading every row.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS catalog_store_address_trgm ON catalog_rows USING gin ((value->>'address') gin_trgm_ops) WHERE tbl = 'Store';
CREATE INDEX IF NOT EXISTS catalog_titles_name_trgm ON catalog_rows USING gin (t_name gin_trgm_ops) WHERE tbl = 'ProductCatalog';
