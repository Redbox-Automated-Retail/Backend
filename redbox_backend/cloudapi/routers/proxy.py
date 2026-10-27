"""Proxy Service stand-in (KCS `ProxyApi`, base `KioskClientProxyApi.ProxyApiUrl` or `AppSettings:DefaultProxyServiceUrl`;
also the Update Client's ping-failure report).

Reference data (pricing, subscription plans, campaigns, planograms, promo codes, products) is read from
the `cloud_*` tables seeded by 51_cloud_seed.sql. Campaigns and planograms answer **208 Already Reported**
when nothing changed since the time the kiosk sends back.
"""
import hashlib
import logging
import math
import urllib.parse

from fastapi import APIRouter, Request
from fastapi.responses import Response

from ... import db
from .. import customers as cust
from .. import media
from ..common import (HOSTNAME, base_url, body_json, check_key, ci, ci_list, error, kiosk_id, net_datetime, net_offset,
                      now, parse_dt, reply, standard, to_int, touch, trunc_seconds)

log = logging.getLogger("cloudapi.proxy")
router = APIRouter()
SOURCE = "proxy"
FILES = "/cloud-files"
KIOSK_FILTER = "(kiosk_ids IS NULL OR %s = ANY(kiosk_ids))"


async def _body(request):
    b = await body_json(request, {})
    return b if isinstance(b, dict) else {}


def _price(r, by_price=False):
    out = {
        "StartDate": net_datetime(r["start_date"]),
        "EndDate": net_datetime(r["end_date"]),
        "Attributes": r["attributes"],
        "LocationRank": r["location_rank"],
        "InitialNight": r["initial_night"],
        "InitialDays": r["initial_days"],
        "ExtraNight": r["extra_night"],
        "NonReturnDays": r["non_return_days"],
        "NonReturnPrice": r["non_return_price"],
        "ExpirePrice": r["expire_price"],
    }
    if by_price:
        out["PriceLevel"] = r["price_levels"]
    else:
        out["PriceLevelType"] = r["price_level_type"]
        out["PriceLevelTypeValue"] = r["price_level_value"]
    return out


# ------------------------------------------------------------------ pricing

@router.get("/api/pricing/kiosk/{kiosk}/rental/default")
async def pricing_rental_default(kiosk: int, request: Request):
    check_key(request, SOURCE)
    await touch(kiosk)
    rows = await db.fetch(f"SELECT * FROM cloud_pricing_default WHERE {KIOSK_FILTER} "
                          "ORDER BY price_level_type, start_date DESC", kiosk)
    groups = {}
    for r in rows:
        groups.setdefault(r["price_level_type"], []).append(_price(r))
    return reply({"FormatPricing": [{"Name": k, "Prices": v} for k, v in groups.items()]})


@router.get("/api/pricing/kiosk/{kiosk}/rentalbyprice")
async def pricing_rental_by_price(kiosk: int, request: Request):
    check_key(request, SOURCE)
    rows = await db.fetch(f"SELECT * FROM cloud_pricing_rental WHERE {KIOSK_FILTER} ORDER BY start_date DESC, id", kiosk)
    return reply({"RentalPricing": [_price(r, by_price=True) for r in rows]})


@router.get("/api/pricing/kiosk/{kiosk}/purchasebyprice")
async def pricing_purchase_by_price(kiosk: int, request: Request):
    check_key(request, SOURCE)
    rows = await db.fetch(f"SELECT * FROM cloud_pricing_purchase WHERE {KIOSK_FILTER} ORDER BY start_date DESC, id", kiosk)
    return reply({"PurchasePricing": [{"StartDate": net_datetime(r["start_date"]), "EndDate": net_datetime(r["end_date"]),
                                       "PurchasePrice": r["purchase_price"], "Titles": r["titles"] or []} for r in rows]})


# ------------------------------------------------------------------ subscriptions

async def _plans():
    return await db.fetch("SELECT * FROM cloud_subscription_plans WHERE active ORDER BY id")


