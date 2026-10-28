# Kiosk cloud APIs (self-hosted)

One FastAPI app (`python -m redbox_backend cloudapi`, `redbox_backend/cloudapi/app.py`) serves every HTTPS API the
kiosk software calls besides the update service, MQTT and the IoT certificate service. The kiosk reaches them
through different base URLs (Proxy Service, Transaction API, Inventory API, Kiosk Data Service, Ad Server, Product
Reels, Bluefin); their paths do not collide, so all base URLs can point at the same host.

Server behaviour is inferred from the decompiled clients (`src/KioskClient/KioskClientService.API`, Device Service,
Kiosk Engine, Update Client) and the protocol docs in `docs/systems/`. Everything is stored in PostgreSQL (tables
`cloud_*`, `backend/redbox_backend/schema/50_cloud.sql`; defaults seeded once by `51_cloud_seed.sql`).

Code: `cloudapi/routers/{transaction,inventory,proxy,kioskdata,ads,reels,bluefin,fraud,tickets}.py` (tickets: the 2014
SOAP Ticket Services, §7), helpers
`cloudapi/{common,customers,stock,media}.py`.

## 1. Pointing a kiosk at this server

`<base>` is this server, e.g. `https://cloud.example.test` (placeholder).

**`%ProgramData%\Redbox\Configuration\configuration.json`** (written by the Update Client from the configuration
service; read by KCS, `docs/systems/kiosk-client.md` §5.2, §8.2):

| Category | Setting | Value |
|---|---|---|
| `KioskClientDefault` | `TransactionApiUrl` | `<base>` |
| | `TransactionApiKey` | any (sent as `x-api-key`) |
| | `InventoryApiUrl` | `<base>` |
| | `InventoryApiKey` | any |
| | `KioskDataServiceUrl` | `<base>` |
| | `KioskDataServiceAPIKey` | any (sent in `KioskDataServiceAuthHeader`, default `Authorization`) |
| | `ProductReelsApiUrl` | `<base>` |
| | `ProductGroupSortOrderType` | e.g. `1` (0 disables the sort-order download) |
| `KioskClientProxyApi` | `ProxyApiUrl` | `<base>` |
| | `ProxyApiKey` | any |
| `KioskClientAds` | `AdServerUrl` | `<base>/api` or `<base>` (both work, see §3.5) |
| | `AdServerAPIKey` | any |
| `KioskClientSubscription` | `SubscriptionPlansActive` | `true` to use the seeded Redbox+ plans |
| `Campaigns` | `JobActive`, `StartScreensActive`, `InCartsActive` | `true` |
| `MarketingReturn` | `ReturnVisitPromoOfferEnabled` | `true` to show the return-visit offer |

