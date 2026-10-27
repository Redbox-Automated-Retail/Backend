# Kiosk cloud APIs (self-hosted)

One FastAPI app (`python -m redbox_backend cloudapi`, `redbox_backend/cloudapi/app.py`) serves every HTTPS API the
kiosk software calls besides the update service, MQTT and the IoT certificate service. The kiosk reaches them
through different base URLs (Proxy Service, Transaction API, Inventory API, Kiosk Data Service, Ad Server, Product
Reels, Bluefin); their paths do not collide, so all base URLs can point at the same host.

Server behaviour is inferred from the decompiled clients (`src/KioskClient/KioskClientService.API`, Device Service,
Kiosk Engine, Update Client) and the protocol docs in `docs/systems/`. Everything is stored in PostgreSQL (tables
`cloud_*`, `backend/redbox_backend/schema/50_cloud.sql`; defaults seeded once by `51_cloud_seed.sql`).

Code: `cloudapi/routers/{transaction,inventory,proxy,kioskdata,ads,reels,bluefin,fraud}.py`, helpers
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
