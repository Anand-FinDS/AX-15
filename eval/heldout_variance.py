"""Run-to-run variance on the held-out split: AX-15 1.0 and 2.0 (frozen code, unchanged), 3 fresh runs each.

    python eval/heldout_variance.py            # -> outputs/heldout_variance.md (+ outputs/heldout_variance/run<k>_<v>.json)
    python eval/heldout_variance.py --report   # re-render from the saved runs, no API calls

Same tickets as eval/heldout_eval.py (43 = held-out minus INC-00162), same agent settings (no action write,
no 2.0 memory) and scoring, but the agent-output cache is bypassed so every run calls the models. A finished run
is saved and not repeated on re-invocation; a run stopped by the spend guard is saved as partial.
Neither base model accepts a temperature setting (both return HTTP 400 for it), so this measures the
variance at the models' default sampling.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import statistics
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ax15.settings import OUTPUTS_DIR, ROOT
from ax15.trust import grounding, spend

EVAL_DIR = Path(__file__).resolve().parent
RUN_DIR = OUTPUTS_DIR / "heldout_variance"
ORDER = [(1, "v1"), (1, "v2"), (2, "v1"), (2, "v2"), (3, "v2"), (3, "v1")]   # the costliest run last
NAMES = {"v1": "1.0", "v2": "2.0"}


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, EVAL_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def run_fresh(ae, agent, ids: list[str], workers: int) -> tuple[dict, str | None]:
    """ae.run_cached without the cache read/write."""
    out, stopped, lock = {}, None, threading.Lock()

    def one(tid):
        nonlocal stopped
        if stopped:
            return
        try:
            spend.check()
            r = agent.run_agent(tid, batch_id="train", split="heldout", conn=ae._conn(), write_action=False,
                                **ae._run_kwargs(agent)).to_dict()
            if r["status"] == "error" and "spend" in (r.get("error") or ""):
                raise spend.SpendCapExceeded(r["error"])
        except spend.SpendCapExceeded as e:
            stopped = str(e)
            return
        except Exception as e:  # noqa: BLE001 - one broken run must not stop the eval
            r = {"status": "error", "ticket_id": tid, "error": f"{type(e).__name__}: {str(e)[:200]}", "answer": None,
                 "tool_calls": 0, "cost_usd": 0.0, "latency_s": 0.0, "trace_path": None}
        with lock:
            out[tid] = r
            if len(out) % 10 == 0 or len(out) == len(ids):
                print(f"  {len(out)}/{len(ids)} · session spend ${spend.total():.3f}", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, ids))
    return out, stopped


def metrics(rows: dict) -> dict:
    """Routing L1/L2 (all), escalation P/R with verify-mode answers excluded (and with all, as in heldout_eval.md),
    pilot routing."""
    def pr(rs):
        ok = [r for r in rs if r["status"] == "ok"]
        tp = sum(1 for r in ok if r["should_escalate"] and r["label_escalate"])
        pred = sum(1 for r in ok if r["should_escalate"])
        actual = sum(1 for r in rs if r["label_escalate"])
        return (tp / pred if pred else None), (tp / actual if actual else None)
    rs = list(rows.values())
    nv = [r for r in rs if r.get("mode") != "verify"]
    pilot = [r for r in rs if r["pilot"]]
    p, rc = pr(nv)
    pa, ra = pr(rs)
    return {"n": len(rs), "routing": sum(r["routing_collapsed_ok"] for r in rs) / max(len(rs), 1),
            "esc_p": p, "esc_r": rc, "esc_p_all": pa, "esc_r_all": ra, "n_verify": len(rs) - len(nv),
            "pilot_routing": sum(r["routing_collapsed_ok"] for r in pilot) / max(len(pilot), 1), "n_pilot": len(pilot),
            "errors": sum(r["status"] != "ok" for r in rs),
            "cost": sum(r["cost_usd"] or 0 for r in rs), "escalations": sum(bool(r["should_escalate"]) for r in rs)}


def _p(x) -> str:
    return "—" if x is None else f"{x:.1%}"


def _stats(vals: list) -> str:
    v = [x for x in vals if x is not None]
    if not v:
        return "— | — | —"
    return f"{statistics.mean(v):.1%} | {min(v):.1%} | {max(v):.1%}"


def render(runs: dict, original: dict, ids: list[str], spent: float) -> str:
    L = ["# AX-15 held-out run-to-run variance: 1.0 and 2.0 (frozen), 3 runs each", "",
         f"_43 held-out tickets (split `v1-seed42` minus INC-00162) · frozen 2.0 code (before the safety rules), unchanged · "
         f"agent-output cache bypassed, every run calls the models · session spend ${spent:.3f}._", "",
         "> **Why there is no temperature setting.** Neither model accepts one. Claude Haiku 5.5 (`llm.base`) "
         "returns `400 temperature: Extra inputs are not permitted` for `temperature=0` and also for the default `1` "
         "(the installed SDK uses API version `2026-10-21`, which rejects sampling parameters). GPT-6 Luna "
         "(`llm.fallback`) returns `400 'temperature' does not support 0 with this model. Only the default (1) value "
         "is supported.` So these runs measure the variance at the models' default sampling.", ""]
    partial = [f"{NAMES[v]} run {k} (not run: {', '.join(sorted(set(ids) - set(d['dev'])))})"
               for (k, v), d in runs.items() if d.get("partial")]
    if partial:
        L += [f"> **Partial:** {'; '.join(partial)}: stopped at the spend cap, so its metrics cover fewer tickets "
              "and its mean/min/max contribution is on that smaller set.", ""]
    for v in ("v1", "v2"):
        ks = sorted(k for (k, vv) in runs if vv == v)
        ms = {k: metrics(runs[(k, v)]["dev"]) for k in ks}
        mo = metrics(original[v]["dev"]) if original.get(v) else None
        L += [f"## {NAMES[v]}", "",
              "| Metric | " + " | ".join(f"Run {k}" for k in ks) + " | Mean | Min | Max | Original held-out run |",
              "|---|" + "---:|" * (len(ks) + 4)]
        for key, label in [("routing", "Routing L1/L2"), ("esc_p", "Escalation precision (verify excluded)"),
                           ("esc_r", "Escalation recall (verify excluded)"), ("pilot_routing", "Pilot routing L1/L2"),
                           ("esc_p_all", "Escalation precision (all, as heldout_eval.md)"),
                           ("esc_r_all", "Escalation recall (all, as heldout_eval.md)")]:
            L.append(f"| {label} | " + " | ".join(_p(ms[k][key]) for k in ks) + f" | {_stats([ms[k][key] for k in ks])} | "
                     f"{_p(mo[key]) if mo else '—'} |")
        for key, label in [("n", "n"), ("n_pilot", "n pilot"), ("n_verify", "Verify answers excluded"),
                           ("escalations", "Escalations"), ("errors", "Errors / incomplete")]:
            L.append(f"| {label} | " + " | ".join(str(ms[k][key]) for k in ks) + " | | | | "
                     f"{mo[key] if mo else '—'} |")
        L.append(f"| Cost | " + " | ".join(f"${ms[k]['cost']:.3f}" for k in ks) + " | | | | "
                 f"{'$%.3f' % mo['cost'] if mo else '—'} |")
        L.append("")
        # routing changes between the fresh runs
        changed = []
        for t in ids:
            rr = [runs[(k, v)]["dev"].get(t) for k in ks]
            cls = [(r["routing"] if r["status"] == "ok" else r["status"]) if r else "not run" for r in rr]
            if len({c for c in cls if c != "not run"}) > 1:
                lab = next(r for r in rr if r)["label_routing"]
                orig = original[v]["dev"].get(t) if original.get(v) else None
                changed.append((t, lab, cls, orig["routing"] if orig else "—",
                                next(r for r in rr if r)["pilot"]))
        orig_diff = 0
        if original.get(v):
            for t in ids:
                o = original[v]["dev"].get(t)
                r1 = runs.get((1, v), {}).get("dev", {}).get(t)
                orig_diff += bool(o and r1 and o["routing"] != r1["routing"])
        L += [f"**Routing changed between the {len(ks)} runs on {len(changed)} of {len(ids)} tickets** "
              f"({len(changed) / len(ids):.0%}). Run 1 vs the original held-out run: {orig_diff} tickets differ.", ""]
        if changed:
            L += ["| Ticket | Pilot | Label | " + " | ".join(f"Run {k}" for k in ks) + " | Original run |",
                  "|---|---|---|" + "---|" * (len(ks) + 1)]
            for t, lab, cls, o, pil in changed:
                L.append(f"| {t} | {'yes' if pil else ''} | {lab} | " + " | ".join(cls) + f" | {o} |")
            L.append("")
    L += ["## Reading this", "",
          "- One ticket = 2.3 points overall and 8.3 points in the pilot subset (n = 12).",
          "- Inputs, code, prompts and configs are identical across runs; differences are the models' choices.",
          "- `Original held-out run` = `outputs/agent_eval_heldout_<v>.json` (2026-11-23, same frozen code); shown "
          "for reference, not included in mean/min/max.",
          "- Verify-mode answers (Resolved/Closed tickets) are excluded from escalation P/R per the freeze rulings; "
          "the held-out split has none, so both P/R rows agree.", ""]
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--report", action="store_true", help="re-render from saved runs only")
    a = ap.parse_args()
    ae, he = _load("agent_eval"), _load("heldout_eval")
    conn = ae._conn()
    ids = [r[0] for r in conn.execute(he.HELDOUT_SQL).fetchall() if r[0] not in he.EXCLUDE]
    labels = {r[0]: {"correct_routing": r[1], "should_escalate": r[2], "relevant_kb_docs": list(r[3] or [])}
              for r in conn.execute(ae.LABEL_SQL).fetchall()}
    corpus = grounding.load_corpus(conn)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[variance] {len(ids)} tickets")
    runs = {}
    for k, v in ORDER:
        path = RUN_DIR / f"run{k}_{v}.json"
        if path.exists():
            runs[(k, v)] = json.loads(path.read_text(encoding="utf-8"))
            if not runs[(k, v)].get("partial"):
                continue
        if a.report:
            continue
        print(f"[variance] {NAMES[v]} run {k}", flush=True)
        out, stopped = run_fresh(ae, ae._agent(v), ids, a.workers)
        rows = [ae.score_run(t, out[t], labels[t], corpus) for t in ids if t in out and t in labels]
        d = {**ae.dump_rows(rows, []), "partial": bool(stopped), "stopped": stopped}
        path.write_text(json.dumps(d, indent=1, default=str), encoding="utf-8")
        runs[(k, v)] = d
        m = metrics(d["dev"])
        print(f"[variance] {NAMES[v]} run {k}: n={m['n']} routing={m['routing']:.3f} esc P/R={_p(m['esc_p'])}/"
              f"{_p(m['esc_r'])} pilot={m['pilot_routing']:.3f} · spend ${spend.total():.3f}", flush=True)
        if stopped:
            print(f"[variance] STOPPED: {stopped}")
            break
    original = {v: json.loads((OUTPUTS_DIR / f"agent_eval_heldout_{v}.json").read_text(encoding="utf-8"))
                for v in ("v1", "v2") if (OUTPUTS_DIR / f"agent_eval_heldout_{v}.json").exists()}
    out = OUTPUTS_DIR / "heldout_variance.md"
    out.write_text(render(runs, original, ids, spend.total()), encoding="utf-8")
    print(f"[variance] wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
