-- 009: allow source = 'ui' for tickets created in the Streamlit console (batch_id 'ui-<YYYYMMDD>').
-- Train/eval queries filter on batch_id = 'train', so UI tickets never mix into them.

ALTER TABLE tickets DROP CONSTRAINT IF EXISTS tickets_source_check;
ALTER TABLE tickets ADD CONSTRAINT tickets_source_check
    CHECK (source IN ('train', 'panel_test', 'manual', 'ui'));

ALTER TABLE batch_runs DROP CONSTRAINT IF EXISTS batch_runs_source_check;
ALTER TABLE batch_runs ADD CONSTRAINT batch_runs_source_check
    CHECK (source IN ('train', 'panel_test', 'manual', 'ui'));
