# Migrations

Numbered, idempotent SQL files applied in order (e.g. `001_extensions.sql`, `002_reference.sql`).
Applied with `DATABASE_URL_DIRECT` (fallback: pooled `DATABASE_URL`). First migration: task L0-1.
