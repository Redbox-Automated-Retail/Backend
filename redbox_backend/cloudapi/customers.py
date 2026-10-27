"""Customers, payment cards and Redbox Perks points, shared by the Transaction API and Proxy Service."""
import hashlib
import secrets

from .. import db
from .common import ci, to_int

# Perks points (invented, see backend/docs/cloud-apis.md): earned per vended item, cost of a free 1-night item.
ACCRUAL_POINTS = 100
REDEMPTION_POINTS = {"dvd": 1500, "bluray": 1800, "4k": 2100, "game": 2700}
TIERS = [(20, "Superstar"), (10, "Star"), (0, "Member")]  # rentals this calendar year -> tier


def product_class(item) -> str:
    family = str(ci(item, "ProductFamily", default="") or "").lower()
    ptype = str(ci(item, "ProductType", default="") or "").lower()
    if "game" in family or any(x in ptype for x in ("xbox", "ps4", "ps5", "ps3", "wii", "switch", "nintendo", "game")):
        return "game"
    if "4k" in ptype or "uhd" in ptype:
        return "4k"
    if "blu" in ptype:
        return "bluray"
    return "dvd"


def accrual_points(item) -> int:
    return ACCRUAL_POINTS


def redemption_points(item) -> int:
    return REDEMPTION_POINTS[product_class(item)]


def card_key(card) -> str | None:
    """Stable fingerprint of a payment card from what the kiosk sends (no PAN is stored)."""
    if not isinstance(card, dict):
        return None
    card_id = ci(card, "CardId")
    if card_id:
        basis = "id:" + str(card_id)
    else:
        number = str(ci(card, "Number", default="") or "")
        if number.isdigit() and len(number) >= 12 and not ci(card, "IsEncrypted"):
            basis = "pan:" + number
        else:
            parts = [ci(card, "BIN"), last_four(card), ci(card, "ExpirationMonth"), ci(card, "ExpirationYear"),
                     str(ci(card, "LastName", default="") or "").upper()]
            if not any(parts[:2]):
                return None
            basis = "card:" + "|".join(str(p or "") for p in parts)
    return hashlib.sha256(basis.encode()).hexdigest()


def last_four(card) -> str | None:
    if not isinstance(card, dict):
        return None
    lf = ci(card, "LastFour")
    if lf:
        return str(lf)[-4:]
    number = str(ci(card, "Number", default="") or "")
    return number[-4:] if number[-4:].isdigit() else None


async def upsert_card(card, gift=False):
    key = card_key(card)
    if not key:
        return None
    row = await db.fetchrow(
        "INSERT INTO cloud_cards (card_key, bin, last_four, card_type, is_gift_card) VALUES (%s, %s, %s, %s, %s) "
        "ON CONFLICT (card_key) DO UPDATE SET last_seen_at = now(), "
        "  is_gift_card = cloud_cards.is_gift_card OR EXCLUDED.is_gift_card "
        "RETURNING *", key, ci(card, "BIN"), last_four(card), to_int(ci(card, "CardType")), gift)
    return row


async def customer_by_cpn(cpn):
    if not cpn:
        return None
    return await db.fetchrow("SELECT * FROM cloud_customers WHERE cpn = %s", str(cpn))


async def customer_by_id(cid):
    if not cid:
        return None
    return await db.fetchrow("SELECT * FROM cloud_customers WHERE customer_id = %s", cid)


async def customer_by_email(email):
    if not email:
        return None
    return await db.fetchrow("SELECT * FROM cloud_customers WHERE lower(email) = lower(%s)", email.strip())


def normalize_phone(phone):
    if not phone:
        return None
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits or None


async def customer_by_phone(phone):
    phone = normalize_phone(phone)
    if not phone:
        return None
    return await db.fetchrow("SELECT * FROM cloud_customers WHERE phone = %s", phone)


async def create_customer(email=None, phone=None, first_name=None, last_name=None, pin=None, password=None,
                          loyalty=False, text_club=False):
    return await db.fetchrow(
        "INSERT INTO cloud_customers (email, phone, first_name, last_name, pin, password, loyalty_enrolled, text_club) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
        email.strip() if email else None, normalize_phone(phone), first_name, last_name, pin, password, loyalty, text_club)


async def find_or_create_by_email(email, **kw):
    c = await customer_by_email(email)
    if c is None and email:
        c = await create_customer(email=email, **kw)
    return c


async def link_card(card_row, customer):
    if card_row and customer and card_row.get("customer_id") != customer["customer_id"]:
        await db.execute("UPDATE cloud_cards SET customer_id = %s WHERE card_key = %s",
                         customer["customer_id"], card_row["card_key"])


async def add_points(customer, delta, reason, reference=None):
    if not customer or not delta:
        return
    await db.execute("UPDATE cloud_customers SET points = GREATEST(0, points + %s), updated_at = now() WHERE customer_id = %s",
                     delta, customer["customer_id"])
    await db.execute("INSERT INTO cloud_points_ledger (customer_id, delta, reason, reference) VALUES (%s, %s, %s, %s)",
                     customer["customer_id"], delta, reason, reference)


async def tier_counter(customer) -> int:
    if not customer:
        return 0
    return await db.fetchval(
        "SELECT count(*) FROM cloud_rentals r JOIN cloud_transactions t ON t.id = r.transaction_id "
        "WHERE t.customer_id = %s AND r.group_type = 1 AND r.status IN ('out', 'returned') "
        "AND r.rented_at >= date_trunc('year', now())", customer["customer_id"]) or 0


async def refresh_tier(customer):
    if not customer:
        return
    n = await tier_counter(customer)
    tier = next(name for threshold, name in TIERS if n >= threshold)
    await db.execute("UPDATE cloud_customers SET tier = %s WHERE customer_id = %s", tier, customer["customer_id"])


def temp_password() -> str:
    return secrets.token_urlsafe(6)