KCS `bin/appsettings.json` `AppSettings:DefaultProxyServiceUrl` = `<base>` (used when `ProxyApiUrl` is empty; the
Update Client's ping-failure report uses `ProxyApiUrl` or this value too).

**Kiosk Engine `system.config`** (rental `IConfiguration`):

| Category | Setting | Value |
|---|---|---|
| `General` | `KioskDataServicesUrl` | `<base>` (`api/KioskData/AddKioskSessionJson`) |
| | `KioskDataServicesKey` | any (sent as `Authorization: Bearer`) |
| `DeviceService` | `BluefinServiceUrl` | `<base>` or `<base>/bluefinservice` |
| | `BluefinServiceApiKey` | any |
| `Fraud` | `FraudServicesUrl`, `AwsFraudServicesUrl` | not used by any code (§3.8) |

**Device Service `Data\KioskData.json`**: `{"KioskId": <id>, "ApiKey": "<any>", "ApiUrl": "<base>"}` (read once at
start-up, `docs/systems/device-service.md` §6.3).

KCS takes the kiosk id from `HKLM\SOFTWARE\Redbox\REDS\Kiosk Engine\Store\ID`. KCS runs on the x86 runtime, so
on 64-bit Windows it reads the 32-bit view (`HKLM\SOFTWARE\WOW6432Node\...`). With no id there, KCS refuses to
refresh pricing and campaigns ("KioskId must be > 0") and sends `x-redbox-kioskid: 0`. The server then takes the
kiosk id from the body.

### Server settings (environment)

| Variable | Default | Meaning |
|---|---|---|
| `PAYMENT_MODE` | `approve` | `approve`, `decline` or `rules` (§3.1) |
| `CLOUD_API_ENFORCE_KEYS` | off | reject requests with a missing or unknown key (401) |
| `CLOUD_API_KEYS` | empty | comma-separated accepted keys (`x-api-key`, `Authorization`, `Authorization: Bearer`) |
| `CLOUD_PUBLIC_BASE_URL` | from the request | base of URLs the server hands out (planogram, asset and ad files) |
| `CLOUD_API_HOSTNAME` | machine name | `HostName` in replies |
| `CLOUD_API_HTTP_PORT` | `80` | extra plain-HTTP listener of the same app (Ticket Services, §7); `0` = off |

Missing or unknown keys are always logged. They are only refused when enforcement is on.

## 2. Endpoints

Legend: **I** = implemented with stored state; **S** = accepted and logged only (no behaviour behind it).
"KCS" = `KioskClientService.API`; "DS" = Device Service; "KE" = Kiosk Engine; "UC" = Update Client.

### Transaction API (`routers/transaction.py`)

| Method & path | Caller | | Behaviour |
|---|---|---|---|
| `POST /api/transaction/authorize` | KCS `TransactionApiClient.Authorize` | I | Card decision (§3.1). Stores a `cloud_transactions` row and returns a new `ReferenceNumber`. Fills customer fields (`HasProfile`, `CustomerProfileNumber`, names, `Email`) when the card is linked to a customer or the request carries a CPN. `AccountNumber` is a stable per-card id. `KioskTransactionSuccess` / `SubscriptionTransactionSuccess` follow KCS's own rules (`TransactionService.FillTestDataAuthorizeResponse`). |
| `POST /api/transaction/giftcardauthorize` | KCS | I | Like authorize, plus `GiftCardSummary`. An unknown gift card is created with a 25.00 balance. Declined `(A006)` when the cart total exceeds the balance. Reconcile debits it. |
| `POST /api/transaction/pickupauthorize` | KCS | I | Authorize plus a reservation check: cancelled or expired reservations give `(A026)`, picked-up ones `(A025)`. Unknown references are recorded as reserved. `ReferenceNumber` in the reply is the reservation reference. |
| `POST /api/transaction/reservationauthorize` | KCS (ROP) | I | Same as pickup authorize (`ReferenceNumber` is a string in the request). |
| `POST /api/transaction/reconcile` | KCS (replayable) | I | Finds the transaction by `ReferenceNumber`, or creates an `offline`/`unmatched` one. Writes one `cloud_rentals` row per line item: VendStatus 0 gives `out` (rental) or `purchased`, anything else `not_vended`. Group 3 gives `subscription` and a customer subscription row. Vended discs leave the kiosk inventory (`Rented`/`Sold`). Learns barcode to title. Records promo-code redemptions and Perks point redemptions, marks redeemed customer offers, accrues points (§3.3), debits gift cards, links card and e-mail to a customer (creating the customer from `Email`), and blocks the card when `Blocklisted`. Idempotent per `MessageId` (a replay gets the stored answer). A second reconcile of the same transaction is not applied twice. Reply `{Success, Errors, HostName, StatusCode, CustomerProfileNumber}`. |
| `POST /api/transaction/reservationPickup` | KCS (replayable) | I | Reconcile, plus the reservation becomes `picked_up`. |
| `POST /api/transaction/return` | KCS (replayable) | I | Closes the latest open rental of the barcode. Computes extra nights (1 h grace, capped at the non-return price) into `extra_nights`/`extra_charge`. Moves the disc into the return kiosk at Deck/Slot (status `In`) and removes it from every other kiosk. Logs `cloud_returns`. Unknown barcodes are accepted (discs rented before this backend existed). A missing barcode gives 400 `(RTN006)` (KCS does not queue it). Idempotent per `MessageId`. |
| `GET /api/transaction/return/{barcode}/promocheck` | KCS | I | `{Promotion, IsLastDisc, Discs[{Barcode, IsReturned}], Error}` for the transaction of the barcode's latest rental. The first check issues one single-use return-visit code per transaction (`cloud_return_promo`, §3.4). 404 with `Error` if the barcode was never rented here. |
| `POST /api/reservation/cancel` | KCS (replayable) | I | Reservation becomes `cancelled` with `ReasonCode`, created if unknown. A picked-up reservation is not changed. |
| `POST /api/reservation/expire` | KCS (replayable) | I | Reservation becomes `expired`. |
| `POST /api/reservation/validatepickup` | KCS | I | Each reference is valid when it is `reserved` and not past `expires_at`, else cancelled. Unknown references are recorded and valid. A blocked card or customer cancels all with `(A002)`. Returns `CustomerProfileNumber`, `AccountNumber`, `AllowRop=true`. |
| `POST /api/customeroffers` | KCS (after-swipe offers) | I | `StoredOffers` = unredeemed, unexpired `cloud_customer_offers` rows of the card's customer. `NewOffers` = rows with no customer. Seeded empty. |

### Inventory API (`routers/inventory.py`)

| Method & path | Caller | | Behaviour |
|---|---|---|---|
| `POST /api/kiosk/sync` | KCS (gzip body) | I | Replaces the kiosk's snapshot in `cloud_inventory` (barcode, deck, slot, status as sent) in one transaction. Logs `cloud_inventory_syncs`. A missing kiosk id gives 400. |
| `POST /api/disc/discaction` | KCS | S | Event `inventory/discaction` (the action dictionary shape is free-form). |
| `POST /api/disc/discfraud` | KCS | S | Event `inventory/discfraud`. |
| `POST /api/disc/updatemerchandizingstatus` | KCS (replayable) | I | Stores `MerchandStatus`/`ThinReasonCode`/`ThinDateUTC` per barcode. Reply `{Data: [failed], error, hostName}`; only updates without a barcode fail. |
| `GET /api/merchandizingorder/{kioskId}/{yyyy-MM-dd}[?orderType=n]` | KCS | I | `cloud_merch_orders` rows for that kiosk and day, as a JSON array, `MerchandizingOrderType` as a name. Seeded empty. |
| `GET /api/productgroupsortorder/{type}` | KCS | I | `cloud_product_group_sort` rows. If there are none: product groups ranked by rentals in the last 14 days (needs `cloud_products`). KCS treats an empty list as "unsuccessful" and keeps its old file. |

### Proxy Service (`routers/proxy.py`)

| Method & path | Caller | | Behaviour |
|---|---|---|---|
| `GET /api/pricing/kiosk/{id}/rental/default` | KCS `PricingService` | I | `{FormatPricing[{Name: PriceLevelType, Prices[...]}]}` from `cloud_pricing_default` (`kiosk_ids` NULL = all). |
| `GET /api/pricing/kiosk/{id}/rentalbyprice` | KCS | I | `{RentalPricing[...]}` from `cloud_pricing_rental` (seeded with 3-night DVD and Blu-ray offers). |
| `GET /api/pricing/kiosk/{id}/purchasebyprice` | KCS | I | `{PurchasePricing[...]}` from `cloud_pricing_purchase` (seeded empty: prices are per title). |
| `GET /api/subscription/subscriptionplans` | KCS | I | `cloud_subscription_plans` (seeded `RBPLUS_OFFER_1`/`_2`). |
| `GET /api/subscription/redboxpluspromocampaigns` | KCS | I | `cloud_rbplus_promo_campaigns`. |
| `POST /api/customerprofile/login` | KCS | I | Email + password, phone + PIN, or CPN alone (Perks Pass). `(KLI003)` not found, `(KLI001)` blocked, `(KLI002)` wrong password or PIN (only checked when the account has one and `IsEncrypted` is false). HTTP 200 with `Success:false` on failure. `Response` has Customer, Loyalty (points, tier, tier counter), PromoCodes (stored offers with a code), OptIns, Subscriptions (eligible plan ids, existing subscriptions). |
| `POST /api/loyalty/estimateaccrual` | KCS | I | Points per rental or purchase item (§3.3). An unknown CPN gives 404 `{"Error":"Account not found"}` (KCS maps it to `(EA001)`). |
| `POST /api/loyalty/estimateredemption` | KCS | I | Points needed for a free 1-night item. |
| `POST /api/loyalty/updateaccount` | KCS | I | Perks sign-up or update: creates the customer (loyalty enrolled) by e-mail, or updates it by CPN or e-mail. Sets phone, PIN and text club. Returns `TempPassword` when the account had no password (it becomes the password). 409 when the e-mail or phone belongs to another account, 400 without an e-mail. |
| `POST /api/marketing/emailconfirmation` | KCS | I | `ConfirmationStatus` 1 (enrolled) or 3 (known, not enrolled). An unknown e-mail creates the customer: 5 `NewCustomer`. Links the card with `AccountNumber`. |
| `POST /api/marketing/kioskoptin` | KCS (replayable) | I | Appends opt-ins to the customer matched by `OptInMethodData` (e-mail or phone). Event. |
| `POST /api/promotion/promocodevalidation` | KCS | I | 200 with the code's `data` fields (Amount, PromotionIntentCode name, ...) when valid. Otherwise 422 with `Error`: `(D001)` unknown, inactive or outside its dates; `(D002)` not for this kiosk; `(D003)` max uses reached; `(D006)` on-demand. Uses are counted at reconcile. |
| `GET /api/planogram/allcurrent?lastReportedTime=` | KCS | I | `{Planograms[{Name, StartDate, FileType, Filename, LastUpdateUtc, PresignedUrl}]}`. **208** with `{"Planograms":[]}` when `lastReportedTime` (UTC, seconds) is at or after the last change, and also when there are no planograms (KCS throws on an empty 200 list). Deletions count as a change: the newest remaining planogram then carries the change time. |
| `GET /cloud-files/planograms/{id}/{filename}` | KCS (download) | I | `cloud_planograms.content`, or a generated one-page XPS. |
| `POST /api/cms/campaign/kiosk/{id}` | KCS `CampaignService` | I | `{LastSyncTime, InCarts, StartScreens}` from active, unexpired `cloud_campaigns` for the kiosk. **208** when the posted `LastSyncTime` is at or after the last change. Start-asset `Asset.Url` points at `/cloud-files/assets/{Id}/{Name}`. |
| `GET /cloud-files/assets/{assetId}/{name}` | KCS (download) | I | `cloud_assets.content`, or a generated PNG of the configured size and colour. |
| `POST /api/inventory/kiosktitlescount` | KCS (dual kiosks) | I | Per kiosk, `{titleId: count}` of discs with status `In`, using the barcode-to-title map learned from transactions. Requested titles with no stock give 0. |
| `POST /api/product/bytitleids` | KCS `ProductCatalogService` | I | `cloud_products` rows. Unknown titles are left out. Seeded empty. |
| `POST /api/kiosk/authenticate` | KCS (field staff) | I | With no `cloud_staff_users` rows, any non-empty user and password is accepted. Otherwise SHA-256 of the password must match. 401 `(KA001)` on failure. |
| `GET /api/kiosk/{id}/nearby` | KCS | I | Kiosks within 25 miles in `cloud_kiosk_locations` (haversine), nearest first, at most 10. `IsDual` from `dual_kiosk_id`. |
| `POST /api/kiosk/{id}/pingfailure` | UC `ReportFailedPingsJob` | S | Event `updateclient/pingfailure`. |
| `GET /api/installer/getpendingstates`, `getpendingbanners?stateid=`, `getpendingkiosks?stateid=&bannerid=`, `getopsmarket?kioskid=` | KCS (installer) | I | From `cloud_installer_kiosks` (seeded empty). The ops market comes from `cloud_kiosk_locations.ops_market` or `kiosks.market`; 404 if neither exists. |

### Kiosk Data Service (`routers/kioskdata.py`)

| Method & path | Caller | | Behaviour |
|---|---|---|---|
| `POST /api/kioskdata/{kioskversion,kioskstatistics,kioskproperties,kioskalert,auditevent,kioskevents,kioskfunctioncheck}` | KCS `DataClient` (replayable) | I | Event `kioskdata/<kind>`. Versions, statistics, properties and the function check are also merged into `kiosks.info`. Reply `{Success, Errors, HostName, StatusCode, MessageId}`. |
| `POST /api/KioskData/KioskClientServiceEvent` | KCS service start/stop | I | Event, plus `kiosks.info.kcs` (assembly version, last event). Reply also has `KinesisResponse: null`. |
| `POST /api/KioskData/DeviceStatus`, `RebootStatus`, `CardStats` | DS `KioskDataServiceClient` | I | Event `deviceservice/<kind>`. Device and reboot status go into `kiosks.info`. StandardResponse. |
| `POST /api/KioskData/AddKioskSessionJson` | KE `KioskDataServicesProxy` | I | Decodes `SessionData` = base64(int32 LE length ‖ gzip(JSON)); plain JSON is accepted when `IsUnCompressed`. Stores `cloud_kiosk_sessions` (the raw text is kept if it cannot be decoded). Reply `{"success": true, ...}`: the key is lower case because the engine checks `"success"` case-sensitively. |

### Ad Server (`routers/ads.py`)

KCS builds `AdServerUrl + "/api/ads/..."`. With the default `http://localhost:5005/api` that is `/api/api/ads/...`,
so every route below exists under both `/api` and `/api/api`.

| Method & path | Caller | | Behaviour |
|---|---|---|---|
| `GET {p}/ads/{location}` | KCS `AdServerClient.GetAd` | I | Weighted random active ad for the location and kiosk. Each ad handed out is a new impression (`cloud_ad_impressions`, `Ad.Id` = impression id). 404 `{Error}` if there is none. |
| `GET {p}/ads?locations=..` / `{p}/ads/cache?locations=..` | KCS | I | `{Data:[Ad]}`, one per location (all locations when none are given). |
| `POST {p}/ads/proofOfPlay` | KCS | I | Records `played_at`/`displayed_ms` on the impression. 404 if the impression is unknown. |
| `POST {p}/ads/{id}/expire` | KCS | I | Sets `expired_at` on the impression. |
| `POST {p}/analytics/{get_ad,proof_of_play,expire,cache,get_asset,download_asset,get_ad_timeout_event}` | KCS `AdServerAnalyticsClient` | S | Event `adserver/analytics_<kind>`. |
| `GET /cloud-files/ads/{assetId}.{png,jpg}` | KCS (asset download) | I | The creative (generated PNG unless content is uploaded). |

Seed: one house ad per location (728x90, VSM 1024x576).

### Product Reels (`routers/reels.py`)

| `POST /products/reels/bulkmorelikethis` | KCS `ProductReelsApiClient` (no auth) | I | `[{"id": group, "productIds": [...]}]` from `cloud_more_like_this`. Otherwise the groups most often rented by customers who rented that group (needs `cloud_products`). Groups with no recommendations are left out. |
|---|---|---|---|

### Bluefin (`routers/bluefin.py`)

| `POST /device/activate`, `/bluefinservice/device/activate` | DS `ActivationService` | I | Upserts `cloud_bluefin_devices` as `active`. StandardResponse. 400 without `KioskId` or `ReaderSerialNumber`. |
|---|---|---|---|
| `POST /device/deactivate`, `/bluefinservice/device/deactivate` | DS (never called) | I | Sets the device to `deactivated`. 404 if it is not known. |

### Fraud (`routers/fraud.py`)

No endpoints. See §3.8.

## 3. Behaviour details

### 3.1 Card authorisation (`PAYMENT_MODE`)

- `approve` (default): every card is approved.
- `decline`: every card authorisation is declined with `(A006)`.
- `rules`: approved, except for these test cards (by last four digits, `LastFour` or the PAN's tail):

  | Last four | Error |
  |---|---|
  | `0002` | `(A006)` Declined |
  | `0003` | `(A004)` Credit card error |
  | `0005` | `(A005)` AVS failure |
  | `0008` | `(A008)` Processor error |
  | `0009` | `(A100)` Authorize processing error |
  | any card past `ExpirationMonth/Year` | `(A004)` |

In every mode, a card or customer flagged `blocked` gets `(A002)`, and a gift card whose balance is below the cart
total gets `(A006)`. Declines are HTTP 200 with `Success:false` and the error, as KCS expects. No card data is stored
apart from BIN, last four and a SHA-256 fingerprint (`CardId`, else the clear PAN, else BIN|last4|expiry|name).

### 3.2 Customers

`cloud_customers` is keyed by CPN (sequence from 500000001), with unique e-mail and phone. Customers are created by
`loyalty/updateaccount` (Perks enrolled), by an e-mail given at reconcile or e-mail confirmation (not enrolled), or by
the admin. Cards link to the customer at reconcile, at authorize with a CPN, or through e-mail confirmation.
Passwords and PINs are compared in plain text. They are not real credentials: a NULL password accepts anything.

### 3.3 Perks points (invented values)

- Accrual: 100 points per vended rental or purchase item, for customers enrolled in loyalty.
- Redemption cost of a free 1-night item: DVD 1500, Blu-ray 1800, 4K 2100, game 2700.
- Loyalty discounts (`DiscountType` 2, `RedemptionPoints`) are deducted at reconcile.
- The ledger is in `cloud_points_ledger`.
- Tier, from rentals this calendar year: Member, Star (10), Superstar (20).

### 3.4 Return-visit promotion

`cloud_return_promo` (one row). Default: enabled, `FirstNightFree`, valid 7 days. The first promo check of a
transaction creates a single-use promo code `RV` + 6 hex characters in `cloud_promo_codes`
(`PromotionIntentCode` `FirstNightFreeItem`, or `FixedValue` with `amount` for `AmountOff`). Later checks return
the same code. KCS shows it only when the last disc of the transaction comes back.

### 3.5 Inventory model

- `cloud_inventory` is a per-kiosk snapshot replaced by `kiosk/sync`.
- Vends mark the disc `Rented`/`Sold` (no slot).
- A return puts it `In` at the returning kiosk and removes it from every other kiosk.
- A disc vended again while an earlier rental of it is still open closes that earlier rental (implicit return).
- Snapshots carry no title ids. Titles come from `cloud_barcodes`, learned from transaction line items, or loaded
  by the admin.

### 3.6 "Not modified" (208)

- Campaigns: statement triggers on `cloud_campaigns` stamp `cloud_changes('campaigns')`. The reply's
  `LastSyncTime` is that stamp truncated to seconds.
- Planograms: the stamp is `cloud_changes('planograms')` together with the rows' `updated_at`.
- Any `INSERT`/`UPDATE`/`DELETE` on those tables makes kiosks download again on their next poll.
- Triggers also stamp `pricing` and `subscriptions`. KCS has no "not modified" for those; it re-downloads them on
  its own schedule.

### 3.7 Idempotency

Replayable calls (reconcile, reservation pickup, return, reservation cancel/expire) are stored in
`cloud_processed_messages` by `(MessageId, endpoint)`. A replay from the KCS queue gets the original answer, and its
side effects are not applied again.

### 3.8 Fraud Services

`system.config` category `Fraud` has `FraudServicesUrl`/`FraudServicesKey` and `AwsFraudServicesUrl`/`AwsFraudServicesKey`.
No code in `src/` reads them:

- `Redbox.FraudServices.Proxy.BarcodeTrackingService` only logs.
- The rental `FraudService` handles local HAL images and statistics.
- Fraud reports go to the Inventory API's `api/disc/discfraud` through KCS.

So there is nothing to implement. `routers/fraud.py` is an empty router.

## 4. Editing data

All reference data is in plain tables. Edit it with SQL (or the admin service):

| Table | Holds |
|---|---|
| `cloud_pricing_default`, `cloud_pricing_rental`, `cloud_pricing_purchase` | prices; `kiosk_ids` limits rows to some kiosks |
| `cloud_subscription_plans`, `cloud_rbplus_promo_campaigns` | Redbox+ plans; `plan_items` is the KCS JSON |
| `cloud_promo_codes` | promo codes; `data` is the validation reply, `max_uses`, `kiosk_ids`, dates |
| `cloud_campaigns`, `cloud_assets` | start screens and in-cart offers; images (`content` bytea or a generated placeholder) |
| `cloud_planograms` | planograms (`content` bytea or a generated placeholder XPS) |
| `cloud_ads` | ads per location, with weight, dates and kiosks |
| `cloud_products`, `cloud_more_like_this`, `cloud_product_group_sort` | catalog and recommendations |
| `cloud_merch_orders` | merchandizing orders |
| `cloud_kiosk_locations`, `cloud_installer_kiosks`, `cloud_staff_users` | locations, installer data and staff logins |
| `cloud_customers`, `cloud_cards`, `cloud_customer_offers` | customers, cards (`blocked`, gift balances) and stored offers |
| `cloud_reservations` | reservations (`expires_at`, status) |

Seeded defaults (applied once, guarded by `cloud_meta` `seed_v1`):

- prices: DVD 1.85, Blu-ray 2.00, game 3.00, 4K 2.50 per night; the latest national defaults found in a kiosk's
  `Pricing.staged`;
- 3-night DVD and Blu-ray offers;
- Redbox+ plans;
- promo codes `WELCOME`, `BLURAY1`, `GAMENIGHT`, `SAVE1`, `ONETIME` (single use) and `EXPIRED`;
- one start screen and one in-cart offer (`SAVE1`);
- house ads;
- one placeholder planogram, `Front_Jan1.xps`.

## 5. Tests

- `tests/test_cloudapi_*.py`: protocol tests in-process (httpx ASGI transport). They need `DATABASE_URL` (use
  `127.0.0.1`, not `localhost`, on Windows) and `pip install -r requirements.txt -r requirements-dev.txt`. They are
  skipped when the database is not reachable.
- `tests/kcs/`: run the real KCS against the server.
  - `run_cloudapi.py [port]` starts the API on plain HTTP. On Windows it runs on a SelectorEventLoop because
    psycopg's async mode cannot use the Proactor loop.
  - `setup_kcs.py install <kcs-bin> <base>` / `setup_kcs.py restore` write and undo `appsettings.json` and
    `configuration.json`.
  - `drive_kcs.py` calls about 50 KCS endpoints and checks how KCS parsed the replies. It skips the pricing and
    campaign checks when KCS has no registry kiosk id.

## 6. Unknown or inferred

- Response fields beyond what the clients deserialize, and status codes the clients do not check, are guesses.
  Examples: 409/400 on updateaccount, 401 on authenticate, 404 on loyalty or unknown ads.
- Which reservation is "valid" was decided by the real online system. Here the kiosk's references are trusted
  unless cancelled, expired or picked up.
- `AccountNumber`, CPN and reference-number formats are invented (numeric strings and numbers).
- Points, tiers, the gift-card default balance and the rental-limit behaviour (none: `Limits` is always empty) are
  invented.
- Disc action and fraud bodies are free-form dictionaries. They are only logged.
- `ProductTypeId`, `FormatIds` and `ActionType` in the seeded promo codes are plausible values that the rental app
  passes through. Their real semantics are unknown.
- Charges for late returns are computed and stored, but nothing charges the card.

## 7. Ticket Services (2014, never deployed)

The kiosk-engine builds in the update-store seeds of Feb/Mar 2014 ship `Redbox.DVD.TicketServices.dll`
(version 1.3.9.4, x86). It adds event-ticket sales to the rental app through Lua kernel functions
(`kernel.ticketsvcs_gettickets`, `_placeorder`, `_updateticketlock`, `_releaseticketlock`, `_getemailbycard`,
`_sendnotification`). These call a WCF "Ticket Controller Service". Redbox apparently never ran this service in
production; the only URL in the client is a dev host. `routers/tickets.py` implements it from the decompiled
client.

### 7.1 Endpoint and protocol

`POST /TicketControllerService/ControllerService.svc`. The client uses WCF `basicHttpBinding`: SOAP 1.1,
`text/xml; charset=utf-8`, no security for `http:` URLs, Transport security for `https:`. Both work here. Plain
HTTP is served on port 80 by the same process (`CLOUD_API_HTTP_PORT`, published by `rb.ps1 up`). HTTPS is on
443 as usual.

- The server dispatches on the `SOAPAction` header,
  `http://www.redbox.com/ControllerServices/Tickets/2012/04/IControllerService/<Operation>`. An empty header
  falls back to the body's wrapper element. An unknown action gets an `a:ActionNotSupported` fault.
- Messages are wrapped document/literal with the DataContractSerializer. Request `<Op>` / `<opRequest>` and reply
  `<OpResponse>` / `<OpResult>` are in the contract namespace. Members are in the namespace of the contract that
  declares them: base `Request`/`Response` in `...Contract.Schema`, operation types in `...Contract.Schema.<Op>`,
  `SupplierDisclaimer`/`GeneralError` in the service namespace.
- Member order is base contract first, then alphabetical; no `[DataMember(Order)]` is set. Required members are
  always written, with `i:nil="true"` for null strings and `int?`. Replies are generated from the `CONTRACTS`
  table, which mirrors the client's `[DataMember]` attributes.
- Faults: `ValidationError` (detail in `...Contract.Schema`, `s:Client`) for bad requests.
  `GeneralError` (detail in the service namespace, `s:Server`) for internal errors. Both carry
  `ErrorCorrelationId`, `Message` and `MessageId` (the request's MessageId). HTTP status 500.
- `GET ...svc?wsdl` returns a WSDL with inline schemas, generated from the same table. WCF's `WsdlImporter`
  imports it without errors.

### 7.2 Operations and behaviour

State is in the `ticket_*` tables (`schema/52_tickets.sql`). Every call records an event (source `tickets`) and
touches the kiosk row. Failures the client maps go in `Response.Code` with `Success=false`. The codes are
`TicketsResponseCode`: 101 not available, 102 no contiguous seats, 103 sold out, 151 card not authorised,
152 NSF, 153 fraud risk, 154 lock expired/relock failed, 155 merchant failure, 161 declined.

| Operation | Behaviour |
|---|---|
| GetTickets | Releases the `UnlockTickets` locks first. Then looks up the production (unknown → ValidationError) and checks that every requested `PriceCodeId` exists and the total is within `ticket_limit_per_order` (else ValidationError). Off sale → 101. It takes up to 3 ticket groups that sell all the requested price codes and have enough free seats (`total - sold - unexpired held locks`). Each one gets a lock (`LockGuid`, `LockDuration` = the production's `lock_duration_minutes`, default 5). Returns them with prices, per-ticket taxes (one `TaxItem`, type 1), delivery methods and the referenced supplier disclaimers. No group with enough seats → 102, or 103 when nothing is left. |
| UpdateTicketLock | Extends the lock and sets its quantity. An expired lock is re-held if the seats are still free, otherwise 154. A released lock → 154. A sold lock → `Success=false`. Unknown lock or wrong group → ValidationError. |
| ReleaseTicketLock | Marks the lock released (also when already expired). A sold lock → `Success=false`. |
| PlaceOrder | Checks every item: lock exists and is not sold, delivery method offered, price codes and per-ticket price/fee match the server, quantity ≤ lock quantity. `OrderHeader` totals must match within 0.01. Any failure → ValidationError. Expired locks are re-held or the order fails with 154. Card decision follows `PAYMENT_MODE`: `approve`; `decline` → 161; `rules` → by last four of `Track2` (0002 → 161, 0003 → 151, 0005 → 152, 0008/0009 → 155, expired → 151). A blocked card or customer (`cloud_cards`/`cloud_customers`) → 153. If approved, the locks are marked sold, `ticket_groups.sold` goes up, `UnlockTickets` are released, card hash → e-mail is remembered, and the new `OrderId` is returned. A declined order is stored too (OrderId 0 is returned, locks stay held). A replayed MessageId returns the stored result. Track data and the encrypted PAN are not stored; the last four digits are. |
| GetEmailByCard | Looks up `HashedCreditCardNumber` in `ticket_card_emails` (from earlier orders). Falls back to a `cloud_cards` row keyed `sha256("id:" + hash)` (the KCS card-id fingerprint, `customers.card_key`) and its customer's e-mail. Not found → `Success=false`, `Email` nil. |
| SendNotification | Needs `CustomerEmail` or `CustomerSMSPhone` (else ValidationError). Stores the sign-up in `ticket_notifications`, linked to a `cloud_customers` row with that e-mail. Nothing is sent. |

Sample data (invented): delivery methods 1 WILL CALL and 2 EMAIL, plus productions 70001 (concert, 3 groups),
70002 (basketball, 2 groups) and 70003 (comedy club, one group of 4 seats). Price codes are 1 Adult,
2 Child and 3 Senior. Edit the tables to sell other events.

### 7.3 Pointing a 2014 kiosk at it

`TicketServiceProxy.Url` reads the URL only from the engine's "system" configuration: section `general`, key
`TicketServicesURL` (`IConfigurationService.TryGetConfigurationData("system", "general", "TicketServicesURL")`).
Neither the endpoint address in `kioskengine.exe.config` nor the unused `DefaultTicketServicesUrl` constant
(`http://tickets-dev.redbox.com/...`) is used for the address. So:

- Set `General` / `TicketServicesURL` in the engine's system configuration to
  `http://<server>/TicketControllerService/ControllerService.svc` (or `https://...`; the server certificate
  includes `tickets-dev.redbox.com`, and the kiosk must trust the local CA).
- To keep the original value `http://tickets-dev.redbox.com/TicketControllerService/ControllerService.svc`, add
  a hosts entry `<server-ip> tickets-dev.redbox.com`. Host port 80 must be reachable (`BIND_ADDRESS`).
- The machine-settings key `Remote Services\Ticket Services` holds the debug switches `LiveCall<Op>` (default
  true; false makes the proxy simulate replies locally) and `TicketServicesTimeout`. Leave `LiveCall*` on.
  `TicketServicesTimeout` is not applied to the binding: WCF defaults apply (1 minute send timeout, 64 KB
  `maxReceivedMessageSize`, so keep replies small).

The kiosk's event catalogue (`tickets-profile.data`, `tickets-images.zip`, read by `TicketDataService`) is a local
file. It reaches the kiosk outside this service and is not served here. The `ProductionId`s and price codes in it
must exist in `ticket_productions` / `ticket_price_codes`.

### 7.4 Tests

- `tests/test_tickets.py` covers the protocol in-process. It checks element names, namespaces and member order;
  locks, expiry and relock; inventory; orders, replays and declines; GetEmailByCard; notifications; both fault
  types; WSDL; and the plain-HTTP listener of the running container.
- `tests/clients/run-ticketservices-harness.ps1 -Dll <path>\Redbox.DVD.TicketServices.dll [-Url ...]` drives the
  DLL's own `ControllerServiceClient` (internal, via reflection, `TicketServicesHarness.cs`) from 32-bit Windows
  PowerShell 5.1. It calls every operation, checks that each reply and the ValidationError fault deserialize,
  and imports `?wsdl` with `WsdlImporter`. Each run buys 2 tickets from production 70001.

### 7.5 Inferred

- All server behaviour: which groups to suggest and how many, locking every suggested group, availability and
  relock rules, price and total validation, which failures are faults and which are `Success=false`.
- `LockDuration` is taken to be minutes (the client's mock uses 5; the unit is not visible in the client).
- `TaxItem.TaxAmt` is taken to be the per-ticket tax, and tax type 1 is invented.
- GetEmailByCard's link to KCS card fingerprints assumes the engine's `GenerateCardHashId()` equals the KCS
  `CardId`. This is unverified, so in practice the e-mail comes from earlier ticket orders.
- The client has two bugs that the server tolerates. `SendNotification` resets its array index inside the loop,
  so only the first subscription arrives and the rest are nil. `PlaceOrder` swaps `FirstName`/`LastName`; the
  server stores them swapped back.
- GeneralError is only produced for unexpected server errors. The real client was run against ValidationError
  only; the GeneralError detail shape is tested in pytest.

## 8. Kiosk Services MessageService (engines 1.81 and older)

Before the Kiosk Client Service, the engine talked to one "Kiosk Services" endpoint,
`https://bl2.accessredbox.net/KioskServices/MessageService.rem` (machine setting
`Remote Services\Store Services\KioskServicesURL`). Every call is `IMessageService.Process(string json) -> string
json`. The transport (`routers/remoting.py`, with the Mono host `rb-remoting` for .NET Remoting) is described in
`docs/systems/kiosk-engine.md` §6.6. The messages are handled by `cloudapi/kioskservices.py`.

Decompiled clients:

| Build | Used by | Messages |
|---|---|---|
| `Redbox.DVD.KioskServices.dll` (2016-10-28 seed) | 2014-2016 engines | "2016" column below |
| `Redbox.KioskServices.Proxy` 1.81.0.27 (`src/KioskServices`) | engine 1.81 | "1.81" column |
| `Redbox.KioskServices.Proxy` (2024-07-23 seed) | later 1.8x engines. Each call has a `Send*ToKioskClientService` switch; Kiosk Services is used when the switch is off | "2024" column |

Queued messages (q) are written to the engine's message queue. The `KioskServices` (default) or `SoftSync` queue
processor sends them later: it forwards the queued JSON as is and deletes the entry whatever the result.

### 8.1 Reply conventions

The client parses the reply with Redbox.Core's JavaScriptSerializer into `Dictionary<string, object>`:

- `Success` is a JSON boolean. On failure the client matches `Errors[0].Code` against its ResponseCode enums.
  The codes used here are `(B004)` authentication failed, `(D001)`-`(D006)` promo codes, `(A002)`, `(A006)`,
  `(A025)`, `(A026)` and the other `(Axxx)` authorisation codes, `(UPA001)` phone conflict, `(EA001)`/`(ER001)`
  account not found, and `(U001)` for anything else (including an unknown MessageType).
- Integers arrive as Int32 and are cast with `(int)`. Numbers with a fraction arrive as Decimal (`(decimal)`), so
  money is always written with a fraction (`1.0`, never `1`).
- Dates that the client converts with `ToObject<DateTime>` are Redbox.Core objects
  `{"theDate": ticks, "dateTimeKind", "dateString"}`. `KioskPing.ServerTime` is one, in UTC; the kiosk sets its
  clock when it is more than 5 minutes off. Request dates come in the same shape. The server reads the ticks,
  because EmailConfirm writes `dateString` with a broken format. Local times are taken as UTC.
- Every reply has `IsOnline: true`, `ServerBusy: false`, `ServerName` and `server_name` (the 2024 merchant calls
  read the lower-case key). The 2014-2016 engines take `IsOnline` from every reply and drain their queue only
  while it is true.
- `Password` (Authenticate) and `Pin` (UpdateAccount) are `EncryptionHelper.Encrypt` values: TripleDES-CBC with
  PKCS7. The key is `Guid("02CAD7C4-...").ToByteArray()` and the IV is the first 8 bytes of
  `Guid("AD186FCD-...").ToByteArray()`. They are decrypted before use (`kioskservices.decrypt_secret`, checked
  against .NET Framework).
- For the state-changing types, a message whose MessageId was already processed gets the stored reply
  (`cloud_processed_messages`, endpoint `ks:<MessageType>`). Queue retries therefore do not book twice.
- A 2024 Return queue entry is `{ClientData, ReturnMessage}`. If there is no top-level `MessageType`, the inner
  `ReturnMessage` is processed.

### 8.2 Messages

Most messages carry the same objects as their KCS successors (`CreditCard`, `ShoppingCart`,
`CustomerProfileNumber`). The handler runs this backend's KCS-era endpoint on the message and reshapes the
answer, so both kiosk generations share one behaviour and one set of tables. 2014-2016 carts are converted to a
KCS `ShoppingCart` first. They come as `Contents` (group name → `Items` with `Prices`, plus `Totals`) and
`DiscountCode`/`DiscountValue`. A group whose name contains "purchase" is a purchase group; every other group is
a rental group.

| MessageType | 2016 | 1.81 | 2024 | Behaviour (reply keys the client reads) |
|---|---|---|---|---|
| GetPendingStates | x | x | x | Installer data from `cloud_installer_kiosks`: `States[{Id, Description}]`. |
| GetPendingBanners | x | x | x | `StateId` → `Banners[{Id, Name}]`. |
| GetPendingKiosks | x | x | x | `StateId`, `BannerId` → `PendingKiosks[{Id, City, State, ZipCode, DueTime, Address, MarketName, KaseyaMarketName}]`. |
| GetOpsMarket | x | x | x | `cloud_kiosk_locations.ops_market` or `kiosks.market` → `OpsMarket`, `KaseyaServer`. Unknown → `Success=false`. |
| Authenticate | x | x | x | Field-staff login, as `api/kiosk/authenticate` (`cloud_staff_users`; an empty table accepts anyone). Failure → `(B004)`. The lock-out after failed attempts happens on the kiosk. |
| DiscountCodeValidation | x | x | x | `cloud_promo_codes`, as the KCS promo validation: `Amount`, `PromotionIntentCode`, `ActionType`, `RentQty`/`GetQty`/... from the code's data. Errors `(D001)`/`(D002)`/`(D003)`/`(D006)`. |
| EmailConfirm | x | x | x | Finds or creates the customer by e-mail → `ConfirmationStatus` (5 new, 1/3 known). 2016 sends `CardId`, which links that card to the customer; 1.81 and 2024 send `AccountNumber`. |
| KioskOptIn (q) | x | x | x | Opt-ins are appended to `cloud_customers.opt_ins`, matched by e-mail or phone. The 2016 `CardHash` is linked to the customer of the opted-in e-mail. |
| UpdateAccount (direct or q) | | x | x | Perks sign-up or update (`api/loyalty/updateaccount`): `CustomerProfileNumber`, `MobilePhoneNumber`, and `TempPassword` for new accounts. Phone used by another account → `(UPA001)`. |
| EstimateAccrual / EstimateRedemption | | x | x | Cart → `Items[{ProductId, Points}]` (§3.3). Unknown CPN → `(EA001)` / `(ER001)`. |
| GetCustomerOffers | | x | x | `cloud_customer_offers` for the card's customer → `StoredOffers`/`NewOffers`, `CustomerProfileNumber`, `AccountNumber`, `EmailAddress`. Offers have the shape `CustomerOfferProxy.ParseOffer` reads: `DiscountType` as an enum name, `IncludeFormats`/`ExcludeFormats` as strings, title ids as ints. |
| GetCustomer | x | | | Digital (Redbox Instant): card `CardId` → `IsEligibleFreeTrial`, `Email`, `CustomerProfile{HasCustomerProfile, CPFirstName, CPLastName}`. |
| RBiFreeTrialRequest | x | | | Finds or creates the customer by `Email` and links the card. Records `info.rbi_free_trial` unless `RequestStatus` is a decline. A second trial → `Success=false`. Reply: `IsEligibleFreeTrial`, `CanCreateProfile` (the e-mail was new), `CustomerProfile{CanCreateProfile}`. |
| GetCustomerCredits | x | | | Free-rental credits (`cloud_customers.info.credits`, an int), one per rental item in the cart: `CreditSummary{TotalCreditsApplied, TotalCreditsAvailable}`, `CreditAllocations[{Barcode, Amount, NumberApplied}]`, `CustomerProfile{Valid, FirstName}`, `GiftCardSummary{MaxDiscs, MaxGames}`. |
| PreAuth | | | x | Pre-authorisation rule. There are no rules here: `PromptRequired=false`, `SkipAuthRule=true`, `AuthAmount` = cart total, `AuthRuleId` and `AuthDays` 0. A blocked card or customer → `(A002)`. |
| Authorize | x | | x | As `api/transaction/authorize` (`PAYMENT_MODE`, §3.1): `ReferenceNumber`, `Email`, `AccountNumber`, `CustomerProfileNumber`, `ConfirmationStatus`, `CustomerProfile{Valid, FirstName, IsRBI}`, `CreditSummary`, `Offers` (empty). With `UseCredits` `OptedIn` (2016), the allocated credits are deducted after approval. |
| GCAuthorize | x | | x | Gift card, as `api/transaction/giftcardauthorize`, plus `GiftCardSummary{Exists, Status, Balance, MaxDiscs, MaxGames}`. |
| ResvAuth | x | | x | As `api/transaction/reservationauthorize` (reservation `ReferenceNumber`). |
| PickupAuthorize | | | x | As `api/transaction/pickupauthorize`. A cancelled or expired reservation → `(A026)`; an already picked-up one → `(A025)`. |
| PickupValidation | x | | x | As `api/reservation/validatepickup`: `ReferenceNumbers` (the valid ones; the client takes the first), `ValidReservations`, `CancelledReservations` (Int32), `AllowRop`, `TransactionEntities`, `AccountNumber`, `CustomerProfileNumber`. |
| Reconcile (q) | x | | x | As `api/transaction/reconcile`: books rentals and purchases, takes the discs out of the inventory, records promo redemptions, points and the gift-card balance. |
| PickupReconcile (q) | | | x | As `api/transaction/reservationpickup`; also marks the reservation `picked_up`. |
| ModResvPickup (q) | | | x | Reservation picked up with a changed cart. With `ModifiedCart`, `cloud_reservations.items` becomes the cart's items. PickupReconcile books the vend. |
| Return (q) | x | | x | As `api/transaction/return`: closes the open rental (late nights computed), puts the disc back in its deck/slot, adds a `cloud_returns` row. |
| SoftSync (q or immediate) | x | | x | Full disc snapshot, as `api/kiosk/sync`. `Status` 0/1 (InventoryItemStatus) is stored as `In`/`Removed`. `RemovedQueueItems` is ignored; the kiosk deletes them after a success. |
| DiscFraud (2024: q) | x | | x | Logged (event `inventory/discfraud`). |
| DiscAction | x | | x | FMA disc actions, logged (event `inventory/discaction`). |
| RentalCount | x | | | `Count` = rentals vended at this kiosk today (UTC). |
| UnplayableReturn | x | | | Adds a `cloud_returns` row with `return_type` 9 (UnplayableReturn) for the latest rental of the barcode. The customer (by `Email`, else the rental's customer) gets one free-rental credit. |
| KioskPing | x | | | Health ping. `Versions` and `Statistics` are merged into `kiosks.info` like the KCS KioskVersion/KioskStatistics posts; engine/bundle version and the last ping time are recorded (event `health/ping`). Reply: `ServerTime`. |
| KioskAlert (q) | x | | | Event `kioskdata/kioskalert` with the numeric alert type (`AlertTypes`: Qlm 1 … TitleEmpty 8). |
| AuditEvent (q) | x | | | Event `kioskdata/auditevent`. |

In 1.81 and the 2024 build, pings, alerts, audit events, kiosk events and function checks always go to the Kiosk
Client Service / Kiosk Data Service, even when everything else uses Kiosk Services.

The transport records every message as event `kioskservices/<MessageType>`, with card fields, password and PIN
masked.

### 8.3 Inferred

- All server behaviour, as for the KCS-era APIs (§6).
- **Credits.** The client does not show what a 2014-2016 "credit" was (Redbox Instant or unplayable-disc
  credits). Here a credit is one free rental night, kept in `cloud_customers.info.credits`; edit it to give
  credits. Authorize with `UseCredits=OptedIn` consumes them, and UnplayableReturn grants one.
- The meaning of `RentalCount` (today's rentals at the kiosk), the `RequestStatus` values of RBiFreeTrialRequest
  (anything but a decline word counts as acceptance), `CanCreateProfile` (the e-mail is not registered yet) and
  PreAuth's rule values.
- Group names in 2014-2016 `Contents`; only "purchase" is special-cased.
- **Offer `ExpirationDate`.** The client reads it with a raw `(DateTime)` cast. JavaScriptSerializer only
  produces a DateTime there from the escaped `"\/Date(ms)\/"` string, and the transport's `json.dumps` cannot
  write that escape. So the key is left out, and the client defaults it to today.
  `ExcludeIfWithinRedboxReleaseDate` is sent as a TimeSpan object when the offer data gives it in days.
- The FraudService and ReservationServices hooks that read the replies of queued Return and validation
  messages are not decompiled (missing assemblies). Those replies carry only `Success`.

### 8.4 Tests

`tests/test_kioskservices.py` sends every MessageType through the REST transport, built the way the proxies build
them: Redbox.Core dates, numeric enums, 2014-2016 `Contents` and 2024 `ShoppingCart` carts, the 2024 Return
wrapper and TripleDES secrets. It checks the replies and the state left in the tables. Run it with
`rb.ps1 test tests/test_kioskservices.py`.
