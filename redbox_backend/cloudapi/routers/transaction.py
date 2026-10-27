"""Transaction API stand-in (KCS `TransactionApiClient`, base `KioskClientDefault.TransactionApiUrl`).

Card authorisation follows `config.PAYMENT_MODE`:

- `approve` (default): everything is approved (blocked cards/customers and gift cards without enough
  balance are still declined);
- `decline`: every card authorisation is declined with `(A006)`;
- `rules`: approved unless the card's last four digits are a test value (see DECLINE_RULES) or the card
  has expired.

Every authorisation, reconcile, return and reservation change is stored (tables `cloud_*`, schema
50_cloud.sql). Replayed messages (same MessageId) get the stored answer.
"""
import datetime as _dt
import decimal
import logging
import math
import secrets

from fastapi import APIRouter, Request

from ... import config, db
from .. import customers as cust
from .. import stock
from ..common import (HOSTNAME, body_json, ci, ci_list, check_key, error, kiosk_id, net_datetime, net_offset, now,
                      parse_dt, remember, replayed, reply, to_dec, to_int, to_uuid, touch)

log = logging.getLogger("cloudapi.transaction")
router = APIRouter()

SOURCE = "transaction"
# PAYMENT_MODE=rules: card last four -> (error code, message).
DECLINE_RULES = {
    "0002": ("(A006)", "Declined"),
    "0003": ("(A004)", "Credit card error"),
    "0005": ("(A005)", "AVS failure"),
    "0008": ("(A008)", "Processor error"),
    "0009": ("(A100)", "Authorize processing error"),
}
GIFT_CARD_DEFAULT_BALANCE = decimal.Decimal("25.00")
SUBSCRIPTION_ERRORS = {"(A030)", "(A031)", "(A033)", "(A034)"}


# ------------------------------------------------------------------ cart helpers

def cart_groups(body):
    return ci_list(ci(body, "ShoppingCart", default={}), "Groups")


def cart_items(body, group_types=None):
    for g in cart_groups(body):
        gt = to_int(ci(g, "GroupType"), 0)
        if group_types and gt not in group_types:
            continue
        for item in ci_list(g, "Items"):
            yield gt, item


def cart_total(body):
    total = decimal.Decimal("0")
    for g in cart_groups(body):
        totals = ci(g, "Totals", default={})
        total += to_dec(ci(totals, "GrandTotal"))
    return total


def cart_discounts(body):
    return ci_list(ci(body, "ShoppingCart", default={}), "Discounts")


def card_expired(card):
    m, y = to_int(ci(card, "ExpirationMonth")), to_int(ci(card, "ExpirationYear"))
    if not m or not y:
        return False
    if y < 100:
        y += 2000
    today = _dt.date.today()
    return (y, m) < (today.year, today.month)


async def find_transaction(ref, reservation=False):
    """By our reference number; for reservation pickups first by the reservation reference."""
    if not ref:
        return None
    by_ref = "SELECT * FROM cloud_transactions WHERE reference_number = %s"
    by_resv = ("SELECT * FROM cloud_transactions WHERE reservation_ref = %s AND status <> 'declined' "
               "ORDER BY id DESC LIMIT 1")
    for sql in ((by_resv, by_ref) if reservation else (by_ref, by_resv)):
        row = await db.fetchrow(sql, ref)
        if row is not None:
            return row
    return None


# ------------------------------------------------------------------ authorisation

def payment_errors(card, card_row, customer, amount, gift):
    mode = (config.PAYMENT_MODE or "approve").strip().lower()
    errs = []
    if (card_row and card_row.get("blocked")) or (customer and customer.get("blocked")):
        return [error("(A002)", "Customer disabled")]
    if mode == "decline":
        return [error("(A006)", "Declined (PAYMENT_MODE=decline)")]
    if gift:
        balance = card_row.get("gift_balance") if card_row else None
        if balance is not None and amount > balance:
            errs.append(error("(A006)", f"Gift card balance {balance} is less than {amount}"))
        return errs
    if mode == "rules":
        lf = cust.last_four(card)
        if lf in DECLINE_RULES:
            code, msg = DECLINE_RULES[lf]
            errs.append(error(code, f"{msg} (test card ending {lf})"))
        elif card_expired(card):
            errs.append(error("(A004)", "Card expired"))
    return errs


