"""L0-1 acceptance: migration discovery (offline) + schema present on the database (online)."""
from __future__ import annotations

import re

import pytest

from ax15 import db as db_mod
from ax15 import migrate

EXPECTED_TABLES = {
    ("public", "schema_migrations"),
    ("public", "stores"),
    ("public", "assets"),
    ("public", "sla_matrix"),
    ("public", "version_matrix"),
    ("public", "error_codes"),
    ("public", "batch_runs"),
    ("public", "tickets"),
    ("public", "ticket_history"),
    ("public", "kb_documents"),
    ("public", "kb_chunks"),
    ("public", "ingestion_runs"),
    ("public", "action_log"),
    ("public", "source_files"),
    ("eval", "eval_labels"),
    ("eval", "chaos_labels"),
    ("eval", "eval_splits"),
}


def test_discover_orders_and_validates(tmp_path):
    (tmp_path / "002_b.sql").write_text("SELECT 2;")
    (tmp_path / "001_a.sql").write_text("SELECT 1;")
    (tmp_path / "README.md").write_text("ignored")
    assert [m.version for m in migrate.discover(tmp_path)] == [1, 2]

    (tmp_path / "002_dup.sql").write_text("SELECT 3;")
    with pytest.raises(ValueError, match="Duplicate"):
        migrate.discover(tmp_path)


def test_discover_rejects_bad_name(tmp_path):
    (tmp_path / "1_bad.sql").write_text("SELECT 1;")
    with pytest.raises(ValueError, match="Bad migration filename"):
        migrate.discover(tmp_path)


def test_repo_migrations_are_guarded():
    """Every CREATE in the repo's migrations must be idempotent."""
    for m in migrate.discover():
        for line in m.sql.upper().splitlines():
            s = line.strip()
            if s.startswith("CREATE ") and not s.startswith("CREATE OR REPLACE"):
                assert "IF NOT EXISTS" in s, f"{m.path.name}: unguarded statement: {line.strip()}"
            if s.startswith("ALTER TABLE") and " ADD COLUMN " in s:
                assert "ADD COLUMN IF NOT EXISTS" in s, f"{m.path.name}: unguarded ADD COLUMN: {line.strip()}"
        # Every ADD CONSTRAINT must be preceded by a DROP CONSTRAINT IF EXISTS of the same name.
        sql = m.sql.lower()
        for name in re.findall(r"add\s+constraint\s+(\w+)", sql):
            assert re.search(rf"drop\s+constraint\s+if\s+exists\s+{name}\b", sql), (
                f"{m.path.name}: ADD CONSTRAINT {name} without DROP CONSTRAINT IF EXISTS"
            )


@pytest.fixture(scope="module")
def db():
    if not db_mod.configured():
        pytest.skip("no database configured")
    conn, _ = migrate.connect()
    yield conn
    conn.close()


@pytest.mark.db
def test_all_migrations_applied(db):
    applied = {r[0] for r in db.execute("SELECT version FROM schema_migrations")}
    assert {m.version for m in migrate.discover()} <= applied


@pytest.mark.db
def test_expected_tables_exist(db):
    rows = db.execute(
        "SELECT table_schema, table_name FROM information_schema.tables"
        " WHERE table_schema IN ('public', 'eval') AND table_type = 'BASE TABLE'"
    ).fetchall()
    assert EXPECTED_TABLES <= set(rows)


@pytest.mark.db
def test_ticket_identity(db):
    """ticket_uid is an always-identity PK; ticket_id is unique per (source, batch_id)."""
    ident = db.execute(
        "SELECT is_identity, identity_generation FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = 'tickets' AND column_name = 'ticket_uid'"
    ).fetchone()
    assert ident == ("YES", "ALWAYS")
    nullable = db.execute(
        "SELECT is_nullable FROM information_schema.columns"
        " WHERE table_schema = 'public' AND table_name = 'tickets' AND column_name = 'batch_id'"
    ).fetchone()[0]
    assert nullable == "NO"
    uniq = db.execute(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
        " WHERE conname = 'tickets_source_batch_ticket_key'"
    ).fetchone()[0]
    assert uniq == "UNIQUE (source, batch_id, ticket_id)"
    fks = {
        r[0]
        for r in db.execute(
            "SELECT conrelid::regclass::text FROM pg_constraint"
            " WHERE contype = 'f' AND confrelid = 'public.tickets'::regclass"
            " AND pg_get_constraintdef(oid) LIKE 'FOREIGN KEY (ticket_uid)%'"
        )
    }
    assert {"ticket_history", "action_log", "eval.eval_labels", "eval.chaos_labels", "eval.eval_splits"} <= fks


@pytest.mark.db
def test_kb_chunks_vector_and_tsvector(db):
    rows = dict(db.execute(
        "SELECT a.attname, format_type(a.atttypid, a.atttypmod) FROM pg_attribute a"
        " WHERE a.attrelid = 'public.kb_chunks'::regclass AND a.attnum > 0 AND NOT a.attisdropped"
    ).fetchall())
    assert rows["embedding"] == "vector(1536)"
    assert rows["tsv"] == "tsvector"
    gen = db.execute(
        "SELECT is_generated FROM information_schema.columns"
        " WHERE table_name = 'kb_chunks' AND column_name = 'tsv'"
    ).fetchone()[0]
    assert gen == "ALWAYS"


@pytest.mark.db
def test_chaos_labels_expectation_columns(db):
    cols = dict(db.execute(
        "SELECT column_name, data_type FROM information_schema.columns"
        " WHERE table_schema = 'eval' AND table_name = 'chaos_labels'"
    ).fetchall())
    assert cols["expected_routing"] == "ARRAY"
    assert cols["expected_mode"] == "text" and cols["confidence"] == "text"
    checks = {r[0] for r in db.execute(
        "SELECT conname FROM pg_constraint WHERE conrelid = 'eval.chaos_labels'::regclass AND contype = 'c'"
    )}
    assert {"chaos_labels_expected_routing_check", "chaos_labels_expected_mode_check",
            "chaos_labels_confidence_check"} <= checks
