-- 010: LangGraph checkpointer tables (AX-15 2.0 session memory: HITL pause/resume per thread = trace_id).
-- Same DDL as langgraph-checkpoint-postgres 3.x PostgresSaver.MIGRATIONS (v0..v9), applied here so every DB
-- change stays a numbered migration (the app never calls PostgresSaver.setup()). Indexes are created without
-- CONCURRENTLY because migrations run inside one transaction; the tables are new and small.

CREATE TABLE IF NOT EXISTS checkpoint_migrations (
    v INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

CREATE TABLE IF NOT EXISTS checkpoint_blobs (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT NOT NULL,
    blob BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    blob BYTEA NOT NULL,
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

ALTER TABLE checkpoint_blobs ALTER COLUMN blob DROP NOT NULL;
ALTER TABLE checkpoint_writes ADD COLUMN IF NOT EXISTS task_path TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints (thread_id);
CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs (thread_id);
CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes (thread_id);

-- Mark the library's own migrations as applied so PostgresSaver never tries to run them.
INSERT INTO checkpoint_migrations (v)
SELECT v FROM generate_series(0, 9) AS v
ON CONFLICT (v) DO NOTHING;

-- Past-actions lookup (get_past_actions: human-verified rows by store / asset / time).
CREATE INDEX IF NOT EXISTS action_log_status_created_idx ON action_log (status, created_at);