async def authorize_common(request: Request, kind: str, gift=False):
    bad = check_key(request, SOURCE)
    if bad:
        return bad
    body = await _body(request)
    kiosk = kiosk_id(request, body)
    await touch(kiosk)
    message_id = to_uuid(ci(body, "MessageId"))
    card = ci(body, "CreditCard", default={}) or {}
    card_row = await cust.upsert_card(card, gift=gift)
    if gift and card_row and card_row.get("gift_balance") is None:
        card_row = await db.fetchrow("UPDATE cloud_cards SET gift_balance = %s, gift_status = 2, is_gift_card = true "
                                     "WHERE card_key = %s RETURNING *", GIFT_CARD_DEFAULT_BALANCE, card_row["card_key"])
    customer = await cust.customer_by_cpn(ci(body, "CustomerProfileNumber"))
    if customer is None and card_row and card_row.get("customer_id"):
        customer = await cust.customer_by_id(card_row["customer_id"])
    if customer and card_row:
        await cust.link_card(card_row, customer)
    amount = to_dec(ci(body, "AuthAmount"), None) or cart_total(body)

    errs = payment_errors(card, card_row, customer, amount, gift)

    # Reservation checks (PickupAuthorize / ReservationAuthorize).
    resv_ref = None
    if kind in ("pickup", "reservation"):
        resv_ref = to_int(ci(body, "ReferenceNumber"))
        if resv_ref:
            resv = await db.fetchrow("SELECT * FROM cloud_reservations WHERE reference_number = %s", resv_ref)
            if resv is None:
                await db.execute("INSERT INTO cloud_reservations (reference_number, kiosk_id, card_key, customer_id) "
                                 "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING", resv_ref, kiosk,
                                 card_row["card_key"] if card_row else None, customer["customer_id"] if customer else None)
            elif resv["status"] in ("cancelled", "expired"):
                errs.append(error("(A026)", f"Reservation {resv_ref} is {resv['status']}"))
            elif resv["status"] == "picked_up":
                errs.append(error("(A025)", f"Reservation {resv_ref} was already picked up"))
        else:
            errs.append(error("(A025)", "Missing reservation reference number"))

    has_kiosk_items = any(True for _ in cart_items(body, {1, 2}))
    has_sub_items = any(True for _ in cart_items(body, {3}))
    only_sub_errors = all(e["Code"] in SUBSCRIPTION_ERRORS for e in errs)
    kiosk_ok = has_kiosk_items and (not errs or only_sub_errors)
    sub_ok = has_sub_items and not any(e["Code"] in SUBSCRIPTION_ERRORS for e in errs) and (not has_kiosk_items or kiosk_ok)
    success = (kiosk_ok or sub_ok) if (has_kiosk_items or has_sub_items) else not errs

    txn = await db.fetchrow(
        "INSERT INTO cloud_transactions (kiosk_id, kind, status, message_id, reservation_ref, card_key, customer_id, "
        "amount, errors, request) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
        kiosk, kind, "authorized" if success else "declined", message_id, resv_ref,
        card_row["card_key"] if card_row else None, customer["customer_id"] if customer else None, amount,
        db.jsonb(errs), db.jsonb(_scrub(body)))

    resp = {
        "MessageId": str(message_id) if message_id else None,
        "Success": success,
        "Errors": errs,
        "StatusCode": 200,
        "ReferenceNumber": resv_ref if resv_ref else txn["reference_number"],
        "Email": customer["email"] if customer else None,
        "CustomerProfileFirstName": customer["first_name"] if customer else None,
        "CustomerProfileLastName": customer["last_name"] if customer else None,
        "HasProfile": bool(customer),
        "CustomerProfileNumber": customer["cpn"] if customer else None,
        "ConfirmationStatus": 0,
        "AccountNumber": card_row["account_number"] if card_row else None,
        "Limits": [],
        "KioskTransactionSuccess": kiosk_ok,
        "SubscriptionTransactionSuccess": sub_ok,
    }
    if gift:
        resp["GiftCardSummary"] = {
            "Exists": bool(card_row),
            "Status": (card_row.get("gift_status") or 2) if card_row else 0,
            "Balance": card_row.get("gift_balance") if card_row else 0,
            "MaxDiscs": 0,
            "MaxGames": 0,
        }
    await db.record_event(kiosk, SOURCE, kind + "authorize", {"ReferenceNumber": resp["ReferenceNumber"],
                                                              "Success": success, "Amount": amount, "Errors": errs})
    return reply({k: v for k, v in resp.items() if v is not None} | {"HostName": _host()})