@router.get("/api/subscription/subscriptionplans")
async def subscription_plans(request: Request):
    check_key(request, SOURCE)
    plans = [{"Id": p["id"], "Description": p["description"], "SubscriptionId": p["subscription_id"],
              "EffectiveStartDate": net_offset(p["effective_start"]), "EffectiveEndDate": net_offset(p["effective_end"]),
              "IsPromotional": p["is_promotional"], "PlanItems": p["plan_items"] or []} for p in await _plans()]
    return standard(SubscriptionPlans=plans)


@router.get("/api/subscription/redboxpluspromocampaigns")
async def redboxplus_promo_campaigns(request: Request):
    check_key(request, SOURCE)
    rows = await db.fetch("SELECT * FROM cloud_rbplus_promo_campaigns ORDER BY promo_campaign_id")
    return standard(RedboxPlusPromoCampaigns=[{"PromoCampaignId": r["promo_campaign_id"], "Tier": r["tier"]} for r in rows])


# ------------------------------------------------------------------ customer profile / loyalty / marketing

async def _login_payload(customer):
    subs = await db.fetch("SELECT * FROM cloud_customer_subscriptions WHERE customer_id = %s ORDER BY id",
                          customer["customer_id"])
    existing = [{"CustomerSubscriptionId": str(s["id"]), "SubscriptionId": s["subscription_id"],
                 "SubscriptionPlanId": s["subscription_plan_id"], "CampaignId": None,
                 "EndDate": net_datetime(s["end_date"]), "status": s["status"], "BenefitUsages": []} for s in subs]
    active_ids = {s["subscription_plan_id"] for s in subs if s["status"] == 0}
    eligible = [p["id"] for p in await _plans() if p["id"] not in active_ids] if not active_ids else []
    promo_rows = await db.fetch("SELECT * FROM cloud_customer_offers WHERE customer_id = %s AND redeemed_at IS NULL "
                                "AND (expires_at IS NULL OR expires_at > now()) ORDER BY offer_id", customer["customer_id"])
    promo_codes = [{"Code": (r["data"] or {}).get("PromotionCode"), "CampaignId": r["campaign_id"],
                    "CampaignName": (r["data"] or {}).get("CampaignName"), "PromotionUsage": "SingleUse",
                    "RedemptionType": "Kiosk", "ExpirationDate": net_datetime(r["expires_at"]) or "2099-12-31T00:00:00"}
                   for r in promo_rows if (r["data"] or {}).get("PromotionCode")]
    return {
        "Customer": {"CPN": customer["cpn"], "FirstName": customer["first_name"], "LoginEmail": customer["email"],
                     "MobilePhoneNumber": customer["phone"], "IsMobilePhoneNumberVerified": bool(customer["phone"]),
                     "IsEmailVerified": customer["email_verified"], "PromptForPerks": not customer["loyalty_enrolled"],
                     "PromptForEarlyId": False, "TextClubEnabled": customer["text_club"]},
        "Loyalty": {"LoyaltySystemOnline": True, "IsEnrolled": customer["loyalty_enrolled"],
                    "PointBalance": customer["points"], "CurrentTier": customer["tier"],
                    "CurrentTierCounter": await cust.tier_counter(customer), "PointsExpiring": 0,
                    "PointsExpirationDate": None},
        "Recommendations": None,
        "AcceptedOffers": [],
        "PromoCodes": promo_codes,
        "OptIns": customer["opt_ins"] or [],
        "Subscriptions": {"EligibleSubscriptionPlanIds": eligible, "ExistingSubscriptions": existing},
    }


