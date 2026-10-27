-- Segmented kiosk configuration served to the Update Client (GetConfigChanges) and written by it to
-- C:\ProgramData\Redbox\Configuration\configuration.json, which the Kiosk Client and Update Client read.

CREATE TABLE IF NOT EXISTS cfg_segments (
    id                 bigserial PRIMARY KEY,
    segmentation_name  text NOT NULL,          -- 'All', 'Market', 'Kiosk'
    key                text NOT NULL,          -- '*', market name, kiosk id
    name               text NOT NULL,
    rank               bigint NOT NULL,
    UNIQUE (segmentation_name, key)
);

CREATE TABLE IF NOT EXISTS cfg_settings (
    setting_id  bigserial PRIMARY KEY,
    category    text NOT NULL,
    name        text NOT NULL,
    data_type   text NOT NULL DEFAULT 'string',
    UNIQUE (category, name)
);

CREATE TABLE IF NOT EXISTS cfg_values (
    value_id          bigserial PRIMARY KEY,
    setting_id        bigint NOT NULL REFERENCES cfg_settings(setting_id) ON DELETE CASCADE,
    segment_id        bigint NOT NULL REFERENCES cfg_segments(id) ON DELETE CASCADE,
    value             text,
    encryption_type   integer,                 -- NULL = plain, 0 = EncryptType1
    effective_at      timestamp NOT NULL DEFAULT '2020-01-01 00:00:00',
    expires_at        timestamp,
    UNIQUE (setting_id, segment_id)
);

-- Global configuration version; bumped on every change.
CREATE TABLE IF NOT EXISTS cfg_version (
    id       integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    version  bigint NOT NULL
);
INSERT INTO cfg_version (id, version) VALUES (1, 1) ON CONFLICT DO NOTHING;

-- What each kiosk reported / activated.
CREATE TABLE IF NOT EXISTS cfg_kiosk_state (
    kiosk_id        bigint PRIMARY KEY,
    reported_version bigint,
    activated_version bigint,
    activated_at    timestamptz,
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Per-kiosk override of GetConfigStatus.EnableUpdates (default true).
CREATE TABLE IF NOT EXISTS cfg_kiosk_status (
    kiosk_id        bigint PRIMARY KEY,
    enable_updates  boolean NOT NULL DEFAULT true
);

INSERT INTO cfg_segments (segmentation_name, key, name, rank) VALUES ('All', '*', 'All kiosks', 0)
    ON CONFLICT DO NOTHING;
