"""In-process client for the cloud API app, used by tests/test_cloudapi_*.py.

Needs a PostgreSQL reachable at DATABASE_URL (e.g. postgresql://redbox:redbox@127.0.0.1:55432/redbox; use
127.0.0.1 rather than localhost on Windows). Runs one event loop for the whole session (a
SelectorEventLoop on Windows, which psycopg's async mode requires).
"""
import asyncio
import gzip
import json
import os
import random
import selectors
import sys
import uuid

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

_client = None


def database_available():
    url = os.environ.get("DATABASE_URL")
    if not url:
        return False
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=3):
            return True
    except Exception:
        return False


class CloudClient:
    def __init__(self):
        from redbox_backend import db
        from redbox_backend.cloudapi.app import create_app
        if sys.platform == "win32":
            self.loop = asyncio.SelectorEventLoop(selectors.SelectSelector())
        else:
            self.loop = asyncio.new_event_loop()
        self.db = db
        self.loop.run_until_complete(db.init())
        self.app = create_app()
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://cloud.test")

    def run(self, coro):
        return self.loop.run_until_complete(coro)

    def request(self, method, path, kiosk=None, json_body=None, gzip_body=False, headers=None, **kw):
        h = {"x-redbox-activityid": str(uuid.uuid4()), "x-api-key": "test-key"}
        if kiosk:
            h["x-redbox-kioskid"] = str(kiosk)
        h.update(headers or {})
        content = None
        if json_body is not None:
            content = json.dumps(json_body).encode()
            h["content-type"] = "application/json; charset=utf-8"
            if gzip_body:
                content = gzip.compress(content)
                h["content-encoding"] = "gzip"
        return self.run(self.http.request(method, path, content=content, headers=h, **kw))

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, json_body=body if body is not None else {}, **kw)

    def sql(self, query, *args):
        return self.run(self.db.fetch(query, *args))

    def sqlval(self, query, *args):
        return self.run(self.db.fetchval(query, *args))


def client():
    global _client
    if _client is None:
        _client = CloudClient()
    return _client


def new_kiosk():
    return random.randint(900000, 999999)


def new_barcode():
    return str(random.randint(100000000, 999999999))


def card(last_four="4242", **kw):
    c = {"FirstName": "Test", "LastName": "Customer", "BIN": "424242", "LastFour": last_four,
         "ExpirationMonth": "12", "ExpirationYear": "39", "CardType": 1, "IsEncrypted": True,
         "CardSourceType": 2, "CardId": kw.pop("card_id", str(uuid.uuid4()))}
    c.update(kw)
    return c


def rental_item(product_id, barcode, vend_status=0, product_type="DVD", initial=1.85):
    return {"ProductId": product_id, "Barcode": barcode, "VendStatus": vend_status, "ProductFamily": "Movie",
            "ProductType": product_type, "ProductName": f"Title {product_id}",
            "Pricing": {"InitialNight": initial, "ExtraNight": initial, "NonReturn": 29.6, "Purchase": 0,
                        "Expiration": 0, "NonReturnDays": 16, "PriceSetId": 1, "InitialDays": 1}}


def cart(items, group_type=1, total=None, discounts=None):
    total = total if total is not None else round(sum(i["Pricing"]["InitialNight"] for i in items), 2)
    return {"Groups": [{"GroupType": group_type, "Items": items,
                        "Totals": {"TaxRate": 0, "Subtotal": total, "DiscountedSubtotal": total, "TaxAmount": 0,
                                   "GrandTotal": total}}],
            "Discounts": discounts or []}
