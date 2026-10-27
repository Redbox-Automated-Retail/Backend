-- File sets: the newer distribution model used by the Update Client (over IoT/S3) and by updatemgrd's
-- `u pr` FileSets channel. One table set serves both; manifests are rendered per channel because the
-- Update Client uses Base64 SHA-1 and updatemgrd upper-case hex.

CREATE TABLE IF NOT EXISTS fs_filesets (
    id                   integer PRIMARY KEY,
    name                 text NOT NULL,
    retention_days       integer NOT NULL DEFAULT 30,
    retention_revisions  integer NOT NULL DEFAULT 3,
    created_at           timestamptz NOT NULL DEFAULT now()
);

-- Stable file ids: one per destination path within a file set.
CREATE TABLE IF NOT EXISTS fs_files (
    file_id      bigserial PRIMARY KEY,
    fileset_id   integer NOT NULL REFERENCES fs_filesets(id) ON DELETE CASCADE,
    destination  text NOT NULL,
    name         text NOT NULL,
    UNIQUE (fileset_id, destination)
);

-- File content versions (FileRevisionId).
CREATE TABLE IF NOT EXISTS fs_file_revisions (
    file_revision_id  bigserial PRIMARY KEY,
    file_id           bigint NOT NULL REFERENCES fs_files(file_id) ON DELETE CASCADE,
    content_blob      text NOT NULL,
    content_size      bigint NOT NULL,
    zip_blob          text,
    zip_size          bigint,
    UNIQUE (file_id, content_blob)
);

CREATE SEQUENCE IF NOT EXISTS fs_revision_seq START 1000;

CREATE TABLE IF NOT EXISTS fs_revisions (
    id                bigint PRIMARY KEY DEFAULT nextval('fs_revision_seq'),
    fileset_id        integer NOT NULL REFERENCES fs_filesets(id) ON DELETE CASCADE,
    name              text NOT NULL,
    version           text NOT NULL,
    set_blob          text,
    set_size          bigint,
    created_at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS fs_revision_files (
    revision_id       bigint NOT NULL REFERENCES fs_revisions(id) ON DELETE CASCADE,
    file_revision_id  bigint NOT NULL REFERENCES fs_file_revisions(file_revision_id),
    PRIMARY KEY (revision_id, file_revision_id)
);

CREATE TABLE IF NOT EXISTS fs_dependencies (
    id                  bigserial PRIMARY KEY,
    revision_id         bigint NOT NULL REFERENCES fs_revisions(id) ON DELETE CASCADE,
    depends_on_fileset  integer NOT NULL,
    dependency_type     integer NOT NULL DEFAULT 0,     -- 0 Version, 1 String, 2 Long
    minimum_version     text,
    maximum_version     text
);

-- Which revision each kiosk (or market, or every kiosk) should run.
CREATE TABLE IF NOT EXISTS fs_deployments (
    id                bigserial PRIMARY KEY,
    fileset_id        integer NOT NULL REFERENCES fs_filesets(id) ON DELETE CASCADE,
    revision_id       bigint REFERENCES fs_revisions(id) ON DELETE CASCADE,   -- NULL = remove (Action 2)
    target_type       text NOT NULL DEFAULT 'all',       -- 'all' | 'market' | 'kiosk'
    target            text,
    channel           text NOT NULL DEFAULT 'ucs',       -- 'ucs' | 'legacy'
    active_on         timestamptz NOT NULL DEFAULT now(),
    activate_start    text,                              -- "hh:mm[:ss]" window, NULL = any time
    activate_end      text,
    download_priority integer NOT NULL DEFAULT 1,
    created_at        timestamptz NOT NULL DEFAULT now()
);

-- Last state each kiosk reported per file set.
CREATE TABLE IF NOT EXISTS fs_kiosk_state (
    kiosk_id     bigint NOT NULL,
    fileset_id   integer NOT NULL,
    channel      text NOT NULL,
    revision_id  bigint,
    state        integer NOT NULL,       -- 0 Active, 1 InProgress, 2 NeedsDependency, 3 Error
    updated_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kiosk_id, fileset_id, channel)
);
