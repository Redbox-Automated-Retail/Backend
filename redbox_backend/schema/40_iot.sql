-- IoT (AWS IoT Core stand-in): device certificates, download-file jobs, cloud->kiosk requests.

CREATE TABLE IF NOT EXISTS iot_certs (
    certificate_id  text PRIMARY KEY,
    kiosk_id        bigint NOT NULL,
    market          text,
    pfx_b64         text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    revoked_at      timestamptz
);
CREATE INDEX IF NOT EXISTS iot_certs_kiosk_idx ON iot_certs (kiosk_id) WHERE revoked_at IS NULL;

CREATE TABLE IF NOT EXISTS iot_registrations (
    kiosk_id          bigint PRIMARY KEY,
    ucs_version       text,
    assembly_version  text,
    broker_registered boolean NOT NULL DEFAULT false,
    broker_reason     text,
    registered_at     timestamptz,
    last_ping_at      timestamptz,
    last_ping         jsonb,
    updated_at        timestamptz NOT NULL DEFAULT now()
);

-- Requests the cloud sent to kiosks and the responses they published.
CREATE TABLE IF NOT EXISTS iot_requests (
    request_id   text PRIMARY KEY,
    kiosk_id     bigint,
    topic        text NOT NULL,
    command      text NOT NULL,
    version      integer NOT NULL,
    payload      jsonb,
    response     jsonb,
    created_at   timestamptz NOT NULL DEFAULT now(),
    responded_at timestamptz
);

-- Download-file jobs (ExecuteDownloadFileJob) and the status updates kiosks report.
CREATE TABLE IF NOT EXISTS iot_download_jobs (
    job_id        text PRIMARY KEY,
    file_key      text,
    script_key    text,
    metadata      jsonb NOT NULL,
    target        text NOT NULL DEFAULT 'all',
    start_date    timestamptz,
    start_time    text,
    end_time      text,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS iot_download_job_status (
    job_id      text NOT NULL,
    kiosk_id    bigint NOT NULL,
    status      text NOT NULL,
    payload     jsonb,
    updated_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (job_id, kiosk_id)
);
