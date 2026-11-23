"""L0-3: normalise and load the training tickets.

    python -m ax15.l0_data.load_train

Globs tickets/train/*.json under DATASET_DIR (the index CSV's raw/
paths are ignored, and the index itself is never read, so chaos_type cannot leak).
Loads with source='train', batch_id='train' and writes outputs/train_load_report.md.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from ax15 import db
from ax15.l0_data.normalise import Normalised, TicketValidationError, from_json
from ax15.l0_data.tickets_db import finish_batch_run, load_tickets, upsert_batch_run
from ax15.settings import OUTPUTS_DIR, ROOT, dataset_dir

BATCH_ID = "train"


def normalise_train(root: Path | None = None) -> tuple[list[Normalised], list[dict]]:
    files = sorted(((root or dataset_dir()) / "tickets" / "train").glob("*.json"))
    ok, errors = [], []
    for p in files:
        try:
            ok.append(from_json(json.loads(p.read_text(encoding="utf-8")), source="train", batch_id=BATCH_ID))
        except (json.JSONDecodeError, TicketValidationError) as e:
            errors.append({"file": p.name, "error": str(e)})
    return ok, errors


def render_report(items: list[Normalised], errors: list[dict], counts: dict[str, int]) -> str:
    L = ["# AX-15 — Train ticket load (L0-3)\n",
         f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC; source=train, batch_id=train._\n",
         "| | Count |", "|---|---:|",
         f"| Files read | {len(items) + len(errors)} |",
         f"| Tickets loaded (DB) | {counts['tickets']} |",
         f"| History rows (DB) | {counts['history']} |",
         f"| Rejected files | {len(errors)} |", ""]
    leaky = [n for n in items if n.leaks.any]
    L.append(f"## Leakage removed ({len(leaky)} tickets)\n")
    if leaky:
        L += ["| Ticket | Field | Removed |", "|---|---|---|"]
        for n in leaky:
            for k in n.leaks.keys_dropped:
                L.append(f"| {n.ticket.ticket_id} | `{k}` (key) | whole field |")
            for path, sentence in n.leaks.sentences_removed:
                L.append(f"| {n.ticket.ticket_id} | `{path}` | {sentence.replace('|', '/')} |")
    L.append("")
    warned = [n for n in items if n.warnings]
    L.append(f"## Normaliser warnings ({len(warned)} tickets)\n")
    for n in warned:
        L.append(f"- {n.ticket.ticket_id}: " + "; ".join(n.warnings))
    if errors:
        L.append("\n## Rejected files\n")
        L += [f"- `{e['file']}`: {e['error']}" for e in errors]
    return "\n".join(L) + "\n"


def main() -> int:
    items, errors = normalise_train()
    conn, var = db.connect()
    print(f"[train] connected via {var}; {len(items)} normalised, {len(errors)} rejected")
    out = OUTPUTS_DIR / "train_load_report.md"
    with conn:
        with conn.transaction():
            upsert_batch_run(conn, BATCH_ID, "train", "train")
            load_tickets(conn, [n.ticket for n in items])
            finish_batch_run(
                conn, BATCH_ID, total=len(items) + len(errors), loaded=len(items),
                rejected=len(errors), errors=errors, report_path=out.relative_to(ROOT).as_posix(),
            )
        counts = {
            "tickets": conn.execute(
                "SELECT count(*) FROM tickets WHERE source = 'train' AND batch_id = %s", (BATCH_ID,)
            ).fetchone()[0],
            "history": conn.execute(
                "SELECT count(*) FROM ticket_history h JOIN tickets t USING (ticket_uid)"
                " WHERE t.source = 'train' AND t.batch_id = %s", (BATCH_ID,)
            ).fetchone()[0],
        }
    for n in items:
        for path, sentence in n.leaks.sentences_removed:
            print(f"[train] leakage removed: {n.ticket.ticket_id} {path}: {sentence!r}")
    print(f"[train] tickets={counts['tickets']} history={counts['history']}")
    OUTPUTS_DIR.mkdir(exist_ok=True)
    out.write_text(render_report(items, errors, counts), encoding="utf-8")
    print(f"[train] wrote {out.relative_to(ROOT)}")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