@router.post("/api/customerprofile/login")
async def customer_login(request: Request):
    check_key(request, SOURCE)
    body = await _body(request)
    kiosk = kiosk_id(request, body)
    await touch(kiosk)
    message_id = ci(body, "MessageId")
    email, password = ci(body, "EmailAddress"), ci(body, "Password")
    phone, pin = ci(body, "PhoneNumber"), ci(body, "Pin")
    cpn = ci(body, "CustomerProfileNumber")
    customer, secret, stored = None, None, None
    if email:
        customer = await cust.customer_by_email(email)
        secret, stored = password, (customer or {}).get("password")
    elif phone:
        customer = await cust.customer_by_phone(phone)
        secret, stored = pin, (customer or {}).get("pin")
    elif cpn:
        customer = await cust.customer_by_cpn(cpn)  # Perks Pass (VAS) login: the CPN is the credential
    errs = []
    if customer is None:
        errs.append(error("(KLI003)", "Account not found"))
    elif customer["blocked"]:
        errs.append(error("(KLI001)", "Account locked"))
    elif stored is not None and secret is not None and not ci(body, "IsEncrypted") and str(secret) != str(stored):
        errs.append(error("(KLI002)", "Invalid password or PIN"))
    resp = {"MessageId": message_id, "Success": not errs, "Errors": errs, "HostName": HOSTNAME, "StatusCode": 200}
    if not errs:
        resp["Response"] = await _login_payload(customer)
    await db.record_event(kiosk, SOURCE, "login", {"Success": not errs, "Errors": errs,
                                                   "Cpn": customer["cpn"] if customer else None})
    return reply(resp)


def _loyalty_items(body, points_fn):
    items = []
    for g in ci_list(ci(body, "ShoppingCart", default={}), "Groups"):
        if to_int(ci(g, "GroupType"), 0) not in (1, 2):
            continue
        for item in ci_list(g, "Items"):
            pid = to_int(ci(item, "ProductId"))
            if pid:
                items.append({"ProductId": pid, "Points": points_fn(item)})
    return items


async def _loyalty_estimate(request, points_fn, kind):
    check_key(request, SOURCE)
    body = await _body(request)
    kiosk = kiosk_id(request, body)
    await touch(kiosk)
    cpn = ci(body, "CustomerProfileNumber")
    if cpn and await cust.customer_by_cpn(cpn) is None:
        return reply({"Items": [], "Error": "Account not found"}, 404)
    return reply({"Items": _loyalty_items(body, points_fn), "Error": None})


@router.post("/api/loyalty/estimateaccrual")
async def loyalty_estimate_accrual(request: Request):
    return await _loyalty_estimate(request, cust.accrual_points, "accrual")


@router.post("/api/loyalty/estimateredemption")
async def loyalty_estimate_redemption(request: Request):
    return await _loyalty_estimate(request, cust.redemption_points, "redemption")


@router.post("/api/loyalty/updateaccount")
async def loyalty_update_account(request: Request):
    """Perks sign-up / update at the kiosk: creates the account when needed (temporary password returned)."""
    check_key(request, SOURCE)
    body = await _body(request)
    kiosk = kiosk_id(request, body)
    await touch(kiosk)
    base = {"MessageId": ci(body, "MessageId"), "KioskId": kiosk}
    email = ci(body, "Email")
    phone = cust.normalize_phone(ci(body, "MobilePhoneNumber"))
    customer = await cust.customer_by_cpn(ci(body, "CustomerProfileNumber"))
    by_email = await cust.customer_by_email(email)
    if customer and by_email and by_email["customer_id"] != customer["customer_id"]:
        return reply(base | {"Error": "Email address is used by another account"}, 409)
    customer = customer or by_email
    if phone:
        other = await cust.customer_by_phone(phone)
        if other and (customer is None or other["customer_id"] != customer["customer_id"]):
            return reply(base | {"Error": "Mobile phone number is used by another account"}, 409)
    temp = None
    if customer is None:
        if not email:
            return reply(base | {"Error": "Email is required"}, 400)
        temp = cust.temp_password()
        customer = await cust.create_customer(email=email, phone=phone, pin=ci(body, "Pin"), password=temp,
                                              loyalty=True, text_club=bool(ci(body, "TextClubOptin")))
    else:
        if customer["password"] is None:
            temp = cust.temp_password()
        customer = await db.fetchrow(
            "UPDATE cloud_customers SET email = COALESCE(%s, email), phone = COALESCE(%s, phone), pin = COALESCE(%s, pin), "
            "password = COALESCE(password, %s), loyalty_enrolled = true, text_club = text_club OR %s, updated_at = now() "
            "WHERE customer_id = %s RETURNING *", email, phone, ci(body, "Pin"), temp,
            bool(ci(body, "TextClubOptin")), customer["customer_id"])
    await db.record_event(kiosk, SOURCE, "updateaccount", {"Cpn": customer["cpn"], "New": temp is not None})
    return reply(base | {"CustomerProfileNumber": customer["cpn"], "MobilePhoneNumber": customer["phone"],
                         "TempPassword": temp, "Error": None})


