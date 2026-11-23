-- 003: batch runs, tickets, ticket history.
-- Ticket identity (L0 decision 4): ticket_uid is the surrogate key every other
-- table references; ticket_id (e.g. INC-00001) is the business key, unique only
-- within a (source, batch_id), so a panel batch may reuse a train ticket_id.
-- Training set: source = 'train', batch_id = 'train'.

CREATE TABLE IF NOT EXISTS batch_runs (
    batch_id        text PRIMARY KEY,              -- e.g. "train" or "<batch_name>-<utc timestamp>"
    batch_name      text NOT NULL,
    source          text NOT NULL CHECK (source IN ('train', 'panel_test', 'manual')),
    status          text NOT NULL DEFAULT 'running'
                        CHECK (status IN ('running', 'completed', 'failed')),
    files_total     integer NOT NULL DEFAULT 0,
    files_loaded    integer NOT NULL DEFAULT 0,
    files_rejected  integer NOT NULL DEFAULT 0,
    errors          jsonb NOT NULL DEFAULT '[]'::jsonb,   -- per-file validation errors
    report_path     text,
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz
);

-- store_id / asset_id deliberately have no foreign keys: tickets may reference
-- assets missing from the CMDB (known data issue) or unknown stores (panel /
-- free-text intake). Those are flagged by data-quality checks, not rejected.
-- Leakage fields (chaos_type, generator text) are never stored here.
CREATE TABLE IF NOT EXISTS tickets (
    ticket_uid        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ticket_id         text NOT NULL,               -- business key, e.g. INC-00001
    store_id          text,
    store_name        text,
    submitted_by      text,
    contact_phone     text,
    date_opened       timestamptz,
    priority          text,
    category          text,
    subcategory       text,
    subject           text,
    description       text,
    asset_id          text,
    system_version    text,
    tags              text[] NOT NULL DEFAULT '{}',
    escalation_flag   boolean NOT NULL DEFAULT false,
    related_tickets   text[] NOT NULL DEFAULT '{}',
    resolution_notes  text,
    status            text,
    source            text NOT NULL CHECK (source IN ('train', 'panel_test', 'manual')),
    batch_id          text NOT NULL REFERENCES batch_runs (batch_id),
    input_format      text NOT NULL DEFAULT 'json' CHECK (input_format IN ('json', 'free_text')),
    raw_input         text,                        -- original free text, if any (after leakage strip)
    loaded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT tickets_source_batch_ticket_key UNIQUE (source, batch_id, ticket_id)
);
CREATE INDEX IF NOT EXISTS tickets_ticket_id_idx ON tickets (ticket_id);
CREATE INDEX IF NOT EXISTS tickets_store_id_idx ON tickets (store_id);
CREATE INDEX IF NOT EXISTS tickets_asset_id_idx ON tickets (asset_id);
CREATE INDEX IF NOT EXISTS tickets_batch_id_idx ON tickets (batch_id);
CREATE INDEX IF NOT EXISTS tickets_source_idx   ON tickets (source);

CREATE TABLE IF NOT EXISTS ticket_history (
    history_id  bigserial PRIMARY KEY,
    ticket_uid  bigint NOT NULL REFERENCES tickets (ticket_uid) ON DELETE CASCADE,
    seq         integer NOT NULL,                  -- order within the ticket's history
    ts          timestamptz,
    actor       text,
    note        text,
    trace_id    text,                              -- set when written by the agent
    loaded_at   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (ticket_uid, seq)
);
