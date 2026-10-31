-- Our own builds (docs/releases.md): release metadata on revisions, staged rollouts and CI tokens.

-- A revision kiosks get by default. Imported and hand-published revisions are released at once; a release
-- published by CI is not, until it is rolled out to every store (`admin rollout --all`).
ALTER TABLE upd_revisions ADD COLUMN IF NOT EXISTS released boolean NOT NULL DEFAULT true;
ALTER TABLE upd_revisions ADD COLUMN IF NOT EXISTS version text;
ALTER TABLE upd_revisions ADD COLUMN IF NOT EXISTS build jsonb;       -- release.json of the build it came from

-- The head a store or a market is served instead of the repository's default (store beats market beats
-- the pin / newest released revision). Rolling out to every store moves the pin instead.
CREATE TABLE IF NOT EXISTS upd_rollouts (
    repo         text NOT NULL REFERENCES upd_repositories(name) ON DELETE CASCADE,
    target_type  text NOT NULL,          -- 'store' | 'market'
    target       text NOT NULL,          -- store number | market name
    head         text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (repo, target_type, target)
);

-- Every change of what a target is served, so a rollback knows what came before.
CREATE TABLE IF NOT EXISTS upd_rollout_log (
    id           bigserial PRIMARY KEY,
    repo         text NOT NULL,
    target_type  text NOT NULL,          -- 'store' | 'market' | 'all'
    target       text,
    action       text NOT NULL,          -- rollout | rollback | revert | clear
    head         text,                   -- served afterwards (NULL = back to the default)
    previous     text,                   -- served before
    actor        text,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS upd_rollout_log_repo_idx ON upd_rollout_log (repo, id);

-- Bearer tokens for the CI API (/api/ci/*). Only the SHA-256 of a token is stored.
CREATE TABLE IF NOT EXISTS api_tokens (
    id            bigserial PRIMARY KEY,
    name          text NOT NULL UNIQUE,
    token_sha256  text NOT NULL UNIQUE,
    scopes        text[] NOT NULL DEFAULT '{publish}',   -- publish | deploy | read
    created_at    timestamptz NOT NULL DEFAULT now(),
    last_used_at  timestamptz,
    revoked_at    timestamptz
);
