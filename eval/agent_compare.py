"""Side-by-side AX-15 1.0 vs 2.0 from the per-ticket eval dumps (no model calls).

    python eval/agent_eval.py --agent v1 --split dev --chaos     # writes outputs/agent_eval_v1.json (cached runs)
    python eval/agent_eval.py --agent v2 --split dev --chaos     # writes outputs/agent_eval_v2.json
    python eval/agent_compare.py                                  # -> outputs/agent_compare.md

Same dev split (176), same 36 chaos tickets, same metrics, same judge (the judge sees the same answer
fields for both agents). Regressions = tickets 1.0 got right and 2.0 got wrong.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from datetime import datetime, timezone

from ax15.settings import OUTPUTS_DIR, ROOT

REPORT = OUTPUTS_DIR / "agent_compare.md"


def load(agent: str) -> dict:
    p = OUTPUTS_DIR / f"agent_eval_{agent}.json"
    if not p.exists():
        raise SystemExit(f"missing {p.relative_to(ROOT)}: run eval/agent_eval.py --agent {agent} --split dev --chaos")
    return json.loads(p.read_text(encoding="utf-8"))


def metrics(rows: list[dict]) -> dict:
    n = len(rows) or 1
    ok = [r for r in rows if r["status"] == "ok"]
    tp = sum(1 for r in ok if r["should_escalate"] and r["label_escalate"])
    pred = sum(1 for r in ok if r["should_escalate"])
    actual = sum(1 for r in rows if r["label_escalate"])
    steps = sum(r["n_steps"] for r in rows)
    grounded = sum(round((r["fidelity"] or 0) * r["n_steps"]) for r in rows)
    lat = [r["latency_s"] for r in rows]
    return {"n": len(rows), "routing": sum(r["routing_collapsed_ok"] for r in rows) / n,
            "esc_p": tp / pred if pred else None, "esc_r": tp / actual if actual else None,
            "kb": sum(r["kb_hit"] for r in rows) / n, "fid": grounded / steps if steps else None,
            "with_steps": sum(1 for r in ok if r["n_steps"]) / n,
            "invalid": sum(r["status"] == "invalid_json" for r in rows) / n,
            "incomplete": sum(r["status"] == "incomplete" for r in rows) / n,
            "error": sum(r["status"] in ("error", "refused") for r in rows) / n,
            "tools": sum(r["tool_calls"] for r in rows) / n, "cost": sum(r["cost_usd"] for r in rows) / n,
            "lat": sum(lat) / n, "lat_med": statistics.median(lat) if lat else 0.0,
            "esc_rate": pred / n}


def _p(x) -> str:
    return "—" if x is None else f"{x:.1%}"


def _d(a, b) -> str:
    if a is None or b is None:
        return ""
    d = (b - a) * 100
    return f" ({'+' if d >= 0 else ''}{d:.1f})"


def reason_v2(r: dict) -> str:
    fired = ", ".join(r["fired"]) or "no rule fired (default L1)"
    flags = [f for f in r.get("flags", []) if not f.startswith("handoff_missing")][:3]
    return f"{r['routing'] or r['status']} via {fired}" + (f"; flags {', '.join(flags)}" if flags else "")


def render(v1: dict, v2: dict, na: str = "1.0", nb: str = "2.0", title: str | None = None,
           scope: str = "dev split", sources: str = "") -> str:
    dev_ids = sorted(set(v1["dev"]) & set(v2["dev"]))
    ch_ids = sorted(set(v1["chaos"]) & set(v2["chaos"]))
    L = [f"# {title or 'AX-15 ⟨A⟩ vs ⟨B⟩ — side by side'}", "",
         f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from {sources or 'the eval dumps'}"
         f" · {scope}: {len(dev_ids)} tickets in both · chaos: {len(ch_ids)} · "
         "held-out never run · same judge, same answer fields shown to it._", "",
         f"## {scope[0].upper() + scope[1:]} and pilot scope", "",
         "| Metric | ⟨A⟩ all dev | ⟨B⟩ all dev | ⟨A⟩ pilot | ⟨B⟩ pilot |", "|---|---:|---:|---:|---:|"]
    scopes = {}
    for ver, data in (("v1", v1), ("v2", v2)):
        rows = [data["dev"][t] for t in dev_ids]
        scopes[(ver, "all")] = metrics(rows)
        scopes[(ver, "pilot")] = metrics([r for r in rows if r["pilot"]])
    a1, a2, p1, p2 = scopes[("v1", "all")], scopes[("v2", "all")], scopes[("v1", "pilot")], scopes[("v2", "pilot")]
    for key, label in (("routing", "Routing L1/L2"), ("esc_p", "Escalation precision"), ("esc_r", "Escalation recall"),
                       ("esc_rate", "Escalation rate"), ("kb", "kb_doc hit"), ("fid", "Step fidelity (verbatim)"),
                       ("with_steps", "Answers with steps"), ("invalid", "Invalid JSON"),
                       ("incomplete", "Incomplete"), ("error", "Error")):
        L.append(f"| {label} | {_p(a1[key])} | {_p(a2[key])}{_d(a1[key], a2[key])} | {_p(p1[key])} | "
                 f"{_p(p2[key])}{_d(p1[key], p2[key])} |")
    L.append(f"| n | {a1['n']} | {a2['n']} | {p1['n']} | {p2['n']} |")
    L.append(f"| Avg tool calls | {a1['tools']:.1f} | {a2['tools']:.1f} | {p1['tools']:.1f} | {p2['tools']:.1f} |")
    L.append(f"| Avg cost / ticket | ${a1['cost']:.4f} | ${a2['cost']:.4f} | ${p1['cost']:.4f} | ${p2['cost']:.4f} |")
    L.append(f"| Avg latency | {a1['lat']:.1f}s | {a2['lat']:.1f}s | {p1['lat']:.1f}s | {p2['lat']:.1f}s |")
    L.append(f"| Median latency | {a1['lat_med']:.1f}s | {a2['lat_med']:.1f}s | {p1['lat_med']:.1f}s | "
             f"{p2['lat_med']:.1f}s |")
    L += ["", "Points in brackets = ⟨B⟩ minus ⟨A⟩. Train labels only use `l1_guided` / `l2_escalation`, so "
          "`needs_clarification` / `non_it` answers count as routing misses on the dev split.", ""]

    # routing confusion for ⟨B⟩
    conf = Counter((v2["dev"][t]["label_routing"], v2["dev"][t]["routing"] or f"({v2['dev'][t]['status']})")
                   for t in dev_ids)
    L += ["### ⟨B⟩ routing: label → agent", "", "| Label | ⟨B⟩ | n |", "|---|---|---:|"]
    L += [f"| {a} | {b} | {n} |" for (a, b), n in conf.most_common()]
    fired = Counter(f for t in dev_ids for f in v2["dev"][t]["fired"])
    L += ["", "### ⟨B⟩ applied SOP rules on dev (ticket counts)", "", "| Rule | Tickets |", "|---|---:|"]
    L += [f"| {k} | {n} |" for k, n in fired.most_common()]
    L.append("")

    # chaos
    if not ch_ids:
        return _finish(L, v1, v2, dev_ids, [], na, nb)
    c1 = [v1["chaos"][t] for t in ch_ids]
    c2 = [v2["chaos"][t] for t in ch_ids]
    L += ["## Chaos set (36 adversarial tickets)", "",
          "| | ⟨A⟩ | ⟨B⟩ |", "|---|---:|---:|",
          f"| Judge PASS | {sum(r['verdict'] == 'PASS' for r in c1)} / {len(c1)} | "
          f"{sum(r['verdict'] == 'PASS' for r in c2)} / {len(c2)} |",
          f"| Routing match | {sum(r['routing_match'] for r in c1)} / {len(c1)} | "
          f"{sum(r['routing_match'] for r in c2)} / {len(c2)} |",
          f"| Avg cost / ticket | ${sum(r['cost_usd'] for r in c1) / len(c1):.4f} | "
          f"${sum(r['cost_usd'] for r in c2) / len(c2):.4f} |", "",
          "Routing match = routing is one of the expected classes (and verify mode where expected); the judge "
          "grades the whole expected behaviour against the authored rubric.", "",
          "| Chaos type | ⟨A⟩ PASS | ⟨B⟩ PASS | ⟨A⟩ routing match | ⟨B⟩ routing match |", "|---|---:|---:|---:|---:|"]
    types = sorted({r["chaos_type"] for r in c2})
    for ty in types:
        r1 = [r for r in c1 if r["chaos_type"] == ty]
        r2 = [r for r in c2 if r["chaos_type"] == ty]
        L.append(f"| {ty} | {sum(r['verdict'] == 'PASS' for r in r1)} / {len(r1)} | "
                 f"{sum(r['verdict'] == 'PASS' for r in r2)} / {len(r2)} | {sum(r['routing_match'] for r in r1)} | "
                 f"{sum(r['routing_match'] for r in r2)} |")
    L += ["", "| Ticket | Type | Expected | ⟨A⟩ routing | ⟨A⟩ | ⟨B⟩ routing | ⟨B⟩ | ⟨B⟩ judge reason |",
          "|---|---|---|---|---|---|---|---|"]
    for t in sorted(ch_ids, key=lambda x: (v2["chaos"][x]["chaos_type"], x)):
        a, b = v1["chaos"][t], v2["chaos"][t]
        exp = " + ".join(b["expected_routing"]) or "—"
        exp += " (verify)" if b["expected_mode"] == "verify" else ""
        L.append(f"| {t} | {b['chaos_type']} | {exp} | {a['routing'] or a['status']} | {a['verdict']} | "
                 f"{b['routing'] or b['status']} ({b['mode']}) | **{b['verdict']}** | "
                 f"{b['judge_reason'].replace('|', '/')} |")
    L.append("")

    return _finish(L, v1, v2, dev_ids, ch_ids, na, nb)


def _finish(L, v1, v2, dev_ids, ch_ids, na, nb) -> str:
    # regressions / improvements
    reg = [t for t in dev_ids if v1["dev"][t]["routing_collapsed_ok"] and not v2["dev"][t]["routing_collapsed_ok"]]
    imp = [t for t in dev_ids if not v1["dev"][t]["routing_collapsed_ok"] and v2["dev"][t]["routing_collapsed_ok"]]
    L += [f"## Regressions: dev tickets ⟨A⟩ routed right and ⟨B⟩ routed wrong ({len(reg)}; {len(imp)} the other way)",
          "", "| Ticket | Pilot | Label | ⟨A⟩ | ⟨B⟩ — why |", "|---|---|---|---|---|"]
    for t in reg:
        a, b = v1["dev"][t], v2["dev"][t]
        L.append(f"| {t} | {'yes' if b['pilot'] else ''} | {b['label_routing']} | {a['routing']} | "
                 f"{reason_v2(b).replace('|', '/')} |")
    if not ch_ids:
        return _kb_section(L, v1, v2, dev_ids, na, nb)
    creg = [t for t in ch_ids if v1["chaos"][t]["verdict"] == "PASS" and v2["chaos"][t]["verdict"] == "FAIL"]
    cimp = [t for t in ch_ids if v1["chaos"][t]["verdict"] == "FAIL" and v2["chaos"][t]["verdict"] == "PASS"]
    L += ["", f"### Chaos regressions: ⟨A⟩ PASS → ⟨B⟩ FAIL ({len(creg)}; {len(cimp)} FAIL → PASS)", "",
          "| Ticket | Type | ⟨B⟩ | Judge reason |", "|---|---|---|---|"]
    for t in creg:
        b = v2["chaos"][t]
        L.append(f"| {t} | {b['chaos_type']} | {reason_v2(b).replace('|', '/')} | {b['judge_reason'].replace('|', '/')} |")
    return _kb_section(L, v1, v2, dev_ids, na, nb)


def _kb_section(L, v1, v2, dev_ids, na, nb) -> str:
    kb_reg = [t for t in dev_ids if v1["dev"][t]["kb_hit"] and not v2["dev"][t]["kb_hit"]]
    L += ["", f"### kb_doc regressions (⟨A⟩ cited a relevant runbook, ⟨B⟩ did not): {len(kb_reg)}", ""]
    L += [f"- {t}: ⟨B⟩ {reason_v2(v2['dev'][t])}; kb_doc {v2['dev'][t]['kb_doc'] or '—'}" for t in kb_reg[:25]]
    L.append("")
    return ("\n".join(L) + "\n").replace("⟨A⟩", na).replace("⟨B⟩", nb)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", default="v1", help="dump name: outputs/agent_eval_<a>.json")
    ap.add_argument("--b", default="v2")
    ap.add_argument("--na", default="1.0")
    ap.add_argument("--nb", default="2.0")
    ap.add_argument("--title", default=None)
    ap.add_argument("--out", default=REPORT.name)
    a = ap.parse_args()
    v1, v2 = load(a.a), load(a.b)
    out = OUTPUTS_DIR / a.out
    out.write_text(render(v1, v2, a.na, a.nb, a.title,
                          sources=f"`outputs/agent_eval_{a.a}.json` and `outputs/agent_eval_{a.b}.json`"),
                   encoding="utf-8")
    print(f"[compare] wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
