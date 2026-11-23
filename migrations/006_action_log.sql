-- 006: action log — every agent/human write with provenance (L0 decision 2).
-- Becomes episodic memory later; joins to tickets by ticket_uid and to traces by trace_id.

CREATE TABLE IF NOT EXISTS action_log (
    action_id          bigserial PRIMARY KEY,
    ticket_uid         bigint NOT NULL REFERENCES tickets (ticket_uid) ON DELETE CASCADE,
    trace_id           text,                       -- tracing-tool trace id for the run that proposed it
    actor              text NOT NULL,              -- agent node name or human user id
    actor_type         text NOT NULL CHECK (actor_type IN ('agent', 'human', 'system')),
    action_type        text NOT NULL,              -- work_note, update_ticket, escalate_to_l2, notify_requester, trigger_automation
    payload            jsonb NOT NULL DEFAULT '{}'::jsonb,
    status             text NOT NULL DEFAULT 'proposed'
                           CHECK (status IN ('proposed', 'approved', 'rejected', 'executed', 'failed')),
    verified_by_human  boolean NOT NULL DEFAULT false,
    verified_by        text,                       -- who approved / rejected (HITL)
    verified_at        timestamptz,
    batch_id           text REFERENCES batch_runs (batch_id),
    created_at         timestamptz NOT NULL DEFAULT now(),
    executed_at        timestamptz
);
CREATE INDEX IF NOT EXISTS action_log_ticket_uid_idx ON action_log (ticket_uid);
CREATE INDEX IF NOT EXISTS action_log_trace_id_idx  ON action_log (trace_id);
CREATE INDEX IF NOT EXISTS action_log_status_idx    ON action_log (status);