@router.post("/api/transaction/authorize")
async def authorize(request: Request):
    return await authorize_common(request, "authorize")


@router.post("/api/transaction/giftcardauthorize")
async def giftcard_authorize(request: Request):
    return await authorize_common(request, "giftcard", gift=True)


@router.post("/api/transaction/pickupauthorize")
async def pickup_authorize(request: Request):
    return await authorize_common(request, "pickup")


@router.post("/api/transaction/reservationauthorize")
async def reservation_authorize(request: Request):
    return await authorize_common(request, "reservation")


# ------------------------------------------------------------------ reconcile

async def apply_reconcile(request: Request, endpoint: str, pickup=False):
    bad = check_key(request, SOURCE)
    if bad:
        return bad
    body = await _body(request)
    kiosk = kiosk_id(request, body)
    await touch(kiosk)
    message_id = to_uuid(ci(body, "MessageId"))
    prev = await replayed(message_id, endpoint)
    if prev:
        return reply(prev[1], prev[0])

    ref = to_int(ci(body, "ReferenceNumber"))
    card = ci(body, "CreditCard", default={}) or {}
    card_row = await cust.upsert_card(card) if card else None
    txn = await find_transaction(ref, reservation=pickup)
    if txn is None:
        kind = "offline" if ci(body, "IsOffline") else ("pickup" if pickup else "unmatched")
        txn = await db.fetchrow(
            "INSERT INTO cloud_transactions (kiosk_id, kind, status, message_id, reservation_ref, card_key, amount) "
            "VALUES (%s, %s, 'authorized', %s, %s, %s, %s) RETURNING *",
            kiosk, kind, message_id, ref if pickup else None, card_row["card_key"] if card_row else None, cart_total(body))
        if ref and not pickup:
            log.info("reconcile for unknown reference %s (kiosk %s); recorded as %s", ref, kiosk, kind)
    already = txn["status"] == "reconciled"

    # Who is the customer?
    customer = await cust.customer_by_cpn(ci(body, "CustomerProfileNumber"))
    if customer is None and txn.get("customer_id"):
        customer = await cust.customer_by_id(txn["customer_id"])
    if customer is None and card_row and card_row.get("customer_id"):
        customer = await cust.customer_by_id(card_row["customer_id"])
    email = ci(body, "Email")
    if customer is None and email:
        customer = await cust.find_or_create_by_email(email)
    elif customer is not None and email and not customer.get("email"):
        await db.execute("UPDATE cloud_customers SET email = %s WHERE customer_id = %s AND NOT EXISTS "
                         "(SELECT 1 FROM cloud_customers WHERE lower(email) = lower(%s))", email, customer["customer_id"], email)
    if card_row is None and txn.get("card_key"):
        card_row = await db.fetchrow("SELECT * FROM cloud_cards WHERE card_key = %s", txn["card_key"])
    await cust.link_card(card_row, customer)
    if ci(body, "Blocklisted") and card_row:
        await db.execute("UPDATE cloud_cards SET blocked = true WHERE card_key = %s", card_row["card_key"])

    points = 0
    charged = decimal.Decimal("0")
    if not already:
        for gt, item in cart_items(body):
            vend = to_int(ci(item, "VendStatus"), 0)
            barcode = ci(item, "Barcode")
            product_id = to_int(ci(item, "ProductId"))
            pricing = ci(item, "Pricing", default={}) or {}
            if gt == 3:
                status = "subscription"
                if customer and ci(item, "SubscriptionId"):
                    await db.execute("INSERT INTO cloud_customer_subscriptions (customer_id, subscription_id, subscription_plan_id) "
                                     "VALUES (%s, %s, %s)", customer["customer_id"], "RBPLUS", str(ci(item, "SubscriptionId")))
            elif vend != 0:
                status = "not_vended"
            elif gt == 2:
                status = "purchased"
                await stock.disc_left_kiosk(kiosk, barcode, "Sold")
                charged += to_dec(ci(pricing, "Purchase"))
            else:
                status = "out"
                await stock.disc_left_kiosk(kiosk, barcode, "Rented")
                charged += to_dec(ci(pricing, "InitialNight"))
            if status in ("out", "purchased"):
                if barcode:  # a disc vended again must have come back without a recorded return
                    await db.execute("UPDATE cloud_rentals SET status = 'returned', returned_at = now(), "
                                     "return_kiosk_id = %s WHERE barcode = %s AND status = 'out'", kiosk, str(barcode))
                await stock.learn_barcode(barcode, product_id)
                points += cust.accrual_points(item)
            await db.execute(
                "INSERT INTO cloud_rentals (transaction_id, kiosk_id, group_type, barcode, product_id, product_type, "
                "product_family, vend_status, status, pricing) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                txn["id"], kiosk, gt, barcode, product_id, ci(item, "ProductType"), ci(item, "ProductFamily"), vend,
                status, db.jsonb(pricing))

        # Discounts: promo codes and Perks redemptions.
        for d in cart_discounts(body):
            dtype = to_int(ci(d, "DiscountType"), 0)
            if dtype == 1 and ci(d, "PromotionCode"):
                code = str(ci(d, "PromotionCode")).upper()
                await db.execute("UPDATE cloud_promo_codes SET uses = uses + 1 WHERE code = %s", code)
                await db.execute("INSERT INTO cloud_promo_redemptions (code, transaction_id, kiosk_id, amount) "
                                 "VALUES (%s, %s, %s, %s)", code, txn["id"], kiosk, to_dec(ci(d, "Amount")))
            elif dtype == 2 and customer and to_int(ci(d, "RedemptionPoints")):
                await cust.add_points(customer, -to_int(ci(d, "RedemptionPoints")), "redemption", txn["reference_number"])
        offer = ci(body, "RedeemedCustomerOffer")
        if offer:
            oid = to_int(ci(offer, "StoredOfferId")) or to_int(ci(offer, "OfferId"))
            if oid:
                await db.execute("UPDATE cloud_customer_offers SET redeemed_at = now() WHERE offer_id = %s", oid)

        if customer and customer.get("loyalty_enrolled") and points:
            await cust.add_points(customer, points, "accrual", txn["reference_number"])
            await cust.refresh_tier(customer)
        if card_row and card_row.get("is_gift_card") and card_row.get("gift_balance") is not None:
            total = cart_total(body)
            await db.execute("UPDATE cloud_cards SET gift_balance = GREATEST(0, gift_balance - %s) WHERE card_key = %s",
                             total, card_row["card_key"])

        await db.execute(
            "UPDATE cloud_transactions SET status = 'reconciled', reconciled_at = now(), reconcile = %s, "
            "customer_id = COALESCE(%s, customer_id), card_key = COALESCE(card_key, %s) WHERE id = %s",
            db.jsonb(_scrub(body)), customer["customer_id"] if customer else None,
            card_row["card_key"] if card_row else None, txn["id"])

    if pickup:
        resv_ref = txn.get("reservation_ref") or ref
        if resv_ref:
            await db.execute(
                "INSERT INTO cloud_reservations (reference_number, kiosk_id, status) VALUES (%s, %s, 'picked_up') "
                "ON CONFLICT (reference_number) DO UPDATE SET status = 'picked_up', updated_at = now()", resv_ref, kiosk)

    resp = {"Success": True, "Errors": [], "HostName": _host(), "StatusCode": 200,
            "CustomerProfileNumber": customer["cpn"] if customer else None}
    await db.record_event(kiosk, SOURCE, endpoint, {"ReferenceNumber": txn["reference_number"], "Points": points,
                                                    "Charged": charged, "Duplicate": already})
    await remember(message_id, endpoint, kiosk, 200, resp)
    return reply(resp)