@router.post("/api/marketing/emailconfirmation")
async def marketing_email_confirmation(request: Request):
    check_key(request, SOURCE)
    body = await _body(request)
    kiosk = kiosk_id(request, body)
    await touch(kiosk)
    email = ci(body, "Email")
    if not email:
        return standard(False, [error("(V001)", "Email is required")], 400, MessageId=ci(body, "MessageId"))
    customer = await cust.customer_by_email(email)
    if customer is None:
        customer = await cust.create_customer(email=email)
        status = 5  # NewCustomer
    else:
        status = 1 if customer["loyalty_enrolled"] else 3  # SubscribedConfirmed / NotSubscribedConfirmed
    account = ci(body, "AccountNumber")
    if account:
        await db.execute("UPDATE cloud_cards SET customer_id = COALESCE(customer_id, %s) WHERE account_number = %s",
                         customer["customer_id"], str(account))
    await db.record_event(kiosk, SOURCE, "emailconfirmation", {"Cpn": customer["cpn"], "ConfirmationStatus": status})
    return standard(MessageId=ci(body, "MessageId"), ConfirmationStatus=status, CustomerProfileNumber=customer["cpn"],
                    MobilePhoneNumber=customer["phone"], PromptForPerks=not customer["loyalty_enrolled"],
                    PromptForEarlyId=False, TextClubEnabled=customer["text_club"], EmailVerified=customer["email_verified"])


@router.post("/api/marketing/kioskoptin")
async def marketing_kiosk_optin(request: Request):
    check_key(request, SOURCE)
    body = await _body(request)
    kiosk = kiosk_id(request, body)
    await touch(kiosk)
    for item in ci_list(body, "OptInItems"):
        data = ci(item, "OptInMethodData")
        customer = await cust.customer_by_email(data) if data and "@" in str(data) else await cust.customer_by_phone(data)
        if customer:
            entry = {"ItemId": ci(item, "ItemId"), "Type": ci(item, "Item_Type"), "Campaign": ci(item, "CampaignName")}
            await db.execute("UPDATE cloud_customers SET opt_ins = opt_ins || %s WHERE customer_id = %s",
                             db.jsonb([entry]), customer["customer_id"])
    await db.record_event(kiosk, SOURCE, "kioskoptin", body)
    return standard(MessageId=ci(body, "MessageId"))


# ------------------------------------------------------------------ promo codes

@router.post("/api/promotion/promocodevalidation")
async def promo_code_validation(request: Request):
    """200 with the promotion for a valid code; 422 with Error (D001/D002/D003/D006) otherwise."""
    check_key(request, SOURCE)
    body = await _body(request)
    kiosk = kiosk_id(request, body)
    await touch(kiosk)
    code = str(ci(body, "Code", default="") or "").strip().upper()
    base = {"MessageId": ci(body, "MessageId"), "KioskId": kiosk}
    row = await db.fetchrow("SELECT * FROM cloud_promo_codes WHERE code = %s", code) if code else None
    t = now()
    err = None
    if row is None or not row["active"] or (row["valid_from"] and row["valid_from"] > t) or (row["valid_to"] and row["valid_to"] < t):
        err = "(D001)"
    elif row["kiosk_ids"] and kiosk not in row["kiosk_ids"]:
        err = "(D002)"
    elif row["max_uses"] is not None and row["uses"] >= row["max_uses"]:
        err = "(D003)"
    elif row["on_demand"]:
        err = "(D006)"
    await db.record_event(kiosk, SOURCE, "promocodevalidation", {"Code": code, "Error": err})
    if err:
        return reply(base | {"Error": err, "Amount": 0, "AllowFullDiscount": False}, 422)
    data = {"Amount": 0, "AllowFullDiscount": False}
    data.update(row["data"] or {})
    return reply(base | data | {"Error": None})


