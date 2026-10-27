"""Fraud Services: nothing to serve.

`system.config` category `Fraud` has `FraudServicesUrl` / `FraudServicesKey` (and the image's
`AwsFraudServicesUrl`), but no code in this source tree reads them: `Redbox.FraudServices.Proxy.
BarcodeTrackingService` only logs, the rental `FraudService` handles local HAL images and statistics, and
fraud reports go through KCS -> Inventory API `api/disc/discfraud` (routers/inventory.py).
The router is kept (empty) so app.py's optional import finds it.
"""
from fastapi import APIRouter

router = APIRouter()