@router.post("/api/transaction/reconcile")
async def reconcile(request: Request):
    return await apply_reconcile(request, "reconcile")


@router.post("/api/transaction/reservationPickup")
@router.post("/api/transaction/reservationpickup")
async def reservation_pickup(request: Request):
    return await apply_reconcile(request, "reservationpickup", pickup=True)


# ------------------------------------------------------------------ returns

def rental_charges(rental, returned_at):
    """Extra nights after the included ones (1 h grace); capped at the non-return price."""
    pricing = rental.get("pricing") or {}
    initial_days = to_int(ci(pricing, "InitialDays"), 1) or 1
    extra_night = to_dec(ci(pricing, "ExtraNight"))
    non_return = to_dec(ci(pricing, "NonReturn"))
    hours = max(0.0, (returned_at - rental["rented_at"]).total_seconds() / 3600 - 1)
    nights = max(1, math.ceil(hours / 24))
    extra = max(0, nights - initial_days)
    charge = extra_night * extra
    if non_return and charge > non_return:
        charge = non_return
    return extra, charge


@router.post("/api/transaction/return")
async def transaction_return(request: Request):
    bad = check_key(request, SOURCE)
    if bad:
        return bad
    body = await _body(request)
    kiosk = kiosk_id(request, body)
    await touch(kiosk)
    message_id = to_uuid(ci(body, "MessageId"))
    prev = await replayed(message_id, "return")
    if prev:
        return reply(prev[1], prev[0])
    barcode = ci(body, "Barcode")
    if not barcode:
        resp = {"MessageId": str(message_id) if message_id else None, "Success": False, "HostName": _host(),
                "Errors": [error("(RTN006)", "Invalid barcode")], "StatusCode": 400}
        return reply(resp, 400)
    barcode = str(barcode)
    returned_at = parse_dt(ci(body, "ReturnDate"))
    if returned_at is None or abs((returned_at - now()).total_seconds()) > 3 * 86400:
        returned_at = now()
    deck, slot = to_int(ci(body, "Deck")), to_int(ci(body, "Slot"))

    rental = await db.fetchrow("SELECT * FROM cloud_rentals WHERE barcode = %s AND status = 'out' ORDER BY rented_at DESC LIMIT 1",
                               barcode)
    if rental:
        extra, charge = rental_charges(rental, returned_at)
        await db.execute("UPDATE cloud_rentals SET status = 'returned', returned_at = %s, return_kiosk_id = %s, "
                         "extra_nights = %s, extra_charge = %s WHERE id = %s", returned_at, kiosk, extra, charge, rental["id"])
    else:
        log.info("return of barcode %s at kiosk %s without an open rental", barcode, kiosk)
    await stock.disc_returned(kiosk, barcode, deck, slot)
    await db.execute("INSERT INTO cloud_returns (message_id, kiosk_id, barcode, return_type, deck, slot, return_date, "
                     "failed_security_read, rental_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                     message_id, kiosk, barcode, to_int(ci(body, "ReturnType")), deck, slot, returned_at,
                     bool(ci(body, "FailedSecurityRead")), rental["id"] if rental else None)
    resp = {"MessageId": str(message_id) if message_id else None, "Success": True, "Errors": [],
            "HostName": _host(), "StatusCode": 200}
    await db.record_event(kiosk, SOURCE, "return", {"Barcode": barcode, "RentalId": rental["id"] if rental else None})
    await remember(message_id, "return", kiosk, 200, resp)
    return reply({k: v for k, v in resp.items() if v is not None})


