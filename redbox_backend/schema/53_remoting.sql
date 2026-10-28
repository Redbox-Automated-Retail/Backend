-- Legacy .NET Remoting backends (rb-remoting): reservation broker registrations and the reservations the
-- broker sent to kiosks (the reservation itself lives in cloud_reservations, shared with the Transaction API).

CREATE TABLE IF NOT EXISTS broker_kiosks (
    kiosk_id         bigint PRIMARY KEY,
    version          jsonb,          -- the JSON version string the kiosk registers with (pricing policy, features)
    host             text,
    connected        boolean NOT NULL DEFAULT false,
    registrations    integer NOT NULL DEFAULT 1,
    registered_at    timestamptz NOT NULL DEFAULT now(),
    disconnected_at  timestamptz
);

CREATE TABLE IF NOT EXISTS broker_requests (
    id                bigserial PRIMARY KEY,
    kiosk_id          bigint NOT NULL,
    kind              text NOT NULL,         -- reserve | cancel | ping
    reference_number  bigint,
    request           jsonb,
    result            jsonb,
    success           boolean,
    error             text,
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS broker_requests_kiosk_idx ON broker_requests (kiosk_id, created_at DESC);
