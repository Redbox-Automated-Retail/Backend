"""Product Reels API stand-in (KCS `ProductReelsApiClient`, base `KioskClientDefault.ProductReelsApiUrl`, no auth).

`POST products/reels/bulkmorelikethis {"productIds": ["<product group id>", ...]}` ->
`[{"id": "<group id>", "productIds": ["<group id>", ...]}]`.

Answers from `cloud_more_like_this`; for groups not listed there, ranks the product groups most often
rented by the customers who rented the asked group (needs `cloud_products` to map titles to groups).
Groups with nothing to recommend are left out (KCS ignores them).
"""
import logging

from fastapi import APIRouter, Request

from ... import db
from ..common import body_json, ci_list, reply, to_int

log = logging.getLogger("cloudapi.reels")
router = APIRouter()
LIMIT = 12


async def _co_rented(group_id):
    rows = await db.fetch(
        "WITH buyers AS ("
        "  SELECT DISTINCT t.customer_id FROM cloud_rentals r JOIN cloud_transactions t ON t.id = r.transaction_id "
        "  JOIN cloud_products p ON p.title_id = r.product_id "
        "  WHERE p.product_group_id = %s AND t.customer_id IS NOT NULL) "
        "SELECT p.product_group_id, count(*) AS n FROM cloud_rentals r JOIN cloud_transactions t ON t.id = r.transaction_id "
        "JOIN cloud_products p ON p.title_id = r.product_id "
        "WHERE t.customer_id IN (SELECT customer_id FROM buyers) AND p.product_group_id <> %s "
        "GROUP BY p.product_group_id ORDER BY n DESC, p.product_group_id LIMIT %s", group_id, group_id, LIMIT)
    return [r["product_group_id"] for r in rows]


@router.post("/products/reels/bulkmorelikethis")
async def bulk_more_like_this(request: Request):
    body = await body_json(request, {})
    ids = [to_int(x) for x in ci_list(body if isinstance(body, dict) else {}, "productIds") if to_int(x)]
    out = []
    explicit = {r["product_group_id"]: r["product_ids"] for r in
                await db.fetch("SELECT * FROM cloud_more_like_this WHERE product_group_id = ANY(%s)", ids)} if ids else {}
    for gid in ids:
        rec = explicit.get(gid) or await _co_rented(gid)
        if rec:
            out.append({"id": str(gid), "productIds": [str(x) for x in rec]})
    return reply(out)