@router.get("/api/transaction/return/{barcode}/promocheck")
async def return_promo_check(barcode: str, request: Request):
    check_key(request, SOURCE)
    kiosk = kiosk_id(request)
    rental = await db.fetchrow("SELECT * FROM cloud_rentals WHERE barcode = %s AND group_type = 1 "
                               "AND status IN ('out', 'returned') ORDER BY rented_at DESC LIMIT 1", barcode)
    if rental is None:
        return reply({"Error": f"No rental found for barcode {barcode}", "IsLastDisc": False, "Discs": []}, 404)
    discs = await db.fetch("SELECT barcode, status FROM cloud_rentals WHERE transaction_id = %s AND group_type = 1 "
                           "AND status IN ('out', 'returned') ORDER BY id", rental["transaction_id"])
    disc_list = [{"Barcode": d["barcode"], "IsReturned": d["status"] == "returned"} for d in discs]
    is_last = all(d["IsReturned"] or d["Barcode"] == barcode for d in disc_list)
    promotion = await _return_promotion(rental["transaction_id"], kiosk)
    return reply({"Promotion": promotion, "IsLastDisc": is_last, "Discs": disc_list})


async def _return_promotion(transaction_id, kiosk):
    cfg = await db.fetchrow("SELECT * FROM cloud_return_promo WHERE id = 1")
    if not cfg or not cfg["enabled"]:
        return None
    issued = await db.fetchrow("SELECT * FROM cloud_return_promo_issued WHERE transaction_id = %s", transaction_id)
    if issued is None:
        code = (cfg["code_prefix"] or "RV") + secrets.token_hex(3).upper()
        expires = now() + _dt.timedelta(days=cfg["valid_days"])
        if cfg["promo_type"] == 1:
            data = {"Amount": 0, "PromotionIntentCode": "FirstNightFreeItem", "ActionType": "Discount", "AllowFullDiscount": True}
        else:
            data = {"Amount": cfg["amount"] or 1, "PromotionIntentCode": "FixedValue", "ActionType": "Discount"}
        await db.execute("INSERT INTO cloud_promo_codes (code, valid_from, valid_to, max_uses, data, note) "
                         "VALUES (%s, now(), %s, 1, %s, 'return-visit offer') ON CONFLICT DO NOTHING",
                         code, expires, db.jsonb(data))
        issued = await db.fetchrow("INSERT INTO cloud_return_promo_issued (transaction_id, code, expires_at) "
                                   "VALUES (%s, %s, %s) ON CONFLICT (transaction_id) DO UPDATE SET code = cloud_return_promo_issued.code "
                                   "RETURNING *", transaction_id, code, expires)
    return {"PromoCode": issued["code"], "PromoType": cfg["promo_type"],
            "Amount": cfg["amount"] if cfg["promo_type"] == 0 else None,
            "ExpirationDate": net_datetime(issued["expires_at"])}


