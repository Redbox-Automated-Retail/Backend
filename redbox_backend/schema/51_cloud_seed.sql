-- Default reference data for the kiosk cloud APIs. Applied once (guarded by cloud_meta 'seed_v1'), so
-- rows deleted or edited by an operator are not recreated at the next start-up.
-- Prices are the national defaults found in a kiosk's Pricing.staged (latest effective entry per format).
DO $seed$
BEGIN
IF EXISTS (SELECT 1 FROM cloud_meta WHERE key = 'seed_v1') THEN
    RETURN;
END IF;

-- Default rental prices per format (FormatPricing). PriceLevelType 2 DVD, 3 Blu-ray, 4 Games, 6 4K UHD.
INSERT INTO cloud_pricing_default (price_level_type, start_date, location_rank, initial_night, initial_days,
                                   extra_night, non_return_days, non_return_price, expire_price) VALUES
    (2, '2021-07-06', 4, 1.85, 1, 1.85, 16, 29.60, 1.85),
    (3, '2018-10-03', 4, 2.00, 1, 2.00, 16, 32.00, 2.00),
    (4, '2018-10-03', 4, 3.00, 1, 3.00, 22, 66.00, 3.00),
    (6, '2018-10-03', 4, 2.50, 1, 2.50, 16, 40.00, 2.50);

-- Multi-night offers (RentalPricing by price level): 3 nights for a fixed price.
INSERT INTO cloud_pricing_rental (price_levels, start_date, location_rank, initial_night, initial_days,
                                  extra_night, non_return_days, non_return_price, expire_price) VALUES
    ('[{"PriceLevelType":2,"PriceLevelTypeValue":null}]', '2024-01-01', 4, 4.50, 3, 1.85, 16, 29.60, 0),
    ('[{"PriceLevelType":3,"PriceLevelTypeValue":null}]', '2024-01-01', 4, 5.00, 3, 2.00, 16, 32.00, 0);

-- Redbox+ subscription plans (RBPLUS_OFFER_1/_2 map to Tier1/Tier2 in KCS).
INSERT INTO cloud_subscription_plans (id, subscription_id, description, effective_start, effective_end, is_promotional, plan_items) VALUES
    ('RBPLUS_OFFER_1', 'RBPLUS', 'Redbox+ (1 free 1-night disc rental per month)', '2024-01-01', '2099-12-31', false,
     '[{"Price":4.99,"BillingPeriod":"Monthly","IsPromoPrice":false,"PeriodLength":1,"PeriodUnit":"Month","SequenceNum":1,
        "Benefits":[{"Id":"RBPLUS_FREE_RENTAL","Description":"1 free 1-night disc rental","Quantity":1,"Traits":[],"Period":"Monthly","Type":"PromoCode"},
                    {"Id":"RBPLUS_EXTRA_POINTS","Description":"Bonus Perks points","Quantity":250,"Traits":[],"Period":"Monthly","Type":"Points"}]}]'),
    ('RBPLUS_OFFER_2', 'RBPLUS', 'Redbox+ Premium (2 free 1-night disc rentals per month)', '2024-01-01', '2099-12-31', false,
     '[{"Price":9.99,"BillingPeriod":"Monthly","IsPromoPrice":false,"PeriodLength":1,"PeriodUnit":"Month","SequenceNum":1,
        "Benefits":[{"Id":"RBPLUS_FREE_RENTAL","Description":"2 free 1-night disc rentals","Quantity":2,"Traits":[],"Period":"Monthly","Type":"PromoCode"},
                    {"Id":"RBPLUS_EXTENDED_TIME","Description":"Extra night on every rental","Quantity":1,"Traits":[],"Period":"Unlimited","Type":"ExtendedTime"}]}]');
INSERT INTO cloud_rbplus_promo_campaigns (promo_campaign_id, tier) VALUES (1001, 1), (1002, 2);

