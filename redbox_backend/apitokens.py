"""Bearer tokens for non-interactive clients (CI) of the dashboard's /api/ci/* routes.

`admin api-token create <name> --scope publish --scope deploy` prints a token once; only its SHA-256 is kept
(`api_tokens`). Scopes: publish (upload releases), deploy (roll out, roll back, deploy file sets), read (status;
every token can read).
"""
import hashlib
import secrets

from . import db

SCOPES = ("publish", "deploy", "read")
PREFIX = "rbt_"


def _digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


async def create(name, scopes):
    bad = set(scopes) - set(SCOPES)
    if bad:
        raise ValueError(f"unknown scopes {sorted(bad)}; use {', '.join(SCOPES)}")
    token = PREFIX + secrets.token_urlsafe(32)
    await db.execute("INSERT INTO api_tokens (name, token_sha256, scopes) VALUES (%s,%s,%s)",
                     name, _digest(token), sorted(set(scopes) | {"read"}))
    return token


async def check(token, scope):
    """The token's row if it is valid and has the scope, else None."""
    if not token or not token.startswith(PREFIX):
        return None
    row = await db.fetchrow("UPDATE api_tokens SET last_used_at=now() WHERE token_sha256=%s AND revoked_at IS NULL"
                            " RETURNING id, name, scopes", _digest(token))
    if row is None or scope not in row["scopes"]:
        return None
    return row


async def list_all():
    return await db.fetch("SELECT id, name, scopes, created_at, last_used_at, revoked_at FROM api_tokens ORDER BY id")


async def revoke(name):
    return await db.execute("UPDATE api_tokens SET revoked_at=now() WHERE name=%s AND revoked_at IS NULL", name)