# ------------------------------------------------------------------ planograms

def _planogram_url(request, r):
    return f"{base_url(request)}{FILES}/planograms/{r['id']}/{urllib.parse.quote(r['filename'])}"


@router.get("/api/planogram/allcurrent")
async def planograms_all_current(request: Request, lastReportedTime: str | None = None):
    check_key(request, SOURCE)
    kiosk = kiosk_id(request)
    await touch(kiosk)
    rows = await db.fetch(f"SELECT id, name, start_date, file_type, filename, updated_at FROM cloud_planograms "
                          f"WHERE {KIOSK_FILTER} ORDER BY name, start_date", kiosk or 0)
    if not rows:  # KCS fails on an empty 200 list (Max() of nothing), so "nothing" is reported as unchanged
        return reply({"Planograms": []}, 208)
    changed = await db.fetchval("SELECT changed_at FROM cloud_changes WHERE area = 'planograms'")
    newest = max(rows, key=lambda r: r["updated_at"])
    mark = max(newest["updated_at"], changed or newest["updated_at"])
    last = parse_dt(lastReportedTime)
    if last is not None and last >= trunc_seconds(mark):
        return reply({"Planograms": []}, 208)
    out = []
    for r in rows:
        stamp = mark if r is newest else r["updated_at"]
        out.append({"Name": r["name"], "StartDate": net_datetime(r["start_date"]), "FileType": r["file_type"],
                    "Filename": r["filename"], "LastUpdateUtc": net_datetime(stamp), "PresignedUrl": _planogram_url(request, r)})
    return reply({"Planograms": out})


@router.get(FILES + "/planograms/{pid}/{filename}")
async def planogram_file(pid: int, filename: str):
    row = await db.fetchrow("SELECT name, filename, content FROM cloud_planograms WHERE id = %s", pid)
    if row is None:
        return Response(status_code=404)
    content = bytes(row["content"]) if row["content"] is not None else media.xps(row["name"])
    return Response(content, media_type="application/vnd.ms-xpsdocument")


# ------------------------------------------------------------------ campaigns

def _asset_url(request, asset):
    name = asset.get("Name") or f"{asset.get('Id')}.png"
    return f"{base_url(request)}{FILES}/assets/{asset.get('Id')}/{urllib.parse.quote(str(name))}"


@router.post("/api/cms/campaign/kiosk/{kiosk}")
async def campaigns_for_kiosk(kiosk: int, request: Request):
    check_key(request, SOURCE)
    await touch(kiosk)
    body = await _body(request)
    changed = await db.fetchval("SELECT changed_at FROM cloud_changes WHERE area = 'campaigns'")
    if changed is None:
        changed = await db.fetchval("INSERT INTO cloud_changes (area) VALUES ('campaigns') ON CONFLICT (area) "
                                    "DO UPDATE SET area = EXCLUDED.area RETURNING changed_at")
    mark = trunc_seconds(changed)
    last = parse_dt(ci(body, "LastSyncTime"))
    if last is not None and last >= mark:
        return reply({}, 208)
    rows = await db.fetch(f"SELECT * FROM cloud_campaigns WHERE active AND {KIOSK_FILTER} "
                          "AND (end_date IS NULL OR end_date > now()) ORDER BY rank DESC, campaign_id", kiosk)
    in_carts, screens = [], []
    for r in rows:
        base = {"CampaignId": r["campaign_id"], "StartDate": net_datetime(r["start_date"]),
                "EndDate": net_datetime(r["end_date"]), "CampaignType": r["campaign_type"], "Rank": r["rank"]}
        data = dict(r["data"] or {})
        if r["campaign_type"] == "InCart":
            data.setdefault("CampaignInCartId", r["campaign_id"])
            data.setdefault("ExcludeTitles", [])
            in_carts.append(base | data)
        else:
            controls = []
            for c in data.get("Controls") or []:
                c = dict(c)
                if isinstance(c.get("Asset"), dict):
                    c["Asset"] = dict(c["Asset"], Url=_asset_url(request, c["Asset"]))
                controls.append(c)
            screens.append(base | {"Controls": controls})
    return reply({"LastSyncTime": net_datetime(mark), "InCarts": in_carts, "StartScreens": screens})


