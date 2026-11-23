"""Panel rehearsal: a 10-ticket held-out CSV through the batch path, checked against the held-out eval.

    python scripts/heldout_rehearsal.py make       # -> incoming/heldout-rehearsal/tickets.csv (gitignored)
    ax15 run-batch incoming/heldout-rehearsal      # agent 2.0, eval mode
    python scripts/heldout_rehearsal.py compare    # batch report vs outputs/agent_eval_heldout_v2.json
    ax15 clear-batch heldout-rehearsal             # remove the copies again

The CSV is written the way a panel might send it: the train JSON keys as columns, list cells as JSON strings,
plus the label columns (correct_routing, should_escalate, relevant_kb_docs) and chaos_type from the index, so
the run also shows that the batch path strips them from agent input and scores with them afterwards.
Tickets are the first 10 held-out tickets (by id, INC-00162 excluded) at 10 different stores: the agent
counts recent tickets at the store, so two copies at one store would change each other's input.
"""
from __future__ import annotations

import csv
import json
import sys

from ax15.settings import OUTPUTS_DIR, ROOT, dataset_dir

NAME = "heldout-rehearsal"
FOLDER = ROOT / "incoming" / NAME
N = 10
EXCLUDE = {"INC-00162"}
COLS = ["ticket_id", "store_id", "store_name", "submitted_by", "contact_phone", "date_opened", "priority", "category",
        "subcategory", "subject", "description", "asset_id", "system_version", "tags", "escalation_flag",
        "related_tickets", "ticket_history", "resolution_notes", "status",
        "chaos_type", "correct_routing", "should_escalate", "relevant_kb_docs"]


def pick() -> list[dict]:
    ds = dataset_dir()
    held = sorted(set(json.loads((ROOT / "eval" / "splits.json").read_text(encoding="utf-8"))["heldout"]) - EXCLUDE)
    out, stores = [], set()
    for tid in held:
        t = json.loads((ds / "tickets" / "train" / f"{tid}.json").read_text(encoding="utf-8"))
        if t.get("store_id") in stores:
            continue
        stores.add(t.get("store_id"))
        out.append(t)
        if len(out) == N:
            break
    return out


def make() -> int:
    ds = dataset_dir()
    labels = {x["ticket_id"]: x for x in json.loads((ds / "labels" / "train_labels.json").read_text(encoding="utf-8"))}
    with (ds / "tickets" / "train_index.csv").open(encoding="utf-8") as f:
        chaos = {r["ticket_id"]: r.get("chaos_type") or "" for r in csv.DictReader(f)}
    FOLDER.mkdir(parents=True, exist_ok=True)
    out = FOLDER / "tickets.csv"
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for t in pick():
            lab = labels[t["ticket_id"]]
            row = {c: t.get(c) for c in COLS}
            for c in ("tags", "related_tickets", "ticket_history"):
                row[c] = json.dumps(t.get(c) or [], ensure_ascii=False)
            row.update(chaos_type=chaos.get(t["ticket_id"], ""), correct_routing=lab["correct_routing"],
                       should_escalate=lab["should_escalate"], relevant_kb_docs=json.dumps(lab["relevant_kb_docs"]))
            w.writerow({k: ("" if v is None else v) for k, v in row.items()})
    print(f"[rehearsal] wrote {out} ({N} held-out tickets at {N} stores)")
    return 0


def compare() -> int:
    held = json.loads((OUTPUTS_DIR / "agent_eval_heldout_v2.json").read_text(encoding="utf-8"))["dev"]
    rep = OUTPUTS_DIR / f"batch_{NAME}_report.csv"
    rows = list(csv.DictReader(rep.open(encoding="utf-8")))
    L = ["| Ticket | Held-out eval (2.0) | Batch run | Escalate (eval → batch) | Match |", "|---|---|---|---|---|"]
    same = 0
    for r in rows:
        h = held.get(r["ticket_id"]) or {}
        got = r["verify_rules_routing"] or r["routing"]
        esc = str(r["escalate"]) == "True"
        ok = got == h.get("routing") and esc == bool(h.get("should_escalate"))
        same += ok
        L.append(f"| {r['ticket_id']} | {h.get('routing')} | {got} | {h.get('should_escalate')} → {esc} | "
                 f"{'yes' if ok else '**no**'} |")
    L.insert(0, f"Routing + escalation match the held-out eval on **{same} / {len(rows)}** tickets.\n")
    out = OUTPUTS_DIR / f"batch_{NAME}_vs_heldout.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    return 0 if same == len(rows) else 1


if __name__ == "__main__":
    sys.exit({"make": make, "compare": compare}[sys.argv[1] if len(sys.argv) > 1 else "make"]())
