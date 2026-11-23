"""Minimal SQL migration runner.

Usage (from the project root):
    python -m ax15.migrate              # apply pending migrations
    python -m ax15.migrate --status     # show applied / pending, change nothing
    python -m ax15.migrate --reapply    # re-execute every file (proves the SQL is idempotent)

Applies `migrations/NNN_name.sql` in numeric order, one transaction per file, and
records each applied version in `schema_migrations`. Connects with
DATABASE_URL_DIRECT, falling back to the pooled DATABASE_URL if the direct
connection fails. Connection strings are never printed.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import psycopg

from ax15 import db
from ax15.settings import ROOT

MIGRATIONS_DIR = ROOT / "migrations"
FILENAME_RE = re.compile(r"^(\d{3})_([a-z0-9_]+)\.sql$")
# Arbitrary constant: serialises concurrent runners (transaction-scoped, so safe over a pooler).
LOCK_KEY = 15_0001

BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version       integer PRIMARY KEY,
    name          text NOT NULL,
    checksum      text NOT NULL,
    applied_at    timestamptz NOT NULL DEFAULT now(),
    execution_ms  integer NOT NULL
)
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Return migrations sorted by version. Non-matching files (e.g. README.md) are ignored."""
    found: dict[int, Migration] = {}
    for path in sorted(directory.glob("*.sql")):
        m = FILENAME_RE.match(path.name)
        if not m:
            raise ValueError(f"Bad migration filename {path.name!r}; expected NNN_lower_snake.sql")
        version = int(m.group(1))
        if version in found:
            raise ValueError(f"Duplicate migration version {version:03d}: {found[version].path.name}, {path.name}")
        found[version] = Migration(version, m.group(2), path, path.read_text(encoding="utf-8"))
    return [found[v] for v in sorted(found)]


def connect() -> tuple[psycopg.Connection, str]:
    return db.connect(db.MIGRATION_VARS)


def applied_versions(conn: psycopg.Connection) -> dict[int, str]:
    rows = conn.execute("SELECT version, checksum FROM schema_migrations").fetchall()
    return {v: c for v, c in rows}


def run(status_only: bool = False, reapply: bool = False, directory: Path = MIGRATIONS_DIR) -> int:
    migrations = discover(directory)
    conn, var = connect()
    print(f"[migrate] connected via {var}; {len(migrations)} migration file(s) in {directory.name}/")
    with conn:
        with conn.transaction():
            conn.execute(BOOTSTRAP_SQL)
        done = applied_versions(conn)

        changed = 0
        for mig in migrations:
            label = f"{mig.version:03d}_{mig.name}"
            already = mig.version in done
            if already and done[mig.version] != mig.checksum:
                print(f"[migrate] WARNING {label}: file changed since it was applied (checksum differs)")

            if status_only:
                print(f"  {label:<28} {'applied' if already else 'PENDING'}")
                continue
            if already and not reapply:
                print(f"  {label:<28} skip (already applied)")
                continue

            start = time.perf_counter()
            with conn.transaction():
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
                # Re-check inside the lock in case another runner applied it meanwhile.
                exists = conn.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = %s", (mig.version,)
                ).fetchone()
                if exists and not reapply:
                    print(f"  {label:<28} skip (applied concurrently)")
                    continue
                conn.execute(mig.sql)  # no params -> multi-statement script allowed
                ms = int((time.perf_counter() - start) * 1000)
                if not exists:
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name, checksum, execution_ms)"
                        " VALUES (%s, %s, %s, %s)",
                        (mig.version, mig.name, mig.checksum, ms),
                    )
                else:   # re-applied: the current file is what is now in effect
                    conn.execute("UPDATE schema_migrations SET checksum = %s WHERE version = %s",
                                 (mig.checksum, mig.version))
            changed += 1
            print(f"  {label:<28} {'re-applied' if exists else 'applied'} ({ms} ms)")

    if not status_only:
        print(f"[migrate] done: {changed} executed, {len(migrations) - changed} skipped")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m ax15.migrate", description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group()
    g.add_argument("--status", action="store_true", help="show applied/pending migrations only")
    g.add_argument("--reapply", action="store_true", help="re-execute every migration (idempotency check)")
    args = p.parse_args(argv)
    try:
        return run(status_only=args.status, reapply=args.reapply)
    except psycopg.Error as e:
        print(f"[migrate] FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
