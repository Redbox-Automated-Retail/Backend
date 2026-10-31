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
