-- Kiosks known to the backend. A row is created the first time a kiosk talks to any service.
CREATE TABLE IF NOT EXISTS kiosks (
    kiosk_id       bigint PRIMARY KEY,
    market         text,
    banner         text,
    state          text,
    timezone       text DEFAULT 'Eastern Standard Time',
    ucs_version    text,
    info           jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at     timestamptz NOT NULL DEFAULT now(),
    last_seen_at   timestamptz NOT NULL DEFAULT now()
);

-- Generic append-only log: telemetry posts, status messages, transfer statistics, pings, ...
CREATE TABLE IF NOT EXISTS events (
    id          bigserial PRIMARY KEY,
    kiosk_id    bigint,
    source      text NOT NULL,
    kind        text NOT NULL,
    payload     jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_kiosk_idx ON events (kiosk_id, created_at DESC);
CREATE INDEX IF NOT EXISTS events_kind_idx ON events (source, kind, created_at DESC);
