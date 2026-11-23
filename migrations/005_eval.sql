-- 005: evaluation tables, in schema `eval` (never read by agent/LLM code paths).
-- Planned (L2): a separate ax15_agent role with no grants on schema eval.
-- Labels key on tickets.ticket_uid; loaders resolve it from
-- (source, batch_id, ticket_id), e.g. ('train', 'train', 'INC-00162').

CREATE TABLE IF NOT EXISTS eval.eval_labels (
    ticket_uid                  bigint PRIMARY KEY REFERENCES public.tickets (ticket_uid) ON DELETE CASCADE,
    correct_routing             text NOT NULL,     -- l1_guided / l2_escalation
    relevant_kb_docs            text[] NOT NULL DEFAULT '{}',   -- kb_documents.doc_path values
    should_escalate             boolean NOT NULL,
    escalation_reason           text,
    correct_resolution_summary  text,
    expected_agent_action       text,
    chaos_correct_behavior      text,
    evaluator_notes             text,
    raw                         jsonb NOT NULL,    -- original label record
    loaded_at                   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS eval.chaos_labels (
    ticket_uid         bigint PRIMARY KEY REFERENCES public.tickets (ticket_uid) ON DELETE CASCADE,
    chaos_type         text NOT NULL,
    expected_behavior  text NOT NULL,
    pass_criteria      text,
    fail_criteria      text,
    raw                jsonb NOT NULL DEFAULT '{}'::jsonb,
    loaded_at          timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS eval.eval_splits (
    split_version  text NOT NULL,                  -- e.g. "v1-seed42"
    ticket_uid     bigint NOT NULL REFERENCES public.tickets (ticket_uid) ON DELETE CASCADE,
    split          text NOT NULL CHECK (split IN ('dev', 'heldout')),
    seed           integer NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (split_version, ticket_uid)
);
