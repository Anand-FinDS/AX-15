-- 007: richer chaos-label expectations + source-file integrity manifest.

-- eval.chaos_labels: expected outcome fields (from eval/chaos_expected_behavior.md).
-- expected_routing is an array because some chaos tickets accept more than one routing.
ALTER TABLE eval.chaos_labels ADD COLUMN IF NOT EXISTS expected_routing text[] NOT NULL DEFAULT '{}';
ALTER TABLE eval.chaos_labels ADD COLUMN IF NOT EXISTS expected_mode    text;
ALTER TABLE eval.chaos_labels ADD COLUMN IF NOT EXISTS confidence       text;

-- Named constraints, dropped and re-added so the file can be re-run safely.
ALTER TABLE eval.chaos_labels DROP CONSTRAINT IF EXISTS chaos_labels_expected_routing_check;
ALTER TABLE eval.chaos_labels ADD  CONSTRAINT chaos_labels_expected_routing_check
    CHECK (expected_routing <@ ARRAY['l1_self_service', 'l1_guided', 'l2_escalation',
                                     'non_it', 'needs_clarification']::text[]);

ALTER TABLE eval.chaos_labels DROP CONSTRAINT IF EXISTS chaos_labels_expected_mode_check;
ALTER TABLE eval.chaos_labels ADD  CONSTRAINT chaos_labels_expected_mode_check
    CHECK (expected_mode IN ('handle', 'verify'));

ALTER TABLE eval.chaos_labels DROP CONSTRAINT IF EXISTS chaos_labels_confidence_check;
ALTER TABLE eval.chaos_labels ADD  CONSTRAINT chaos_labels_confidence_check
    CHECK (confidence IN ('H', 'M', 'L'));

-- Integrity manifest for the read-only original dataset (the original dataset is read-only).
-- The baseline hash is written once when a file is first seen; later runs only
-- bump last_verified_at and never overwrite sha256, so a changed file stays detectable.
CREATE TABLE IF NOT EXISTS public.source_files (
    rel_path          text PRIMARY KEY,            -- relative to DATASET_DIR, forward slashes
    sha256            text NOT NULL,
    size_bytes        bigint,
    first_seen_at     timestamptz NOT NULL DEFAULT now(),
    last_verified_at  timestamptz
);
