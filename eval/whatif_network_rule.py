"""Offline what-if (no LLM calls): what would the proposed SOP-PROPOSED-NET rule change?

    python eval/whatif_network_rule.py      # -> outputs/whatif_network_rule.md

Re-evaluates the SOP rules over the facts cached by the frozen 2.0 runs (decide-stage facts in
outputs/agent_eval_cache/v2/<ticket>_<prompt hash>.json), once as configured (rule inactive) and once with
the proposed rule switched on, for the dev split and the held-out split. Only the routing decision is
re-derived (escalate -> l2_escalation, l1 -> l1_*, clarify, non_it; verify -> the rules' routing class,
information only), so the numbers isolate the rule's effect. The rule stays inactive in the agent.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from ax15 import db
from ax15.agents import v2_graph
from ax15.settings import OUTPUTS_DIR, ROOT
from ax15.trust import sop_rules

CACHE = OUTPUTS_DIR / "agent_eval_cache" / "v2"
SPLIT_SQL = ("SELECT t.ticket_id, s.split, l.correct_routing, l.should_escalate FROM eval.eval_splits s"
             " JOIN tickets t USING (ticket_uid) JOIN eval.eval_labels l USING (ticket_uid) WHERE t.batch_id = 'train'")
EXCLUDE = ("INC-00162",)
ROUTE = {"escalate": "l2_escalation", "clarify": "needs_clarification", "non_it": "non_it", "l1": "l1_guided"}


def routing_of(res: dict) -> tuple[str, bool]:
    d = res["decision"]
    if d == "verify":
        return res["routing_hint"] or ROUTE.get(res["verify_decision"], "l1_guided"), False
    return res["routing_hint"] or ROUTE.get(d, "l1_guided"), d == "escalate"


def collapse(r: str | None) -> str:
    return "l1" if (r or "").startswith("l1_") else ("l2" if r == "l2_escalation" else "other")


def score(rows: list[tuple[str, bool, str, bool]]) -> dict:
    n = len(rows) or 1
    tp = sum(p and a for _, p, _, a in rows)
    pred = sum(p for _, p, _, _ in rows)
    act = sum(a for _, _, _, a in rows)
    return {"n": len(rows), "routing": sum(collapse(r) == collapse(lab) for r, _, lab, _ in rows) / n,
            "precision": tp / pred if pred else None, "recall": tp / act if act else None, "esc": pred}


def main() -> int:
    ph = v2_graph.prompt_hash()
    conn, _ = db.connect(autocommit=True)
    labels = {r[0]: (r[1], r[2], r[3]) for r in conn.execute(SPLIT_SQL).fetchall()}
    sets: dict[str, list] = {"dev": [], "heldout": []}
    missing = {"dev": 0, "heldout": 0}
    for tid, (split, lab, esc) in sorted(labels.items()):
        if split not in sets or tid in EXCLUDE:
            continue
        p = CACHE / f"{tid}_{ph}.json"
        facts = json.loads(p.read_text(encoding="utf-8")).get("facts") if p.exists() else None
        if not facts:
            missing[split] += 1
            continue
        base = sop_rules.evaluate(facts)
        prop = sop_rules.evaluate(facts, include_proposed=True)
        sets[split].append((tid, facts, base, prop, lab, esc))

    L = ["# What-if: proposed rule SOP-PROPOSED-NET (offline, no LLM calls)", "",
         "Rule (proposed for ServeWell's SOP, **inactive** in AX-15 2.0): *network issue affecting POS during service "
         "→ L2* = `network_issue` AND `pos_affected` AND `during_service` (customer-facing or peak hour), all derived "
         "in code from the cached classify facts.", "",
         f"Facts from the frozen 2.0 runs (prompt hash `{ph}`). Tickets without cached facts: dev {missing['dev']}, "
         f"held-out {missing['heldout']}.", "",
         "| Split | n | Routing L1/L2: as frozen → with rule | Esc. precision | Esc. recall | Escalations | Tickets changed |",
         "|---|---:|---|---|---|---|---:|"]
    changed_rows = []
    for split, rows in sets.items():
        if not rows:
            L.append(f"| {split} | 0 | — | — | — | — | — |")
            continue
        b = score([(*routing_of(base), lab, esc) for _, _, base, _, lab, esc in rows])
        q = score([(*routing_of(prop), lab, esc) for _, _, _, prop, lab, esc in rows])
        ch = [(split, tid, routing_of(base)[0], routing_of(prop)[0], lab) for tid, _, base, prop, lab, _ in rows
              if routing_of(base) != routing_of(prop)]
        changed_rows += ch
        f = lambda x: "—" if x is None else f"{x:.1%}"  # noqa: E731
        L.append(f"| {split} | {b['n']} | {f(b['routing'])} → **{f(q['routing'])}** | {f(b['precision'])} → "
                 f"{f(q['precision'])} | {f(b['recall'])} → **{f(q['recall'])}** | {b['esc']} → {q['esc']} | {len(ch)} |")
    good = sum(collapse(new) == collapse(lab) for *_, new, lab in changed_rows)
    L += ["", f"Of the {len(changed_rows)} tickets the rule changes, {good} move to the labelled class and "
          f"{len(changed_rows) - good} move away from it.", "",
          "| Split | Ticket | As frozen | With rule | Label |", "|---|---|---|---|---|"]
    L += [f"| {s} | {t} | {a} | {b_} | {lab} |" for s, t, a, b_, lab in changed_rows]
    L += ["", "Baseline here is the rules re-evaluated over the cached facts (it matches the frozen answers except "
          "where the guard escalated an answer after the rules ran).", ""]
    out = OUTPUTS_DIR / "whatif_network_rule.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"[whatif] wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
