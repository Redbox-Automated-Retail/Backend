-- Kiosk cloud APIs (Transaction API, Inventory API, Proxy Service, Kiosk Data Service, Ad Server,
-- Product Reels, Bluefin). Every table is prefixed `cloud_`. Idempotent: applied at every start-up.

CREATE TABLE IF NOT EXISTS cloud_meta (
    key         text PRIMARY KEY,
    value       jsonb,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Messages already processed, so replays from the KCS queue (at-least-once) get the same answer.
CREATE TABLE IF NOT EXISTS cloud_processed_messages (
    message_id  uuid NOT NULL,
    endpoint    text NOT NULL,
    kiosk_id    bigint,
    status      int NOT NULL DEFAULT 200,
    response    jsonb,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (message_id, endpoint)
);

-- ---------------------------------------------------------------- customers / loyalty
CREATE SEQUENCE IF NOT EXISTS cloud_cpn_seq START 500000001;
CREATE TABLE IF NOT EXISTS cloud_customers (
    customer_id      bigserial PRIMARY KEY,
    cpn              text UNIQUE NOT NULL DEFAULT nextval('cloud_cpn_seq')::text,
    email            text,
    phone            text,
    first_name       text,
    last_name        text,
    password         text,            -- NULL: any password accepted (no real authentication here)
    pin              text,
    loyalty_enrolled boolean NOT NULL DEFAULT false,
    points           int NOT NULL DEFAULT 0,
    tier             text NOT NULL DEFAULT 'Member',
    email_verified   boolean NOT NULL DEFAULT false,
    text_club        boolean NOT NULL DEFAULT false,
    blocked          boolean NOT NULL DEFAULT false,
    opt_ins          jsonb NOT NULL DEFAULT '[]'::jsonb,
    info             jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS cloud_customers_email_idx ON cloud_customers (lower(email)) WHERE email IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS cloud_customers_phone_idx ON cloud_customers (phone) WHERE phone IS NOT NULL;

-- Payment cards seen, keyed by a fingerprint (SHA-256 of the PAN when clear, else BIN|last four|expiry|name).
CREATE SEQUENCE IF NOT EXISTS cloud_account_seq START 700000001;
CREATE TABLE IF NOT EXISTS cloud_cards (
    card_key       text PRIMARY KEY,
    account_number text UNIQUE NOT NULL DEFAULT nextval('cloud_account_seq')::text,
    customer_id    bigint REFERENCES cloud_customers ON DELETE SET NULL,
    bin            text,
    last_four      text,
    card_type      int,
    is_gift_card   boolean NOT NULL DEFAULT false,
    gift_balance   numeric(10,2),
    gift_status    int,                -- GiftCardStatus: 0 None, 1 Unregistered, 2 Registered
    blocked        boolean NOT NULL DEFAULT false,
    first_seen_at  timestamptz NOT NULL DEFAULT now(),
    last_seen_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cloud_points_ledger (
    id           bigserial PRIMARY KEY,
    customer_id  bigint NOT NULL REFERENCES cloud_customers ON DELETE CASCADE,
    delta        int NOT NULL,
    reason       text NOT NULL,
    reference    bigint,
    created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cloud_customer_subscriptions (
    id                    bigserial PRIMARY KEY,
    customer_id           bigint NOT NULL REFERENCES cloud_customers ON DELETE CASCADE,
    subscription_id       text NOT NULL,
    subscription_plan_id  text,
    status                int NOT NULL DEFAULT 0,  -- CustomerSubscriptionPlanStatus: 0 Active, 1 Canceled
    end_date              timestamptz,
    created_at            timestamptz NOT NULL DEFAULT now()
);

-- Stored customer offers (GetCustomerOffers). Seeded empty; add rows to give a customer an offer.
CREATE TABLE IF NOT EXISTS cloud_customer_offers (
    offer_id        bigserial PRIMARY KEY,
    customer_id     bigint REFERENCES cloud_customers ON DELETE CASCADE,  -- NULL: offered to everyone (NewOffers)
    campaign_id     bigint NOT NULL DEFAULT 0,
    data            jsonb NOT NULL DEFAULT '{}'::jsonb,  -- extra CustomerOffer fields (PromotionCode, DiscountAmount, ...)
    expires_at      timestamptz,
    redeemed_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- transactions
CREATE SEQUENCE IF NOT EXISTS cloud_reference_seq START 300000001;
CREATE TABLE IF NOT EXISTS cloud_transactions (
    id                 bigserial PRIMARY KEY,
    reference_number   bigint UNIQUE NOT NULL DEFAULT nextval('cloud_reference_seq'),
    kiosk_id           bigint,
    kind               text NOT NULL,      -- authorize, giftcard, pickup, reservation, offline
    status             text NOT NULL,      -- authorized, declined, reconciled
    message_id         uuid,
    reservation_ref    bigint,
    card_key           text,
    customer_id        bigint REFERENCES cloud_customers ON DELETE SET NULL,
    amount             numeric(10,2),
    errors             jsonb NOT NULL DEFAULT '[]'::jsonb,
    request            jsonb,
    reconcile          jsonb,
    created_at         timestamptz NOT NULL DEFAULT now(),
    reconciled_at      timestamptz
);
CREATE INDEX IF NOT EXISTS cloud_transactions_resv_idx ON cloud_transactions (reservation_ref);
CREATE INDEX IF NOT EXISTS cloud_transactions_kiosk_idx ON cloud_transactions (kiosk_id, created_at DESC);

-- One row per line item of a reconciled transaction.
CREATE TABLE IF NOT EXISTS cloud_rentals (
    id               bigserial PRIMARY KEY,
    transaction_id   bigint NOT NULL REFERENCES cloud_transactions ON DELETE CASCADE,
    kiosk_id         bigint,
    group_type       int NOT NULL,         -- 1 Rental, 2 Purchase, 3 Subscription
    barcode          text,
    product_id       bigint,
    product_type     text,
    product_family   text,
    vend_status      int,
    status           text NOT NULL,        -- out, returned, purchased, not_vended, subscription
    pricing          jsonb,
    rented_at        timestamptz NOT NULL DEFAULT now(),
    returned_at      timestamptz,
    return_kiosk_id  bigint,
    extra_nights     int,
    extra_charge     numeric(10,2)
);
CREATE INDEX IF NOT EXISTS cloud_rentals_barcode_idx ON cloud_rentals (barcode, rented_at DESC);
CREATE INDEX IF NOT EXISTS cloud_rentals_txn_idx ON cloud_rentals (transaction_id);

CREATE TABLE IF NOT EXISTS cloud_returns (
    id            bigserial PRIMARY KEY,
    message_id    uuid,
    kiosk_id      bigint,
    barcode       text,
    return_type   int,
    deck          int,
    slot          int,
    return_date   timestamptz,
    failed_security_read boolean,
    rental_id     bigint REFERENCES cloud_rentals ON DELETE SET NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- Online reservations. Created by the online system (or the admin); unknown references that a kiosk
-- presents are accepted and recorded, because the kiosk holds the reservation itself.
CREATE TABLE IF NOT EXISTS cloud_reservations (
    reference_number  bigint PRIMARY KEY,
    kiosk_id          bigint,
    customer_id       bigint REFERENCES cloud_customers ON DELETE SET NULL,
    card_key          text,
    status            text NOT NULL DEFAULT 'reserved',  -- reserved, picked_up, cancelled, expired
    reason_code       text,
    authorize_at_pickup boolean NOT NULL DEFAULT true,
    items             jsonb NOT NULL DEFAULT '[]'::jsonb,
    expires_at        timestamptz,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- inventory
CREATE TABLE IF NOT EXISTS cloud_inventory (
    kiosk_id       bigint NOT NULL,
    barcode        text NOT NULL,
    deck           int,
    slot           int,
    status         text NOT NULL,          -- as sent by the kiosk ("In", "Removed", ...) or "Rented"/"Sold"
    merch_status   bigint,
    thin_reason    bigint,
    thin_date      timestamptz,
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (kiosk_id, barcode)
);
CREATE INDEX IF NOT EXISTS cloud_inventory_barcode_idx ON cloud_inventory (barcode);

CREATE TABLE IF NOT EXISTS cloud_inventory_syncs (
    id          bigserial PRIMARY KEY,
    kiosk_id    bigint,
    message_id  uuid,
    disc_count  int,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Barcode -> product (title) id, learned from transactions (sync snapshots carry no title).
CREATE TABLE IF NOT EXISTS cloud_barcodes (
    barcode     text PRIMARY KEY,
    product_id  bigint NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS cloud_merch_orders (
    id           bigserial PRIMARY KEY,
    kiosk_id     bigint NOT NULL,
    order_date   date NOT NULL,
    order_type   int NOT NULL,            -- 1 RebalancingExtract, 2 Fill, 3 ThinningExtract
    title_id     bigint NOT NULL,
    unit_count   int NOT NULL
);
CREATE INDEX IF NOT EXISTS cloud_merch_orders_idx ON cloud_merch_orders (kiosk_id, order_date);

CREATE TABLE IF NOT EXISTS cloud_product_group_sort (
    sort_type         int NOT NULL,
    product_group_id  bigint NOT NULL,
    forecasted_rents  int NOT NULL,
    PRIMARY KEY (sort_type, product_group_id)
);

-- ---------------------------------------------------------------- reference data (Proxy Service)
-- kiosk_ids NULL = every kiosk.
CREATE TABLE IF NOT EXISTS cloud_pricing_default (
    id              bigserial PRIMARY KEY,
    kiosk_ids       bigint[],
    price_level_type int NOT NULL,         -- 1 Title, 2 Dvd, 3 Bluray, 4 Games, 6 FourKUHD
    price_level_value text,
    start_date      timestamp NOT NULL,
    end_date        timestamp,
    attributes      smallint,
    location_rank   int NOT NULL DEFAULT 4,
    initial_night   numeric(10,2) NOT NULL,
    initial_days    int NOT NULL DEFAULT 1,
    extra_night     numeric(10,2) NOT NULL,
    non_return_days int NOT NULL,
    non_return_price numeric(10,2) NOT NULL,
    expire_price    numeric(10,2) NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cloud_pricing_rental (
    id              bigserial PRIMARY KEY,
    kiosk_ids       bigint[],
    price_levels    jsonb NOT NULL,         -- [{"PriceLevelType":1,"PriceLevelTypeValue":"123"}]
    start_date      timestamp NOT NULL,
    end_date        timestamp,
    attributes      smallint,
    location_rank   int NOT NULL DEFAULT 4,
    initial_night   numeric(10,2) NOT NULL,
    initial_days    int NOT NULL DEFAULT 1,
    extra_night     numeric(10,2) NOT NULL,
    non_return_days int NOT NULL,
    non_return_price numeric(10,2) NOT NULL,
    expire_price    numeric(10,2) NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS cloud_pricing_purchase (
    id              bigserial PRIMARY KEY,
    kiosk_ids       bigint[],
    start_date      timestamp NOT NULL,
    end_date        timestamp,
    purchase_price  numeric(10,2) NOT NULL,
    titles          bigint[] NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS cloud_subscription_plans (
    id                  text PRIMARY KEY,
    subscription_id     text NOT NULL,
    description         text,
    effective_start     timestamptz NOT NULL,
    effective_end       timestamptz NOT NULL,
    is_promotional      boolean NOT NULL DEFAULT false,
    plan_items          jsonb NOT NULL DEFAULT '[]'::jsonb,   -- SubscriptionPlanItem list (PascalCase)
    active              boolean NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS cloud_rbplus_promo_campaigns (
    promo_campaign_id   int PRIMARY KEY,
    tier                int NOT NULL                          -- 1 Tier1, 2 Tier2
);

CREATE TABLE IF NOT EXISTS cloud_promo_codes (
    code             text PRIMARY KEY,         -- stored upper case
    active           boolean NOT NULL DEFAULT true,
    valid_from       timestamptz,
    valid_to         timestamptz,
    kiosk_ids        bigint[],                 -- NULL: every kiosk
    max_uses         int,                      -- NULL: unlimited
    uses             int NOT NULL DEFAULT 0,
    on_demand        boolean NOT NULL DEFAULT false,
    data             jsonb NOT NULL DEFAULT '{}'::jsonb,   -- PromotionCodeValidationResponse fields (Amount, PromotionIntentCode, ...)
    note             text,
    created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cloud_promo_redemptions (
    id               bigserial PRIMARY KEY,
    code             text NOT NULL,
    transaction_id   bigint REFERENCES cloud_transactions ON DELETE CASCADE,
    kiosk_id         bigint,
    amount           numeric(10,2),
    created_at       timestamptz NOT NULL DEFAULT now()
);
-- Return-visit promotion (GET api/transaction/return/{barcode}/promocheck); single row id=1.
CREATE TABLE IF NOT EXISTS cloud_return_promo (
    id               int PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    enabled          boolean NOT NULL DEFAULT true,
    promo_type       int NOT NULL DEFAULT 1,          -- ReturnPromoType: 0 AmountOff, 1 FirstNightFree
    amount           numeric(10,2),
    valid_days       int NOT NULL DEFAULT 7,
    code_prefix      text NOT NULL DEFAULT 'RV'
);
CREATE TABLE IF NOT EXISTS cloud_return_promo_issued (
    transaction_id   bigint PRIMARY KEY REFERENCES cloud_transactions ON DELETE CASCADE,
    code             text NOT NULL,
    expires_at       timestamptz NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now()
);

-- Campaigns (start screens and in-cart offers). data = the CMS detail object minus CampaignId/dates/rank.
CREATE TABLE IF NOT EXISTS cloud_campaigns (
    campaign_id      bigint PRIMARY KEY,
    campaign_type    text NOT NULL CHECK (campaign_type IN ('StartScreen', 'InCart')),
    rank             int NOT NULL DEFAULT 0,
    start_date       timestamp NOT NULL,
    end_date         timestamp,
    kiosk_ids        bigint[],
    data             jsonb NOT NULL DEFAULT '{}'::jsonb,
    active           boolean NOT NULL DEFAULT true,
    updated_at       timestamptz NOT NULL DEFAULT now()
);
-- Start-screen / ad images. content NULL: a generated placeholder PNG is served.
CREATE TABLE IF NOT EXISTS cloud_assets (
    asset_id         text PRIMARY KEY,
    name             text,
    mime_type        text NOT NULL DEFAULT 'image/png',
    width            int NOT NULL DEFAULT 728,
    height           int NOT NULL DEFAULT 90,
    color            text NOT NULL DEFAULT '#c8102e',
    content          bytea,
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- Planograms (XPS). content NULL: a generated one-page placeholder XPS is served.
CREATE TABLE IF NOT EXISTS cloud_planograms (
    id               bigserial PRIMARY KEY,
    name             text NOT NULL,
    start_date       timestamp NOT NULL,
    file_type        text NOT NULL DEFAULT 'xps',
    filename         text NOT NULL,
    kiosk_ids        bigint[],
    content          bytea,
    updated_at       timestamptz NOT NULL DEFAULT now()
);

-- Last-change marks used for "208 Already Reported".
CREATE TABLE IF NOT EXISTS cloud_changes (
    area        text PRIMARY KEY,
    changed_at  timestamptz NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION cloud_mark_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO cloud_changes (area, changed_at) VALUES (TG_ARGV[0], now())
    ON CONFLICT (area) DO UPDATE SET changed_at = now();
    RETURN NULL;
END $$;
CREATE OR REPLACE TRIGGER cloud_campaigns_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON cloud_campaigns
    FOR EACH STATEMENT EXECUTE FUNCTION cloud_mark_change('campaigns');
CREATE OR REPLACE TRIGGER cloud_planograms_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON cloud_planograms
    FOR EACH STATEMENT EXECUTE FUNCTION cloud_mark_change('planograms');
CREATE OR REPLACE TRIGGER cloud_pricing_default_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON cloud_pricing_default
    FOR EACH STATEMENT EXECUTE FUNCTION cloud_mark_change('pricing');
CREATE OR REPLACE TRIGGER cloud_pricing_rental_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON cloud_pricing_rental
    FOR EACH STATEMENT EXECUTE FUNCTION cloud_mark_change('pricing');
CREATE OR REPLACE TRIGGER cloud_pricing_purchase_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON cloud_pricing_purchase
    FOR EACH STATEMENT EXECUTE FUNCTION cloud_mark_change('pricing');
CREATE OR REPLACE TRIGGER cloud_subscription_plans_changed AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON cloud_subscription_plans
    FOR EACH STATEMENT EXECUTE FUNCTION cloud_mark_change('subscriptions');

-- Product catalog (POST api/product/bytitleids) and "more like this" (Product Reels).
CREATE TABLE IF NOT EXISTS cloud_products (
    title_id          bigint PRIMARY KEY,
    product_group_id  bigint NOT NULL,
    product_ids       jsonb NOT NULL DEFAULT '{}'::jsonb,   -- {"DVD": 123, "Bluray": 124}
    product_page      text,
    name              text
);
CREATE INDEX IF NOT EXISTS cloud_products_group_idx ON cloud_products (product_group_id);
CREATE TABLE IF NOT EXISTS cloud_more_like_this (
    product_group_id  bigint PRIMARY KEY,
    product_ids       bigint[] NOT NULL
);

-- ---------------------------------------------------------------- ads
CREATE TABLE IF NOT EXISTS cloud_ads (
    id          bigserial PRIMARY KEY,
    location    text NOT NULL,                -- AdLocation name (Welcome, TitleDetail, VSM, ...)
    asset_id    text NOT NULL REFERENCES cloud_assets ON DELETE CASCADE,
    provider    int NOT NULL DEFAULT 0,       -- AdProviderName: 0 Default
    expirable   boolean NOT NULL DEFAULT true,
    weight      int NOT NULL DEFAULT 1,
    kiosk_ids   bigint[],
    active      boolean NOT NULL DEFAULT true,
    start_date  timestamptz,
    end_date    timestamptz
);
CREATE TABLE IF NOT EXISTS cloud_ad_impressions (
    id           uuid PRIMARY KEY,
    ad_id        bigint REFERENCES cloud_ads ON DELETE SET NULL,
    kiosk_id     bigint,
    location     text,
    via          text,                         -- get, list, cache
    issued_at    timestamptz NOT NULL DEFAULT now(),
    played_at    timestamptz,
    displayed_ms int,
    expired_at   timestamptz
);

-- ---------------------------------------------------------------- kiosk data / devices
CREATE TABLE IF NOT EXISTS cloud_kiosk_sessions (
    session_id     text PRIMARY KEY,
    kiosk_id       bigint,
    start_time     text,
    end_time       text,
    data           jsonb,
    raw            text,                       -- kept when the session data could not be decoded
    received_at    timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cloud_bluefin_devices (
    kiosk_id               bigint NOT NULL,
    reader_serial          text NOT NULL,
    injected_serial        text,
    status                 text NOT NULL,       -- active, deactivated
    local_time             text,
    activated_at           timestamptz,
    deactivated_at         timestamptz,
    PRIMARY KEY (kiosk_id, reader_serial)
);

-- Kiosk locations for api/kiosk/{id}/nearby and installer pending kiosks.
CREATE TABLE IF NOT EXISTS cloud_kiosk_locations (
    kiosk_id        bigint PRIMARY KEY,
    label           text,
    location_name   text,
    location_type   int NOT NULL DEFAULT 1,   -- 0 Outdoor, 1 Indoor
    address         text,
    city            text,
    state           text,
    zip             text,
    latitude        double precision,
    longitude       double precision,
    dual_kiosk_id   bigint,
    ops_market      text,
    kaseya_server   text
);
CREATE TABLE IF NOT EXISTS cloud_installer_kiosks (
    id                  int PRIMARY KEY,
    state_id            smallint NOT NULL,
    state_name          text NOT NULL,
    banner_id           int NOT NULL,
    banner_name         text NOT NULL,
    address             text,
    city                text,
    zip                 text,
    due_time            text,
    market_name         text,
    kaseya_market_name  text,
    pending             boolean NOT NULL DEFAULT true
);
-- Field-staff logins (POST api/kiosk/authenticate). Empty table: any non-empty user/password is accepted.
CREATE TABLE IF NOT EXISTS cloud_staff_users (
    username         text PRIMARY KEY,
    password_sha256  text NOT NULL,          -- hex SHA-256 of the password
    active           boolean NOT NULL DEFAULT true
);
