"""Held-out evaluation: AX-15 1.0 and 2.0 (frozen) on the held-out split, once.

    python eval/heldout_eval.py            # -> outputs/heldout_eval.md (+ agent_eval_heldout_v1/v2.json)

The 44 held-out tickets minus INC-00162 (used in 1.0 smoke tests) = 43. Run only after 2.0 is frozen
(before the safety rules); nothing in the agents changes because of these numbers. Same runner, cache, scoring
and metrics as eval/agent_eval.py (dev); labels are joined after the runs.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from ax15.settings import OUTPUTS_DIR, ROOT
from ax15.trust import grounding, spend

EVAL_DIR = Path(__file__).resolve().parent
EXCLUDE = ("INC-00162",)          # used in 1.0 smoke tests before the split was enforced
HELDOUT_SQL = ("SELECT t.ticket_id FROM eval.eval_splits s JOIN tickets t USING (ticket_uid)"
               " WHERE s.split = 'heldout' AND t.batch_id = 'train' ORDER BY t.ticket_id")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, EVAL_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    ae, cmp_ = _load("agent_eval"), _load("agent_compare")
    conn = ae._conn()
    ids = [r[0] for r in conn.execute(HELDOUT_SQL).fetchall() if r[0] not in EXCLUDE]
    print(f"[heldout] {len(ids)} tickets (excluded: {', '.join(EXCLUDE)})")
    dumps = {}
    for name in ("v1", "v2"):
        agent = ae._agent(name)
        runs, stopped = ae.run_all(agent, ids, "heldout", a.workers)
        if stopped:
            print(f"[heldout] STOPPED: {stopped}")
            return 1
        c = ae._conn()
        labels = {r[0]: {"correct_routing": r[1], "should_escalate": r[2], "relevant_kb_docs": list(r[3] or [])}
                  for r in c.execute(ae.LABEL_SQL).fetchall()}
        corpus = grounding.load_corpus(c)
        rows = [ae.score_run(t, runs[t], labels[t], corpus) for t in ids if t in runs and t in labels]
        dumps[name] = ae.dump_rows(rows, [])
        (OUTPUTS_DIR / f"agent_eval_heldout_{name}.json").write_text(json.dumps(dumps[name], indent=1, default=str),
                                                                     encoding="utf-8")
        m = ae.metrics(rows)
        print(f"[heldout] {name}: n={m['n']} routing L1/L2={m['routing_l1_l2']:.3f} esc P/R="
              f"{m['esc_precision'] or 0:.3f}/{m['esc_recall'] or 0:.3f} · spend ${spend.total():.3f}")
    md = cmp_.render(dumps["v1"], dumps["v2"], "1.0", "2.0 (frozen)",
                     title="AX-15 held-out evaluation: 1.0 vs 2.0 (frozen)", scope="held-out split",
                     sources="`outputs/agent_eval_heldout_v1.json` and `outputs/agent_eval_heldout_v2.json`")
    note = (f"\n> Held-out = 44 tickets of split `v1-seed42` minus {', '.join(EXCLUDE)} (used in 1.0 smoke tests) "
            f"= {len(ids)}. Run once, after the 2.0 freeze; no agent change follows from it. "
            f"Small n: one ticket = {100 / max(len(ids), 1):.1f} points overall and "
            f"{100 / max(sum(r['pilot'] for r in dumps['v2']['dev'].values()), 1):.1f} points in the pilot subset.\n")
    md = (md.replace(" all dev |", " all held-out |")
          .replace("## Regressions: dev tickets", "## Regressions: held-out tickets")
          .replace("applied SOP rules on dev", "applied SOP rules on held-out")
          .replace(" · held-out never run · same judge, same answer fields shown to it.", ".")
          .replace("count as routing misses on the dev split", "count as routing misses here"))
    lines = md.split("\n")
    out = OUTPUTS_DIR / "heldout_eval.md"
    out.write_text("\n".join(lines[:3] + [note] + lines[3:]), encoding="utf-8")
    print(f"[heldout] wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
