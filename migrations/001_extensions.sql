-- 001: extensions and schemas.
-- One Postgres for everything (L0 decision 1). Evaluation data lives in its own
-- schema `eval` so agent-facing code can be kept away from labels by
-- search_path / role grants (no label leakage into prompts).

CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS eval;