@router.get(FILES + "/assets/{asset_id}/{filename}")
async def asset_file(asset_id: str):
    row = await db.fetchrow("SELECT * FROM cloud_assets WHERE asset_id = %s", asset_id)
    if row is None:
        return Response(status_code=404)
    if row["content"] is not None:
        return Response(bytes(row["content"]), media_type=row["mime_type"])
    return Response(media.png(row["width"], row["height"], row["color"]), media_type="image/png")


# ------------------------------------------------------------------ inventory / products / kiosks

@router.post("/api/inventory/kiosktitlescount")
async def kiosk_titles_count(request: Request):
    """Titles in stock (status In) per kiosk, from the snapshots and the barcode -> title map."""
    check_key(request, SOURCE)
    body = await _body(request)
    kiosk = kiosk_id(request, body)
    await touch(kiosk)
    kiosks = [to_int(k) for k in ci_list(body, "KioskIds") if to_int(k)]
    titles = [to_int(t) for t in ci_list(body, "TitleIds") if to_int(t)]
    result = []
    for k in kiosks:
        sql = ("SELECT b.product_id, count(*)::int AS n FROM cloud_inventory i JOIN cloud_barcodes b ON b.barcode = i.barcode "
               "WHERE i.kiosk_id = %s AND lower(i.status) = 'in'")
        args = [k]
        if titles:
            sql += " AND b.product_id = ANY(%s)"
            args.append(titles)
        counts = {str(r["product_id"]): r["n"] for r in await db.fetch(sql + " GROUP BY b.product_id", *args)}
        for t in titles:
            counts.setdefault(str(t), 0)
        result.append({"KioskId": k, "TitlesCount": counts})
    return reply({"MessageId": ci(body, "MessageId"), "KioskTitlesCount": result})


@router.post("/api/product/bytitleids")
async def products_by_title_ids(request: Request):
    check_key(request, SOURCE)
    body = await _body(request)
    ids = [to_int(t) for t in ci_list(body, "TitleIds") if to_int(t)]
    rows = await db.fetch("SELECT * FROM cloud_products WHERE title_id = ANY(%s)", ids) if ids else []
    return reply({"Products": [{"TitleId": r["title_id"],
                                "ProductGroup": {"ProductGroupId": r["product_group_id"], "ProductIds": r["product_ids"] or {}},
                                "ProductPage": r["product_page"]} for r in rows]})


@router.post("/api/kiosk/authenticate")
async def kiosk_authenticate(request: Request):
    """Field-staff login. With no cloud_staff_users rows, any non-empty user name and password is accepted."""
    check_key(request, SOURCE)
    body = await _body(request)
    kiosk = kiosk_id(request, body)
    await touch(kiosk)
    user, password = ci(body, "Username"), ci(body, "Password")
    ok = False
    if user and (password or ci(body, "UseNtAuthentication")):
        configured = await db.fetchval("SELECT count(*) FROM cloud_staff_users")
        if not configured:
            ok = True
        else:
            digest = hashlib.sha256(str(password or "").encode()).hexdigest()
            ok = bool(await db.fetchval("SELECT 1 FROM cloud_staff_users WHERE lower(username) = lower(%s) AND active "
                                        "AND password_sha256 = %s", user, digest))
    await db.record_event(kiosk, SOURCE, "authenticate", {"Username": user, "Success": ok})
    if not ok:
        return standard(False, [error("(KA001)", "Invalid user name or password")], 401)
    return standard()


