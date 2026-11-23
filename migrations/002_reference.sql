-- 002: reference tables (stores, CMDB assets, SLA matrix, version matrix).
-- Source: data-original/data/*.csv and kb/system-specs/version-matrix.csv.

CREATE TABLE IF NOT EXISTS stores (
    store_id        text PRIMARY KEY,              -- e.g. SW-0001
    store_name      text NOT NULL,
    city            text,
    neighborhood    text,
    store_type      text,                          -- corporate / franchise
    region_manager  text,
    support_phone   text,
    loaded_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS assets (
    asset_id          text PRIMARY KEY,            -- e.g. POS-0001-T1
    store_id          text NOT NULL REFERENCES stores (store_id),
    asset_type        text NOT NULL,
    make              text,
    model             text,
    system_version    text,                        -- e.g. "FoodTech POS v5.1.0"
    serial_number     text,
    install_date      date,
    last_service_date date,
    warranty_expiry   date,
    loaded_at         timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS assets_store_id_idx ON assets (store_id);

CREATE TABLE IF NOT EXISTS sla_matrix (
    priority                   text NOT NULL,      -- P1..P4
    category                   text NOT NULL,
    first_response_minutes     integer NOT NULL,
    resolution_target_minutes  integer NOT NULL,
    escalation_to_l2_minutes   integer NOT NULL,
    loaded_at                  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (priority, category)
);

CREATE TABLE IF NOT EXISTS version_matrix (
    system           text NOT NULL,                -- e.g. "FoodTech POS"
    version          text NOT NULL,                -- e.g. "v4.2.1"
    release_date     date,
    known_bugs       text,
    patch_available  text,                         -- free text in source ("Yes — v4.2.2 patch")
    notes            text,                         -- e.g. "EOL scheduled 2026-12"
    loaded_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (system, version)
);
