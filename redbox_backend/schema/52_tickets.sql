-- Ticket Controller Service (2014 kiosk engine "Ticket Services", routers/tickets.py).
-- Event inventory, seat locks, orders and notification sign-ups. Sample events are seeded once (ON CONFLICT DO
-- NOTHING): edit or add rows to change what the kiosk can sell.

CREATE TABLE IF NOT EXISTS ticket_delivery_methods (
    delivery_method_id int PRIMARY KEY,
    display_name       text NOT NULL,
    description        text NOT NULL
);

-- SupplierDisclaimer: a supplier note the kiosk shows before checkout (TicketGroup.SupplierNoteLogId).
CREATE TABLE IF NOT EXISTS ticket_supplier_notes (
    supplier_note_log_id int PRIMARY KEY,
    supplier_id          int NOT NULL,
    note                 text NOT NULL
);

CREATE TABLE IF NOT EXISTS ticket_events (
    event_id   bigint PRIMARY KEY,
    name       text NOT NULL,
    short_note text
);

-- A production is one performance of an event at a venue; the kiosk asks for tickets by ProductionId (the ids
-- come from the kiosk's tickets-profile.data catalog).
CREATE TABLE IF NOT EXISTS ticket_productions (
    production_id          bigint PRIMARY KEY,
    event_id               bigint REFERENCES ticket_events ON DELETE SET NULL,
    venue_name             text,
    starts_at              timestamptz,
    on_sale_at             timestamptz NOT NULL DEFAULT now(),
    off_sale_at            timestamptz,
    ticket_limit_per_order int NOT NULL DEFAULT 8,
    lock_duration_minutes  int NOT NULL DEFAULT 5
);

CREATE TABLE IF NOT EXISTS ticket_price_codes (
    production_id bigint NOT NULL REFERENCES ticket_productions ON DELETE CASCADE,
    price_code_id int NOT NULL,
    name          text NOT NULL,
    PRIMARY KEY (production_id, price_code_id)
);

-- A block of seats sold together (section/row or general admission). Available = total - sold - held locks.
CREATE TABLE IF NOT EXISTS ticket_groups (
    ticket_group_id      int PRIMARY KEY,
    production_id        bigint NOT NULL REFERENCES ticket_productions ON DELETE CASCADE,
    supplier_id          int NOT NULL DEFAULT 1,
    supplier_note_log_id int REFERENCES ticket_supplier_notes ON DELETE SET NULL,
    zone                 text,
    level                text,
    section              text,
    row_name             text,
    suggestion_type_id   int NOT NULL DEFAULT 1,
    suggestion_type_name text NOT NULL DEFAULT 'Best Available',
    display_order        int NOT NULL DEFAULT 0,
    total_tickets        int NOT NULL,
    sold                 int NOT NULL DEFAULT 0,
    delivery_method_ids  int[] NOT NULL DEFAULT '{1,2}'
);
CREATE INDEX IF NOT EXISTS ticket_groups_production_idx ON ticket_groups (production_id);

-- Per-ticket prices of a group for each price code it sells (taxes per ticket).
CREATE TABLE IF NOT EXISTS ticket_group_prices (
    ticket_group_id  int NOT NULL REFERENCES ticket_groups ON DELETE CASCADE,
    price_code_id    int NOT NULL,
    price_per_ticket numeric(10,2) NOT NULL,
    fee_per_ticket   numeric(10,2) NOT NULL DEFAULT 0,
    price_tax        numeric(10,2) NOT NULL DEFAULT 0,
    fee_tax          numeric(10,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (ticket_group_id, price_code_id)
);

-- Seat holds handed out by GetTickets (status held -> sold | released | expired).
CREATE TABLE IF NOT EXISTS ticket_locks (
    lock_guid       uuid PRIMARY KEY,
    ticket_group_id int NOT NULL REFERENCES ticket_groups ON DELETE CASCADE,
    kiosk_id        bigint,
    quantity        int NOT NULL,
    price_codes     jsonb NOT NULL DEFAULT '[]'::jsonb,
    status          text NOT NULL DEFAULT 'held',
    expires_at      timestamptz NOT NULL,
    order_id        bigint,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ticket_locks_held_idx ON ticket_locks (ticket_group_id) WHERE status = 'held';

-- PlaceOrder results (approved and declined). No card number or track data is stored.
CREATE TABLE IF NOT EXISTS ticket_orders (
    order_id        bigserial PRIMARY KEY,
    message_id      uuid UNIQUE,
    kiosk_id        bigint,
    status          text NOT NULL,          -- approved | declined
    code            text,                   -- TicketsResponseCode number when declined (151, 161, ...)
    email           text,
    hashed_card_id  text,
    card_last_four  text,
    first_name      text,
    last_name       text,
    postal_code     text,
    subtotal        numeric(10,2),
    total_fee       numeric(10,2),
    total_tax       numeric(10,2),
    total           numeric(10,2),
    kiosk_order_ref int,
    promotions      jsonb NOT NULL DEFAULT '[]'::jsonb,
    items           jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Card hash -> e-mail, learnt from approved orders (GetEmailByCard).
CREATE TABLE IF NOT EXISTS ticket_card_emails (
    hashed_card_id text PRIMARY KEY,
    email          text NOT NULL,
    updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ticket_notifications (
    id            bigserial PRIMARY KEY,
    kiosk_id      bigint,
    message_id    uuid,
    email         text,
    sms_phone     text,
    customer_id   bigint,
    subscriptions jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at    timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- sample data (invented)

INSERT INTO ticket_delivery_methods (delivery_method_id, display_name, description) VALUES
    (1, 'WILL CALL', 'Pick the tickets up at the venue box office on the day of the event.'),
    (2, 'EMAIL', 'Receive the tickets by e-mail and print them at home.')
ON CONFLICT DO NOTHING;

INSERT INTO ticket_supplier_notes (supplier_note_log_id, supplier_id, note) VALUES
    (1, 1, 'Tickets are issued by the venue. All sales are final; no refunds or exchanges.'),
    (2, 2, 'Lawn seating is general admission. Gates open one hour before the show.')
ON CONFLICT DO NOTHING;

INSERT INTO ticket_events (event_id, name, short_note) VALUES
    (5001, 'Sample Rock Concert', 'An evening of loud guitars'),
    (5002, 'Sample Basketball Game', 'Home team vs. visitors'),
    (5003, 'Sample Comedy Night', 'Small club, few seats')
ON CONFLICT DO NOTHING;

INSERT INTO ticket_productions (production_id, event_id, venue_name, starts_at, on_sale_at, off_sale_at,
                                ticket_limit_per_order, lock_duration_minutes) VALUES
    (70001, 5001, 'Example Amphitheater', now() + interval '30 days', now() - interval '1 day', '2099-12-31', 8, 5),
    (70002, 5002, 'Example Arena', now() + interval '14 days', now() - interval '1 day', '2099-12-31', 6, 5),
    (70003, 5003, 'Example Comedy Club', now() + interval '7 days', now() - interval '1 day', '2099-12-31', 4, 5)
ON CONFLICT DO NOTHING;

INSERT INTO ticket_price_codes (production_id, price_code_id, name) VALUES
    (70001, 1, 'Adult'), (70001, 2, 'Child'),
    (70002, 1, 'Adult'), (70002, 2, 'Child'), (70002, 3, 'Senior'),
    (70003, 1, 'Adult')
ON CONFLICT DO NOTHING;

INSERT INTO ticket_groups (ticket_group_id, production_id, supplier_id, supplier_note_log_id, zone, level, section,
                           row_name, suggestion_type_id, suggestion_type_name, display_order, total_tickets,
                           delivery_method_ids) VALUES
    (700011, 70001, 1, 1, 'Floor', '1', '101', 'A', 1, 'Best Available', 1, 20, '{1,2}'),
    (700012, 70001, 1, 1, 'Lower', '2', '204', 'G', 2, 'Lowest Price', 2, 40, '{1,2}'),
    (700013, 70001, 2, 2, 'Lawn', '3', 'GA', 'GA', 3, 'General Admission', 3, 200, '{2}'),
    (700021, 70002, 1, 1, 'Courtside', '1', '1', 'B', 1, 'Best Available', 1, 10, '{1,2}'),
    (700022, 70002, 1, NULL, 'Upper', '3', '310', 'R', 2, 'Lowest Price', 2, 60, '{1,2}'),
    (700031, 70003, 1, 1, 'Main', '1', 'Main', '1', 1, 'Best Available', 1, 4, '{1}')
ON CONFLICT DO NOTHING;

INSERT INTO ticket_group_prices (ticket_group_id, price_code_id, price_per_ticket, fee_per_ticket, price_tax, fee_tax) VALUES
    (700011, 1, 89.50, 12.25, 0, 0.98), (700011, 2, 69.50, 12.25, 0, 0.98),
    (700012, 1, 49.50, 8.75, 0, 0.70), (700012, 2, 34.50, 8.75, 0, 0.70),
    (700013, 1, 29.50, 6.50, 0, 0.52), (700013, 2, 19.50, 6.50, 0, 0.52),
    (700021, 1, 150.00, 18.00, 0, 1.44), (700021, 2, 120.00, 18.00, 0, 1.44), (700021, 3, 120.00, 18.00, 0, 1.44),
    (700022, 1, 25.00, 5.00, 0, 0.40), (700022, 2, 15.00, 5.00, 0, 0.40), (700022, 3, 15.00, 5.00, 0, 0.40),
    (700031, 1, 20.00, 3.00, 1.60, 0.24)
ON CONFLICT DO NOTHING;
