-- 008: contextual chunks. Each chunk gets a stored context prefix
-- ("<doc title> — <one-line doc summary>") that is embedded and full-text indexed
-- with the chunk, while kb_chunks.text stays the original text the bot displays.

ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS summary text;
ALTER TABLE kb_chunks    ADD COLUMN IF NOT EXISTS context_prefix text NOT NULL DEFAULT '';

-- Rebuild the generated tsv only if it does not already include context_prefix
-- (a generated column's expression cannot be altered in place).
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_attrdef d
        JOIN pg_attribute a ON a.attrelid = d.adrelid AND a.attnum = d.adnum
        WHERE d.adrelid = 'public.kb_chunks'::regclass AND a.attname = 'tsv'
          AND pg_get_expr(d.adbin, d.adrelid) LIKE '%context_prefix%'
    ) THEN
        ALTER TABLE kb_chunks DROP COLUMN IF EXISTS tsv;
        ALTER TABLE kb_chunks ADD COLUMN IF NOT EXISTS tsv tsvector GENERATED ALWAYS AS (
            setweight(to_tsvector('english', coalesce(breadcrumb, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(text, '')), 'B') ||
            setweight(to_tsvector('english', coalesce(context_prefix, '')), 'C')
        ) STORED;
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS kb_chunks_tsv_idx ON kb_chunks USING gin (tsv);

-- Category filter for search_kb (set by the 2.0 classifier).
CREATE INDEX IF NOT EXISTS kb_chunks_category_idx ON kb_chunks ((metadata->>'category'));
