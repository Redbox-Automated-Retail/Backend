"""Data the KioskServicesCases harness needs, created in the running stack's database (run inside rb-cloudapi:
`python - < kioskservices_cases_setup.py`). Prints `HARNESS_RESERVATION=<n>`.

- an ops market for the harness kiosk (GetOpsMarket);
- a disc in its inventory (Return, SoftSync, DiscFraud, DiscAction);
- an offer for every customer with a known expiry (GetCustomerOffers: ExpirationDate);
- an online reservation held for the harness card (ResvAuth, PickupValidation, PickupAuthorize, PickupReconcile,
  ModResvPickup), as an online order would leave it.
"""
import asyncio
import datetime as dt
import json
import os

from redbox_backend import db
from redbox_backend.cloudapi import customers as cust

KIOSK = int(os.environ.get("HARNESS_KIOSK", "990010"))
CARD = {"CardId": f"HARNESS-CARD-{KIOSK}", "BIN": "424242", "LastFour": "4242", "ExpirationMonth": "12",
        "ExpirationYear": "39", "IsEncrypted": True}


async def main():
    await db.init()
    await db.execute("INSERT INTO kiosks (kiosk_id, market) VALUES (%s, 'Harness Market') ON CONFLICT (kiosk_id) DO UPDATE"
                     " SET market = COALESCE(kiosks.market, EXCLUDED.market)", KIOSK)
    await db.execute("INSERT INTO cloud_barcodes VALUES ('123456789', 11) ON CONFLICT DO NOTHING")
    await db.execute("INSERT INTO cloud_inventory (kiosk_id, barcode, deck, slot, status) VALUES"
                     " (%s, '123456789', 3, 12, 'In') ON CONFLICT (kiosk_id, barcode) DO UPDATE SET status = 'In'", KIOSK)
    card = await cust.upsert_card(CARD)
    ref = int(await db.fetchval("SELECT nextval('cloud_reference_seq')"))
    items = [{"ItemID": 1, "TitleID": 11, "ReservationType": 1, "Price": 1.85, "DiscountedPrice": 1.85,
              "ReservedBarcode": "123456789", "Reservable": True}]
    await db.execute("INSERT INTO cloud_reservations (reference_number, kiosk_id, card_key, status, authorize_at_pickup,"
                     " items, expires_at) VALUES (%s, %s, %s, 'reserved', true, %s, now() + interval '1 day')",
                     ref, KIOSK, card["card_key"], json.dumps(items))
    print(f"HARNESS_RESERVATION={ref}")
    # A new (all-customer) offer with a known expiry: GetCustomerOffers must hand it to the client as a .NET date.
    expires = (dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=9)).replace(hour=17, minute=30, second=0, microsecond=0)
    await db.execute("DELETE FROM cloud_customer_offers WHERE campaign_id = %s", KIOSK)
    await db.execute("INSERT INTO cloud_customer_offers (campaign_id, data, expires_at) VALUES (%s, %s, %s)", KIOSK,
                     json.dumps({"OfferCode": "HARNESS-EXPIRY", "DiscountType": 1, "DiscountAmount": 1.0}), expires)
    print(f"HARNESS_OFFER_EXPIRES={expires.strftime('%Y-%m-%dT%H:%M:%SZ')}")


asyncio.run(main())
