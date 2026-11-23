-- 004: knowledge base — documents, chunks (vector + full text), error codes, ingestion runs.

CREATE TABLE IF NOT EXISTS kb_documents (
    doc_path      text PRIMARY KEY,                -- relative to kb/, e.g. "runbooks/pos-scanner.md" (matches labels)
    doc_type      text NOT NULL CHECK (doc_type IN ('runbook', 'faq', 'sop', 'system_spec')),
    title         text,
    category      text,
    content_hash  text NOT NULL,                   -- sha256 of source file
    ingested_at   timestamptz NOT NULL DEFAULT now()
);

-- status (L0 decision 3): proposed runbook updates are stored as 'draft' and must
-- never reach store staff until approved; search filters on status.
-- tsv is generated: breadcrumb weighted A, chunk text weighted B.
CREATE TABLE IF NOT EXISTS kb_chunks (
    chunk_id         bigserial PRIMARY KEY,
    doc_path         text NOT NULL REFERENCES kb_documents (doc_path) ON DELETE CASCADE,
    chunk_index      integer NOT NULL,             -- order within document
    parent_chunk_id  bigint REFERENCES kb_chunks (chunk_id) ON DELETE SET NULL,  -- small-to-big
    breadcrumb       text NOT NULL,                -- e.g. "POS Scanner > Diagnosis > Step 2"
    text             text NOT NULL,
    metadata         jsonb NOT NULL DEFAULT '{}'::jsonb,  -- category, systems, versions, audience, section_type
    status           text NOT NULL DEFAULT 'approved' CHECK (status IN ('approved', 'draft')),
    content_hash     text NOT NULL,                -- sha256 of breadcrumb + text; drives re-embedding
    embedding        vector(1536),
    embedding_model  text,
    embedded_at      timestamptz,
    tsv              tsvector GENERATED ALWAYS AS (
                         setweight(to_tsvector('english', coalesce(breadcrumb, '')), 'A') ||
                         setweight(to_tsvector('english', coalesce(text, '')), 'B')
                     ) STORED,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (doc_path, chunk_index)
);
CREATE INDEX IF NOT EXISTS kb_chunks_tsv_idx       ON kb_chunks USING gin (tsv);
CREATE INDEX IF NOT EXISTS kb_chunks_metadata_idx  ON kb_chunks USING gin (metadata jsonb_path_ops);
CREATE INDEX IF NOT EXISTS kb_chunks_status_idx    ON kb_chunks (status);
CREATE INDEX IF NOT EXISTS kb_chunks_hash_idx      ON kb_chunks (content_hash);
CREATE INDEX IF NOT EXISTS kb_chunks_embedding_idx ON kb_chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS error_codes (
    error_code       text NOT NULL,                -- e.g. 0x8004, E04, LSY-4002
    system           text NOT NULL DEFAULT '',     -- '' when not tied to a system
    meaning          text,
    doc_path         text REFERENCES kb_documents (doc_path) ON DELETE CASCADE,
    source_chunk_id  bigint REFERENCES kb_chunks (chunk_id) ON DELETE SET NULL,
    loaded_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (error_code, system)
);
CREATE INDEX IF NOT EXISTS error_codes_code_upper_idx ON error_codes (upper(error_code));

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id            bigserial PRIMARY KEY,
    started_at        timestamptz NOT NULL DEFAULT now(),
    finished_at       timestamptz,
    status            text NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed')),
    embedding_model   text,
    embedding_dims    integer,
    doc_hashes        jsonb NOT NULL DEFAULT '{}'::jsonb,   -- {doc_path: sha256}
    docs_seen         integer NOT NULL DEFAULT 0,
    chunks_total      integer NOT NULL DEFAULT 0,
    chunks_embedded   integer NOT NULL DEFAULT 0,
    chunks_unchanged  integer NOT NULL DEFAULT 0,
    chunks_deleted    integer NOT NULL DEFAULT 0,
    notes             text
);