def _miles(a_lat, a_lon, b_lat, b_lon):
    r = 3958.8
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


@router.get("/api/kiosk/{kiosk}/nearby")
async def kiosk_nearby(kiosk: int, request: Request):
    check_key(request, SOURCE)
    await touch(kiosk)
    me = await db.fetchrow("SELECT * FROM cloud_kiosk_locations WHERE kiosk_id = %s", kiosk)
    out = []
    if me and me["latitude"] is not None and me["longitude"] is not None:
        for r in await db.fetch("SELECT * FROM cloud_kiosk_locations WHERE kiosk_id <> %s AND latitude IS NOT NULL "
                                "AND longitude IS NOT NULL", kiosk):
            d = _miles(me["latitude"], me["longitude"], r["latitude"], r["longitude"])
            if d <= 25:
                out.append({"KioskId": r["kiosk_id"], "IsDual": me["dual_kiosk_id"] == r["kiosk_id"],
                            "Label": r["label"], "LocationName": r["location_name"], "LocationType": r["location_type"],
                            "Address": ", ".join(x for x in (r["address"], r["city"], r["state"], r["zip"]) if x),
                            "DistanceMiles": round(d, 2)})
        out.sort(key=lambda x: x["DistanceMiles"])
    return reply({"MessageId": request.headers.get("x-redbox-activityid"), "Kiosks": out[:10]})


@router.post("/api/kiosk/{kiosk}/pingfailure")
async def kiosk_ping_failure(kiosk: int, request: Request):
    """Update Client ReportFailedPingsJob: the kiosk could not ping over MQTT for over an hour."""
    check_key(request, SOURCE)
    body = await body_json(request, {})
    await db.record_event(kiosk, "updateclient", "pingfailure", body)
    await touch(kiosk)
    return standard()


# ------------------------------------------------------------------ installer

@router.get("/api/installer/getpendingstates")
async def installer_pending_states(request: Request):
    check_key(request, SOURCE)
    rows = await db.fetch("SELECT DISTINCT state_id, state_name FROM cloud_installer_kiosks WHERE pending ORDER BY state_name")
    return standard(States=[{"Id": r["state_id"], "Description": r["state_name"]} for r in rows])


@router.get("/api/installer/getpendingbanners")
async def installer_pending_banners(request: Request, stateid: int = 0):
    check_key(request, SOURCE)
    rows = await db.fetch("SELECT DISTINCT banner_id, banner_name FROM cloud_installer_kiosks WHERE pending AND state_id = %s "
                          "ORDER BY banner_name", stateid)
    return standard(Banners=[{"Id": r["banner_id"], "Name": r["banner_name"]} for r in rows])


@router.get("/api/installer/getpendingkiosks")
async def installer_pending_kiosks(request: Request, stateid: int = 0, bannerid: int = 0):
    check_key(request, SOURCE)
    rows = await db.fetch("SELECT * FROM cloud_installer_kiosks WHERE pending AND state_id = %s AND banner_id = %s ORDER BY id",
                          stateid, bannerid)
    return standard(PendingKiosks=[{"Id": r["id"], "Address": r["address"], "City": r["city"], "State": r["state_name"],
                                    "ZipCode": r["zip"], "DueTime": r["due_time"], "MarketName": r["market_name"],
                                    "KaseyaMarketName": r["kaseya_market_name"]} for r in rows])


@router.get("/api/installer/getopsmarket")
async def installer_ops_market(request: Request, kioskid: int = 0):
    check_key(request, SOURCE)
    loc = await db.fetchrow("SELECT ops_market, kaseya_server FROM cloud_kiosk_locations WHERE kiosk_id = %s", kioskid)
    market = (loc or {}).get("ops_market") or await db.fetchval("SELECT market FROM kiosks WHERE kiosk_id = %s", kioskid)
    if not market:
        return standard(False, [error("(I001)", f"No ops market known for kiosk {kioskid}")], 404)
    return standard(OpsMarket=market, KaseyaServer=(loc or {}).get("kaseya_server"))
