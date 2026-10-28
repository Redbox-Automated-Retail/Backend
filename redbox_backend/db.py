"""PostgreSQL access shared by all services.

Each service calls `await db.init()` at start-up. The schema files in `schema/` are applied in name
order under an advisory lock, so containers starting at the same time do not race. Every statement in
them must be idempotent (`CREATE ... IF NOT EXISTS`, `ON CONFLICT DO NOTHING`).
"""
import asyncio
import json
import logging
import pathlib

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from . import config

log = logging.getLogger("db")

SCHEMA_DIR = pathlib.Path(__file__).parent / "schema"
_pool = None


def pool():
    if _pool is None:
        raise RuntimeError("db.init() has not been called")
    return _pool


async def init(min_size=1, max_size=10, apply_schema=True):
    global _pool
    if _pool is not None:
        return _pool
    for attempt in range(60):
        try:
            p = AsyncConnectionPool(config.DATABASE_URL, min_size=min_size, max_size=max_size,
                                    kwargs={"row_factory": dict_row, "autocommit": True}, open=False)
            await p.open(wait=True, timeout=5)
            _pool = p
            break
        except Exception as e:  # database still starting
            log.info("waiting for database (%s): %s", attempt, e)
            await asyncio.sleep(2)
    else:
        raise RuntimeError("database not reachable")
    if apply_schema:
        await migrate()
    return _pool


async def migrate():
    async with pool().connection() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(7002)")
            for path in sorted(SCHEMA_DIR.glob("*.sql")):
                await conn.execute(path.read_text(encoding="utf-8"))
    log.info("schema applied")


async def close():
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


async def fetch(sql, *args):
    async with pool().connection() as conn:
        cur = await conn.execute(sql, args or None)
        return await cur.fetchall() if cur.description else []


async def fetchrow(sql, *args):
    rows = await fetch(sql, *args)
    return rows[0] if rows else None


async def fetchval(sql, *args):
    row = await fetchrow(sql, *args)
    return None if row is None else next(iter(row.values()))


async def execute(sql, *args):
    async with pool().connection() as conn:
        cur = await conn.execute(sql, args or None)
        return cur.rowcount


async def executemany(sql, rows):
    rows = list(rows)
    if not rows:
        return
    async with pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.executemany(sql, rows)


def jsonb(value):
    """Wrap a Python value for a jsonb parameter."""
    return Jsonb(value, dumps=lambda v: json.dumps(v, default=str))


async def record_event(kiosk_id, source, kind, payload=None):
    """Append to the generic event log (telemetry, status messages, pings, ...)."""
    try:
        kid = int(kiosk_id) if kiosk_id not in (None, "") else None
    except (TypeError, ValueError):
        kid = None
    await execute("INSERT INTO events (kiosk_id, source, kind, payload) VALUES (%s, %s, %s, %s)",
                  kid, source, kind, jsonb(payload))


async def touch_kiosk(kiosk_id, **fields):
    """Create the kiosk row if needed and update last_seen plus any given columns."""
    try:
        kid = int(kiosk_id)
    except (TypeError, ValueError):
        return
    if kid <= 0:
        return
    await execute("INSERT INTO kiosks (kiosk_id) VALUES (%s) ON CONFLICT (kiosk_id) DO UPDATE SET last_seen_at = now()",
                  kid)
    allowed = {"market", "banner", "state", "timezone", "ucs_version", "info"}
    for key, value in fields.items():
        if key in allowed and value is not None:
            if key == "info":
                await execute("UPDATE kiosks SET info = info || %s WHERE kiosk_id = %s", jsonb(value), kid)
            else:
                await execute(f"UPDATE kiosks SET {key} = %s WHERE kiosk_id = %s", value, kid)
