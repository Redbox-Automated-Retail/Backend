"""Read-only status pages for operators (not used by kiosks)."""
from fastapi import APIRouter

from ... import db

router = APIRouter(prefix="/_admin")


@router.get("/kiosks")
async def kiosks():
    return await db.fetch("SELECT * FROM kiosks ORDER BY kiosk_id")


@router.get("/events")
async def events(kiosk_id: int = None, limit: int = 100):
    return await db.fetch("SELECT * FROM events WHERE (%s::bigint IS NULL OR kiosk_id = %s) ORDER BY id DESC LIMIT %s",
                          kiosk_id, kiosk_id, limit)