# ------------------------------------------------------------------ reservations

async def _reservation_status(request: Request, endpoint: str, status: str):
    bad = check_key(request, SOURCE)
    if bad:
        return bad
    body = await _body(request)
    kiosk = kiosk_id(request, body)
    await touch(kiosk)
    message_id = to_uuid(ci(body, "MessageId"))
    prev = await replayed(message_id, endpoint)
    if prev:
        return reply(prev[1], prev[0])
    ref = to_int(ci(body, "ReferenceNumber"))
    if not ref:
        resp = {"Success": False, "Errors": [error("(V001)", "ReferenceNumber is required")], "HostName": _host(),
                "StatusCode": 400}
        return reply(resp, 400)
    await db.execute(
        "INSERT INTO cloud_reservations (reference_number, kiosk_id, status, reason_code) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (reference_number) DO UPDATE SET status = CASE WHEN cloud_reservations.status = 'picked_up' "
        "THEN cloud_reservations.status ELSE EXCLUDED.status END, reason_code = EXCLUDED.reason_code, updated_at = now()",
        ref, kiosk, status, ci(body, "ReasonCode"))
    resp = {"Success": True, "Errors": [], "HostName": _host(), "StatusCode": 200}
    await db.record_event(kiosk, SOURCE, endpoint, {"ReferenceNumber": ref, "ReasonCode": ci(body, "ReasonCode")})
    await remember(message_id, endpoint, kiosk, 200, resp)
    return reply(resp)


@router.post("/api/reservation/cancel")
async def reservation_cancel(request: Request):
    return await _reservation_status(request, "reservationcancel", "cancelled")


@router.post("/api/reservation/expire")
async def reservation_expire(request: Request):
    return await _reservation_status(request, "reservationexpire", "expired")


