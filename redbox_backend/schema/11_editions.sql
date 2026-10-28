-- Update-store "editions": one per imported store (a kiosk's .store directory or a data-<date>.zip installer
-- seed). Redbox's seeds hold the same linear history per repository, but newer ones trim the contents of old
-- revisions (their change lists are emptied and the files re-seeded later), so each edition keeps its own
-- revlog view. The served (canonical) history in upd_revisions/upd_changes is rebuilt from the newest edition
-- that has the repository, with content filled in from older editions where the newest lacks it.

CREATE TABLE IF NOT EXISTS upd_editions (
    name         text PRIMARY KEY,           -- e.g. data-05072024
    source       text,                       -- file or directory it came from
    edition_date date,
    heads        jsonb NOT NULL DEFAULT '{}'::jsonb,   -- {repo: head hash}
    revisions    jsonb NOT NULL DEFAULT '{}'::jsonb,   -- {repo: revision count}
    imported_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS upd_edition_revisions (
    edition   text NOT NULL REFERENCES upd_editions(name) ON DELETE CASCADE,
    repo      text NOT NULL,
    seq       integer NOT NULL,
    hash      text NOT NULL,
    label     text,
    PRIMARY KEY (edition, repo, seq)
);
CREATE INDEX IF NOT EXISTS upd_edition_revisions_hash_idx ON upd_edition_revisions (repo, hash);

CREATE TABLE IF NOT EXISTS upd_edition_changes (
    edition       text NOT NULL REFERENCES upd_editions(name) ON DELETE CASCADE,
    repo          text NOT NULL,
    rev_hash      text NOT NULL,
    idx           integer NOT NULL,
    target_name   text NOT NULL,
    target_path   text NOT NULL,
    version_hash  text NOT NULL,
    content_hash  text,
    is_seed       boolean NOT NULL,
    composite     boolean NOT NULL,
    blob          text,
    PRIMARY KEY (edition, repo, rev_hash, idx)
);
CREATE INDEX IF NOT EXISTS upd_edition_changes_content_idx ON upd_edition_changes (repo, rev_hash, target_path, target_name)
    WHERE blob IS NOT NULL;

-- Where served revisions came from, and an optional pin of the head offered to kiosks.
ALTER TABLE upd_revisions ADD COLUMN IF NOT EXISTS origin text NOT NULL DEFAULT 'import';   -- import | published
ALTER TABLE upd_repositories ADD COLUMN IF NOT EXISTS canonical_edition text;
ALTER TABLE upd_repositories ADD COLUMN IF NOT EXISTS serve_head text;   -- NULL = newest revision

-- Background import jobs started from the dashboard.
CREATE TABLE IF NOT EXISTS upd_import_jobs (
    id          bigserial PRIMARY KEY,
    source      text NOT NULL,
    options     jsonb NOT NULL DEFAULT '{}'::jsonb,
    status      text NOT NULL DEFAULT 'running',     -- running | done | failed
    progress    jsonb NOT NULL DEFAULT '[]'::jsonb,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
