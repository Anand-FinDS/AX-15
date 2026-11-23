"""L0-2 reference loaders: stores, assets, SLA matrix, version matrix.

    python -m ax15.l0_data.reference

1. Verifies every file under DATASET_DIR against the source_files baseline.
2. Validates the reference CSVs with pydantic and upserts them (idempotent).
3. Writes outputs/data_quality_report.md.
"""
from __future__ import annotations

import csv
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import TypeVar

import psycopg
from pydantic import BaseModel, ValidationError

from ax15 import db
from ax15.l0_data.models import Asset, SlaRow, Store, VersionRow
from ax15.l0_data.source_files import verify_and_record
from ax15.settings import OUTPUTS_DIR, dataset_dir

M = TypeVar("M", bound=BaseModel)

SOURCES = {
    "stores": ("data/stores.csv", Store),
    "assets": ("data/assets.csv", Asset),
    "sla_matrix": ("data/sla_matrix.csv", SlaRow),
    "version_matrix": ("kb/system-specs/version-matrix.csv", VersionRow),
}
KEYS = {
    "stores": ("store_id",),
    "assets": ("asset_id",),
    "sla_matrix": ("priority", "category"),
    "version_matrix": ("system", "version"),
}


class ReferenceDataError(ValueError):
    pass


def read_csv(rel_path: str, model: type[M], root: Path | None = None) -> list[M]:
    """Parse and validate a CSV. Collects every bad row, then raises once."""
    path = (root or dataset_dir()) / rel_path
    rows: list[M] = []
    errors: list[str] = []
    with path.open(encoding="utf-8-sig", newline="") as f:
        for line_no, raw in enumerate(csv.DictReader(f), start=2):
            try:
                rows.append(model.model_validate(raw))
            except ValidationError as e:
                errors.append(f"{rel_path}:{line_no}: {e.errors()[0]['loc']} {e.errors()[0]['msg']}")
    if errors:
        raise ReferenceDataError(f"{len(errors)} invalid row(s):\n  " + "\n  ".join(errors[:20]))
    return rows


def read_all(root: Path | None = None) -> dict[str, list[BaseModel]]:
    data = {name: read_csv(rel, model, root) for name, (rel, model) in SOURCES.items()}
    for name, rows in data.items():
        keys = [tuple(getattr(r, k) for k in KEYS[name]) for r in rows]
        dupes = {k for k in keys if keys.count(k) > 1}
        if dupes:
            raise ReferenceDataError(f"{name}: duplicate keys {sorted(dupes)[:5]}")
    store_ids = {s.store_id for s in data["stores"]}
    orphans = sorted({a.store_id for a in data["assets"]} - store_ids)
    if orphans:
        raise ReferenceDataError(f"assets reference unknown stores: {orphans[:10]}")
    return data


def _upsert(conn: psycopg.Connection, table: str, rows: Iterable[BaseModel]) -> int:
    rows = list(rows)
    if not rows:
        return 0
    cols = list(type(rows[0]).model_fields)
    keys = KEYS[table]
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in cols if c not in keys)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})"
        f" ON CONFLICT ({', '.join(keys)}) DO UPDATE SET {updates}, loaded_at = now()"
    )
    with conn.cursor() as cur:
        cur.executemany(sql, [tuple(getattr(r, c) for c in cols) for r in rows])
    return len(rows)


def load_reference(conn: psycopg.Connection, data: dict[str, list[BaseModel]]) -> dict[str, int]:
    """Upsert all reference tables in one transaction (stores before assets for the FK)."""
    with conn.transaction():
        for table in ("stores", "assets", "sla_matrix", "version_matrix"):
            _upsert(conn, table, data[table])
    return {
        t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
        for t in ("stores", "assets", "sla_matrix", "version_matrix")
    }


def main() -> int:
    from ax15.l0_data.quality import build_report  # local import keeps module import light

    conn, var = db.connect()
    print(f"[reference] connected via {var}")
    with conn:
        integrity = verify_and_record(conn)
        print(f"[reference] source integrity: {integrity.summary()}")
        for rel in integrity.changed + integrity.missing:
            print(f"  ! {rel}", file=sys.stderr)

        data = read_all()
        counts = load_reference(conn, data)
        for t, n in counts.items():
            print(f"[reference] {t:<15} {n} rows")

        report = build_report(data, integrity, counts)
    OUTPUTS_DIR.mkdir(exist_ok=True)
    out = OUTPUTS_DIR / "data_quality_report.md"
    out.write_text(report, encoding="utf-8")
    print(f"[reference] wrote {out.relative_to(OUTPUTS_DIR.parent)}")
    return 0 if integrity.ok else 2


if __name__ == "__main__":
    sys.exit(main())
