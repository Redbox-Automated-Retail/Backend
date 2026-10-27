"""Inventory API stand-in (KCS `InventoryApiClient`, base `KioskClientDefault.InventoryApiUrl`)."""
import datetime as _dt
import logging

from fastapi import APIRouter, Request

from ... import db
from ..common import (HOSTNAME, body_json, check_key, ci, ci_list, error, kiosk_id, parse_dt, reply, to_int, to_uuid,
                      touch)

log = logging.getLogger("cloudapi.inventory")
router = APIRouter()
SOURCE = "inventory"
ORDER_TYPES = {1: "RebalancingExtract", 2: "Fill", 3: "ThinningExtract"}


def _inventory_response(message_id, success=True, errors=None, status=200):
    body = {"Success": success, "Errors": errors or [], "HostName": HOSTNAME, "StatusCode": status}
    if message_id:
        body["MessageId"] = str(message_id)
    return reply(body, status)


@router.post("/api/kiosk/sync")
async def kiosk_sync(request: Request):
    """Full disc snapshot (gzip body): replaces what is stored for the kiosk."""
    bad = check_key(request, SOURCE)
    if bad:
        return bad
    body = await body_json(request, {})
    if not isinstance(body, dict):
        return _inventory_response(None, False, [error("(V000)", "body must be a JSON object")], 400)
    kiosk = kiosk_id(request, body)
    message_id = to_uuid(ci(body, "MessageId"))
    if not kiosk:
        return _inventory_response(message_id, False, [error("(V001)", "KioskId is required")], 400)
    await touch(kiosk)
    discs = ci_list(body, "Discs")
    rows = []
    seen = set()
    for d in discs:
        barcode = ci(d, "Barcode")
        if not barcode or str(barcode) in seen:
            continue
        seen.add(str(barcode))
        rows.append((kiosk, str(barcode), to_int(ci(d, "Deck")), to_int(ci(d, "Slot")), str(ci(d, "Status", default="In"))))
    async with db.pool().connection() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM cloud_inventory WHERE kiosk_id = %s", (kiosk,))
            if rows:
                async with conn.cursor() as cur:
                    await cur.executemany("INSERT INTO cloud_inventory (kiosk_id, barcode, deck, slot, status) "
                                          "VALUES (%s, %s, %s, %s, %s)", rows)
            await conn.execute("INSERT INTO cloud_inventory_syncs (kiosk_id, message_id, disc_count) VALUES (%s, %s, %s)",
                               (kiosk, message_id, len(rows)))
    await db.record_event(kiosk, SOURCE, "sync", {"MessageId": message_id, "Discs": len(rows)})
    return _inventory_response(message_id)


@router.post("/api/disc/discaction")
async def disc_action(request: Request):
    bad = check_key(request, SOURCE)
    if bad:
        return bad
    body = await body_json(request, {}) or {}
    kiosk = kiosk_id(request, body)
    await touch(kiosk)
    await db.record_event(kiosk, SOURCE, "discaction", body)
    return _inventory_response(to_uuid(ci(body, "MessageId")))


@router.post("/api/disc/discfraud")
async def disc_fraud(request: Request):
    bad = check_key(request, SOURCE)
    if bad:
        return bad
    body = await body_json(request, {}) or {}
    kiosk = kiosk_id(request, body)
    await touch(kiosk)
    await db.record_event(kiosk, SOURCE, "discfraud", body)
    return _inventory_response(to_uuid(ci(body, "MessageId")))


@router.post("/api/disc/updatemerchandizingstatus")
async def update_merchandizing_status(request: Request):
    bad = check_key(request, SOURCE)
    if bad:
        return bad
    body = await body_json(request, [])
    updates = body if isinstance(body, list) else ([body] if isinstance(body, dict) else [])
    kiosk = kiosk_id(request)
    await touch(kiosk)
    failed = []
    for u in updates:
        barcode = ci(u, "Barcode")
        if not barcode:
            failed.append(u)
            continue
        params = (to_int(ci(u, "MerchandStatus")), to_int(ci(u, "ThinReasonCode")), parse_dt(ci(u, "ThinDateUTC")))
        if kiosk:
            await db.execute("INSERT INTO cloud_inventory (kiosk_id, barcode, status, merch_status, thin_reason, thin_date) "
                             "VALUES (%s, %s, 'In', %s, %s, %s) ON CONFLICT (kiosk_id, barcode) DO UPDATE SET "
                             "merch_status = EXCLUDED.merch_status, thin_reason = EXCLUDED.thin_reason, "
                             "thin_date = EXCLUDED.thin_date, updated_at = now()", kiosk, str(barcode), *params)
        else:
            await db.execute("UPDATE cloud_inventory SET merch_status = %s, thin_reason = %s, thin_date = %s, "
                             "updated_at = now() WHERE barcode = %s", *params, str(barcode))
    await db.record_event(kiosk, SOURCE, "merchandizingstatus", {"Count": len(updates), "Failed": len(failed)})
    return reply({"Data": failed, "error": None, "hostName": HOSTNAME})


@router.get("/api/merchandizingorder/{kiosk}/{order_date}")
async def merchandizing_orders(kiosk: int, order_date: str, request: Request, orderType: int | None = None):
    check_key(request, SOURCE)
    await touch(kiosk)
    try:
        day = _dt.date.fromisoformat(order_date[:10])
    except ValueError:
        return reply({"Error": "order date must be yyyy-MM-dd"}, 400)
    sql = "SELECT * FROM cloud_merch_orders WHERE kiosk_id = %s AND order_date = %s"
    args = [kiosk, day]
    if orderType:
        sql += " AND order_type = %s"
        args.append(orderType)
    rows = await db.fetch(sql + " ORDER BY id", *args)
    return reply([{"KioskId": r["kiosk_id"], "MerchandizingOrderType": ORDER_TYPES.get(r["order_type"], r["order_type"]),
                   "TitleId": r["title_id"], "UnitCount": r["unit_count"]} for r in rows])


@router.get("/api/productgroupsortorder/{sort_type}")
async def product_group_sort_order(sort_type: int, request: Request):
    """Explicit table rows, else product groups ranked by rentals of the last 14 days."""
    check_key(request, SOURCE)
    rows = await db.fetch("SELECT product_group_id, forecasted_rents FROM cloud_product_group_sort WHERE sort_type = %s "
                          "ORDER BY forecasted_rents DESC, product_group_id", sort_type)
    if not rows:
        rows = await db.fetch(
            "SELECT p.product_group_id, count(*)::int AS forecasted_rents FROM cloud_rentals r "
            "JOIN cloud_products p ON p.title_id = r.product_id WHERE r.group_type = 1 AND r.status IN ('out', 'returned') "
            "AND r.rented_at > now() - interval '14 days' GROUP BY p.product_group_id ORDER BY 2 DESC LIMIT 500")
    return reply({"ProductGroupSortOrderType": sort_type,
                  "ProductGroupSortOrders": [{"ProductGroupId": r["product_group_id"],
                                              "ForecastedRents": r["forecasted_rents"]} for r in rows]})
