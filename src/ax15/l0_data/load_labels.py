"""L0-4 (part): load the 220 train labels into eval.eval_labels.

    python -m ax15.l0_data.load_labels

Labels are evaluation-only (no label leakage into prompts): they live in schema `eval` and are never
passed to an agent or LLM prompt. Each label is joined to its ticket via
tickets.ticket_uid, resolved from (source='train', batch_id='train', ticket_id).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ax15 import db
from ax15.settings import dataset_dir

LABELS_FILE = "labels/train_labels.json"


class TrainLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ticket_id: str
    correct_routing: str
    relevant_kb_docs: list[str]
    should_escalate: bool
    escalation_reason: str | None = None
    correct_resolution_summary: str | None = None
    expected_agent_action: str | None = None
    chaos_correct_behavior: str | None = None
    evaluator_notes: str | None = None


def read_labels(root: Path | None = None) -> list[tuple[TrainLabel, dict]]:
    raw = json.loads(((root or dataset_dir()) / LABELS_FILE).read_text(encoding="utf-8"))
    return [(TrainLabel.model_validate(r), r) for r in raw]


def load_labels(conn, labels: list[tuple[TrainLabel, dict]]) -> int:
    uid = dict(conn.execute(
        "SELECT ticket_id, ticket_uid FROM tickets WHERE source = 'train' AND batch_id = 'train'"
    ).fetchall())
    missing = sorted(lab.ticket_id for lab, _ in labels if lab.ticket_id not in uid)
    if missing:
        raise SystemExit(f"[labels] {len(missing)} labels have no train ticket (run load_train first): {missing[:5]}")
    cols = list(TrainLabel.model_fields)[1:]
    sql = (
        f"INSERT INTO eval.eval_labels (ticket_uid, {', '.join(cols)}, raw)"
        f" VALUES (%s, {', '.join(['%s'] * len(cols))}, %s::jsonb)"
        " ON CONFLICT (ticket_uid) DO UPDATE SET "
        + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols)
        + ", raw = EXCLUDED.raw, loaded_at = now()"
    )
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(sql, [
            (uid[lab.ticket_id], *(getattr(lab, c) for c in cols), json.dumps(raw)) for lab, raw in labels
        ])
    return conn.execute("SELECT count(*) FROM eval.eval_labels").fetchone()[0]


def main() -> int:
    labels = read_labels()
    conn, var = db.connect()
    with conn:
        n = load_labels(conn, labels)
    print(f"[labels] connected via {var}; read {len(labels)}, eval.eval_labels now has {n} rows")
    return 0 if n == len(labels) else 1


if __name__ == "__main__":
    sys.exit(main())
