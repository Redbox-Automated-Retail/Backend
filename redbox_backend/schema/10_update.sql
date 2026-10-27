-- Legacy Update Service (updatemgrd, srcp:7002). Content lives in the blob store (DATA_DIR/blobs/<sha1>).

CREATE TABLE IF NOT EXISTS upd_repositories (
    name            text PRIMARY KEY,
    auto_subscribe  boolean NOT NULL DEFAULT true,   -- offered to every store without explicit links
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Revision history, oldest first (seq 0 ..).
CREATE TABLE IF NOT EXISTS upd_revisions (
    repo        text NOT NULL REFERENCES upd_repositories(name) ON DELETE CASCADE,
    seq         integer NOT NULL,
    hash        text NOT NULL,
    label       text,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (repo, seq),
    UNIQUE (repo, hash)
);

-- One row per changed file per revision (the revlog "Changes" entries).
CREATE TABLE IF NOT EXISTS upd_changes (
    repo          text NOT NULL,
    rev_hash      text NOT NULL,
    idx           integer NOT NULL,
    target_name   text NOT NULL,
    target_path   text NOT NULL,
    version_hash  text NOT NULL,
    content_hash  text,
    is_seed       boolean NOT NULL,
    composite     boolean NOT NULL,
    blob          text,              -- sha1 of the .patch bytes; NULL for placeholders
    PRIMARY KEY (repo, rev_hash, idx),
    FOREIGN KEY (repo, rev_hash) REFERENCES upd_revisions(repo, hash) ON DELETE CASCADE
);

-- Full-file snapshot of a repository at a revision, used to seed kiosks that have no usable head.
CREATE TABLE IF NOT EXISTS upd_snapshots (
    repo          text NOT NULL,
    head_hash     text NOT NULL,
    target_name   text NOT NULL,
    target_path   text NOT NULL,
    version_hash  text NOT NULL,
    blob          text NOT NULL,
    PRIMARY KEY (repo, head_hash, target_path, target_name)
);

-- Stores (kiosks as the Update Service calls them) and their BITS policy.
CREATE TABLE IF NOT EXISTS upd_stores (
    number                 text PRIMARY KEY,
    id                     bigserial,
    group_name             text NOT NULL DEFAULT 'Default',
    enabled                boolean NOT NULL DEFAULT true,
    -- Written by the kiosk into the BITS group policy (Kbps; EnableBITSMaxBandwidth is always set),
    -- so keep these high unless you want throttling.
    max_bw_outside         bigint NOT NULL DEFAULT 1000000,
    max_bw_within          bigint NOT NULL DEFAULT 1000000,
    schedule_start         smallint NOT NULL DEFAULT 8,
    schedule_end           smallint NOT NULL DEFAULT 17,
    allowed_mb             integer NOT NULL DEFAULT 0,
    no_progress_timeout    bigint NOT NULL DEFAULT 1209600,
    time_zone              text,          -- Windows time zone id to push through StoreInfo (NULL = leave)
    correct_time           boolean NOT NULL DEFAULT true,     -- send the server clock when the kiosk is >5 min off
    last_check_in          timestamptz,
    created_at             timestamptz NOT NULL DEFAULT now()
);

-- Explicit repository links. A store with no rows gets every auto_subscribe repository.
CREATE TABLE IF NOT EXISTS upd_store_repos (
    number  text NOT NULL REFERENCES upd_stores(number) ON DELETE CASCADE,
    repo    text NOT NULL REFERENCES upd_repositories(name) ON DELETE CASCADE,
    linked  boolean NOT NULL DEFAULT true,     -- false = explicitly unlinked
    PRIMARY KEY (number, repo)
);

-- What each store last reported (`u p` c: map).
CREATE TABLE IF NOT EXISTS upd_store_heads (
    number       text NOT NULL,
    repo         text NOT NULL,
    head         text NOT NULL,
    reported_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (number, repo)
);

-- Config files (ConfigFileChangeSet). One generation = one full JSON document.
CREATE TABLE IF NOT EXISTS upd_config_files (
    oid          integer PRIMARY KEY,
    name         text NOT NULL,
    path         text NOT NULL,
    generation   integer NOT NULL,
    data         jsonb NOT NULL,          -- ConfigurationData list
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- Last config generations a store reported.
CREATE TABLE IF NOT EXISTS upd_store_config (
    number      text NOT NULL,
    oid         integer NOT NULL,
    staged_gen  integer,
    active_gen  integer,
    reported_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (number, oid)
);

CREATE SEQUENCE IF NOT EXISTS upd_sync_seq;

-- Store files (StoreFileChangeSet): single data files pushed to kiosks.
CREATE TABLE IF NOT EXISTS upd_store_files (
    id           integer PRIMARY KEY,
    name         text NOT NULL,
    path         text NOT NULL,
    data_id      integer NOT NULL,
    blob         text,
    deleted      boolean NOT NULL DEFAULT false,
    modified_on  timestamptz NOT NULL DEFAULT now()
);

-- Download files (DownloadFile poll replies) queued per store.
CREATE TABLE IF NOT EXISTS upd_download_files (
    id                bigserial PRIMARY KEY,
    number            text,              -- NULL = every store
    name              text NOT NULL,
    type              integer NOT NULL DEFAULT 0,   -- 0 Bits, 1 WUA, 2 Script
    file_name         text,
    destination_path  text,
    blob              text,
    activate_script   text,              -- Lua source (plain text; encoded when sent)
    start_time        text,
    end_time          text,
    created_at        timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS upd_download_file_sent (
    download_id  bigint NOT NULL REFERENCES upd_download_files(id) ON DELETE CASCADE,
    number       text NOT NULL,
    sent_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (download_id, number)
);

-- installer start / installer log sessions.
CREATE TABLE IF NOT EXISTS upd_installer_sessions (
    guid        text PRIMARY KEY,
    hash        text,
    version     text,
    log         jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at  timestamptz NOT NULL DEFAULT now()
);
