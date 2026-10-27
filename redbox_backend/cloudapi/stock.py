"""Per-kiosk disc inventory kept from snapshots, vends and returns."""
from .. import db


async def learn_barcode(barcode, product_id):
    if barcode and product_id:
        await db.execute("INSERT INTO cloud_barcodes (barcode, product_id) VALUES (%s, %s) "
                         "ON CONFLICT (barcode) DO UPDATE SET product_id = EXCLUDED.product_id, updated_at = now()",
                         str(barcode), int(product_id))


async def disc_left_kiosk(kiosk_id, barcode, status="Rented"):
    """A disc was vended (rented or sold): it is no longer in a slot."""
    if not barcode:
        return
    await db.execute("UPDATE cloud_inventory SET status = %s, deck = NULL, slot = NULL, updated_at = now() "
                     "WHERE barcode = %s", status, str(barcode))
    if kiosk_id:
        await db.execute("INSERT INTO cloud_inventory (kiosk_id, barcode, status) VALUES (%s, %s, %s) "
                         "ON CONFLICT (kiosk_id, barcode) DO NOTHING", kiosk_id, str(barcode), status)


async def disc_returned(kiosk_id, barcode, deck, slot):
    """A disc was returned into `kiosk_id`: it is there now and nowhere else."""
    if not barcode:
        return
    if kiosk_id:
        await db.execute("DELETE FROM cloud_inventory WHERE barcode = %s AND kiosk_id <> %s", str(barcode), kiosk_id)
        await db.execute("INSERT INTO cloud_inventory (kiosk_id, barcode, deck, slot, status) VALUES (%s, %s, %s, %s, 'In') "
                         "ON CONFLICT (kiosk_id, barcode) DO UPDATE SET deck = EXCLUDED.deck, slot = EXCLUDED.slot, "
                         "status = 'In', updated_at = now()", kiosk_id, str(barcode), deck, slot)