@router.post("/api/reservation/validatepickup")
async def reservation_validate_pickup(request: Request):
    bad = check_key(request, SOURCE)
    if bad:
        return bad
    body = await _body(request)
    kiosk = kiosk_id(request, body)
    await touch(kiosk)
    card = ci(body, "CreditCard", default={}) or {}
    card_row = await cust.upsert_card(card) if card else None
    customer = await cust.customer_by_id(card_row["customer_id"]) if card_row and card_row.get("customer_id") else None
    refs = [to_int(r) for r in ci_list(body, "ReferenceNumbers") if to_int(r)]
    valid, cancelled, errs = [], [], []
    blocked = (card_row and card_row.get("blocked")) or (customer and customer.get("blocked"))
    for ref in refs:
        resv = await db.fetchrow("SELECT * FROM cloud_reservations WHERE reference_number = %s", ref)
        if resv is None:
            await db.execute("INSERT INTO cloud_reservations (reference_number, kiosk_id, card_key, customer_id) "
                             "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING", ref, kiosk,
                             card_row["card_key"] if card_row else None, customer["customer_id"] if customer else None)
            resv = {"status": "reserved", "expires_at": None}
        expired = resv.get("expires_at") is not None and resv["expires_at"] < now()
        if blocked or resv["status"] != "reserved" or expired:
            cancelled.append(ref)
        else:
            valid.append(ref)
            if card_row:
                await db.execute("UPDATE cloud_reservations SET card_key = COALESCE(card_key, %s), updated_at = now() "
                                 "WHERE reference_number = %s", card_row["card_key"], ref)
    if blocked:
        errs.append(error("(A002)", "Customer disabled"))
    resp = {
        "Success": bool(valid) and not blocked,
        "Errors": errs,
        "HostName": _host(),
        "StatusCode": 200,
        "CustomerProfileNumber": customer["cpn"] if customer else None,
        "AccountNumber": card_row["account_number"] if card_row else None,
        "ReferenceNumbers": refs,
        "CancelledReservations": cancelled,
        "ValidReservations": valid,
        "AllowRop": True,
        "TransactionEntities": [],
    }
    await db.record_event(kiosk, SOURCE, "validatepickup", {"Valid": valid, "Cancelled": cancelled})
    return reply(resp)


# ------------------------------------------------------------------ customer offers

@router.post("/api/customeroffers")
async def customer_offers(request: Request):
    check_key(request, SOURCE)
    body = await _body(request)
    kiosk = kiosk_id(request, body)
    await touch(kiosk)
    card = ci(body, "CreditCard", default={}) or {}
    card_row = await cust.upsert_card(card) if card else None
    customer = await cust.customer_by_id(card_row["customer_id"]) if card_row and card_row.get("customer_id") else None
    stored, new = [], []
    rows = await db.fetch("SELECT * FROM cloud_customer_offers WHERE redeemed_at IS NULL "
                          "AND (expires_at IS NULL OR expires_at > now()) AND (customer_id IS NULL OR customer_id = %s) "
                          "ORDER BY offer_id", customer["customer_id"] if customer else -1)
    for r in rows:
        offer = {"IncludeTitleIds": [], "ExcludeTitleIds": [], "IncludeFormatIds": [], "ExcludeFormatIds": [],
                 "OfferProducts": [], "DiscountAmount": 0}
        offer.update(r["data"] or {})
        offer.update({"OfferId": r["offer_id"], "CampaignId": r["campaign_id"],
                      "ExpirationDate": net_offset(r["expires_at"]) if r["expires_at"] else None})
        if r["customer_id"]:
            offer["StoredOfferId"] = r["offer_id"]
            stored.append(offer)
        else:
            new.append(offer)
    return reply({"Success": True, "Errors": [], "HostName": _host(), "StatusCode": 200,
                  "AccountNumber": card_row["account_number"] if card_row else None,
                  "CustomerProfileNumber": customer["cpn"] if customer else None,
                  "EmailAddress": customer["email"] if customer else None,
                  "StoredOffers": stored, "NewOffers": new})


# ------------------------------------------------------------------ helpers

async def _body(request: Request):
    body = await body_json(request, {})
    return body if isinstance(body, dict) else {}


def _host():
    return HOSTNAME


def _scrub(body):
    """Request copy without card data."""
    if not isinstance(body, dict):
        return body
    out = {}
    for k, v in body.items():
        if k.lower() == "creditcard" and isinstance(v, dict):
            out[k] = {"LastFour": cust.last_four(v), "BIN": ci(v, "BIN"), "CardType": ci(v, "CardType"),
                      "CardSourceType": ci(v, "CardSourceType")}
        else:
            out[k] = v
    return out