-- Promo codes. data holds the PromotionCodeValidationResponse fields returned for a valid code.
INSERT INTO cloud_promo_codes (code, valid_from, valid_to, max_uses, data, note) VALUES
    ('WELCOME',  '2024-01-01', NULL, NULL,
     '{"Amount":1.85,"PromotionIntentCode":"FirstNightDVD","ProductTypeId":1,"FormatIds":[1],"ActionType":"Discount","AllowFullDiscount":true}',
     'free first night of a DVD rental'),
    ('BLURAY1',  '2024-01-01', NULL, NULL,
     '{"Amount":2.00,"PromotionIntentCode":"FirstNightBluray","ProductTypeId":1,"FormatIds":[2],"ActionType":"Discount","AllowFullDiscount":true}',
     'free first night of a Blu-ray rental'),
    ('GAMENIGHT', '2024-01-01', NULL, NULL,
     '{"Amount":3.00,"PromotionIntentCode":"FirstNightGame","ProductTypeId":2,"ActionType":"Discount","AllowFullDiscount":true}',
     'free first night of a game rental'),
    ('SAVE1',    '2024-01-01', NULL, NULL,
     '{"Amount":1.00,"PromotionIntentCode":"FixedValue","ActionType":"Discount","AllowFullDiscount":false}',
     '$1 off the cart'),
    ('ONETIME',  '2024-01-01', NULL, 1,
     '{"Amount":1.00,"PromotionIntentCode":"FixedValue","ActionType":"Discount","AllowFullDiscount":false}',
     'single-use code (D003 once redeemed)'),
    ('EXPIRED',  '2020-01-01', '2020-12-31', NULL,
     '{"Amount":1.00,"PromotionIntentCode":"FixedValue"}',
     'expired code (D001)');

INSERT INTO cloud_return_promo (id, enabled, promo_type, amount, valid_days) VALUES (1, true, 1, NULL, 7)
    ON CONFLICT (id) DO NOTHING;

-- Images (content NULL: a placeholder PNG of the given size and colour is generated).
INSERT INTO cloud_assets (asset_id, name, width, height, color) VALUES
    ('1001', 'start-welcome.png', 1024, 600, '#c8102e'),
    ('1002', 'start-perks.png',   1024, 600, '#1f4e9a'),
    ('ad-welcome', 'Welcome house ad', 728, 90, '#c8102e'),
    ('ad-titledetail', 'TitleDetail house ad', 728, 90, '#1f4e9a'),
    ('ad-vsm', 'VSM house ad', 1024, 576, '#2e7d32'),
    ('ad-zipcode', 'ZipCode house ad', 728, 90, '#6a1b9a'),
    ('ad-pickup', 'Pickup house ad', 728, 90, '#ef6c00'),
    ('ad-return', 'Return house ad', 728, 90, '#00838f'),
    ('ad-email', 'EmailNotification house ad', 728, 90, '#5d4037'),
    ('ad-vending', 'Vending house ad', 728, 90, '#ad1457'),
    ('ad-pleasewait', 'PleaseWait house ad', 728, 90, '#455a64');

INSERT INTO cloud_ads (location, asset_id) VALUES
    ('Welcome', 'ad-welcome'), ('TitleDetail', 'ad-titledetail'), ('VSM', 'ad-vsm'), ('ZipCode', 'ad-zipcode'),
    ('Pickup', 'ad-pickup'), ('Return', 'ad-return'), ('EmailNotification', 'ad-email'),
    ('Vending', 'ad-vending'), ('PleaseWait', 'ad-pleasewait');

-- Campaigns. StartAsset controls reference cloud_assets by Asset.Id; Url/FullPath are filled in per request.
INSERT INTO cloud_campaigns (campaign_id, campaign_type, rank, start_date, data) VALUES
    (1, 'StartScreen', 1, '2024-01-01',
     '{"Controls":[
        {"ControlType":"StartAsset","ControlId":11,"DisplayDuration":8,"Asset":{"Id":1001,"Name":"start-welcome.png"},
         "Target":"None","TargetValue":null,"Order":1,"ShowPressToStart":true,"IncludeIfNoInventory":true},
        {"ControlType":"StartAsset","ControlId":12,"DisplayDuration":8,"Asset":{"Id":1002,"Name":"start-perks.png"},
         "Target":"PromoCode","TargetValue":"WELCOME","Order":2,"ShowPressToStart":true,"IncludeIfNoInventory":true},
        {"ControlType":"Carousel","ControlId":13,"DisplayDuration":5,"MaxTitles":20}]}'),
    (2, 'InCart', 1, '2024-01-01',
     '{"CampaignInCartId":21,"InCartType":"RentOneGetFixedOff","Amount":1.00,"PromoCode":"SAVE1","ExcludeTitles":[]}');

-- One placeholder planogram (generated XPS).
INSERT INTO cloud_planograms (name, start_date, filename) VALUES ('Front', '2024-01-01', 'Front_Jan1.xps');

INSERT INTO cloud_meta (key, value) VALUES ('seed_v1', to_jsonb(now()));
END
$seed$;
