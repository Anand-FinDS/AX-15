"""Evaluate an AX-15 agent on the dev split and the chaos set.

    python eval/agent_eval.py --agent v1 --split dev [--limit N] [--chaos] [--workers 6]

The agent runs on ticket ids only (prompt-safe fields via tools.get_ticket); labels are joined
after the runs, here, for scoring. Held-out tickets are never run (only split='dev' members are
selected). Agent outputs are cached by ticket + agent prompt hash in outputs/agent_eval_cache/,
judge verdicts by ticket + judge prompt hash. Eval runs don't write action_log rows.

Metrics: routing accuracy (exact, and L1-vs-L2 collapsed), escalation precision/recall, kb_doc hit
vs relevant_kb_docs, invalid-JSON / incomplete rates, avg tool calls, cost and latency, and step
fidelity (share of returned steps found verbatim, whitespace-normalised, in kb_chunks.text).
Chaos: a Haiku judge (eval/prompts/chaos_judge.md) grades PASS/FAIL against the authored expected
behaviour (eval.chaos_labels has no separate pass/fail criteria, so expected_behavior is the rubric).
Writes outputs/agent_eval_<agent>.md.
"""
from __future__ import annotations

import argparse
import hashlib
import statistics
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import psycopg

from ax15 import db
from ax15.config import base_llm
from ax15.settings import OUTPUTS_DIR, ROOT
from ax15.trust import grounding, spend

EVAL_DIR = Path(__file__).resolve().parent
JUDGE_PROMPT = EVAL_DIR / "prompts" / "chaos_judge.md"
CACHE = OUTPUTS_DIR / "agent_eval_cache"
PILOT_PREFIXES = ("runbooks/pos-", "runbooks/printer-")

DEV_SQL = ("SELECT t.ticket_id FROM eval.eval_splits s JOIN tickets t USING (ticket_uid)"
           " WHERE s.split = 'dev' AND t.batch_id = 'train' ORDER BY t.ticket_id")
LABEL_SQL = ("SELECT t.ticket_id, l.correct_routing, l.should_escalate, l.relevant_kb_docs FROM eval.eval_labels l"
             " JOIN tickets t USING (ticket_uid) WHERE t.batch_id = 'train'")
CHAOS_SQL = ("SELECT t.ticket_id, c.chaos_type, c.expected_behavior, c.expected_routing, c.expected_mode,"
             " c.confidence, c.raw->>'section_note' FROM eval.chaos_labels c JOIN tickets t USING (ticket_uid)"
             " WHERE t.batch_id = 'train' ORDER BY t.ticket_id")
HELDOUT_SIZE_SQL = "SELECT count(*) FROM eval.eval_splits WHERE split = 'heldout'"

_local = threading.local()


def _conn():
    if not hasattr(_local, "conn") or _local.conn.closed or _local.conn.broken:
        _local.conn, _ = db.connect(autocommit=True)
    return _local.conn


def _agent(name: str):
    if name == "v1":
        from ax15.agents import v1_single
        return v1_single
    if name == "v2":
        from ax15.agents import v2_graph
        return v2_graph
    raise SystemExit(f"unknown agent {name!r}")


def _run_kwargs(agent) -> dict:
    # 2.0 eval runs skip episodic memory (get_past_actions) so results don't depend on console approvals
    return {"memory": False} if agent.AGENT_VERSION == "v2" else {}


# Fields both agents produce; the judge sees only these so 1.0 and 2.0 are graded like for like.
JUDGE_FIELDS = ("routing", "mode", "should_escalate", "escalation_reason", "priority", "kb_doc",
                "kb_section_breadcrumb", "steps", "reply_to_store", "l2_handoff", "flags", "confidence")


# --- running (cached) -------------------------------------------------------------------------

def run_cached(agent, ticket_id: str, split: str, phash: str) -> dict:
    path = CACHE / agent.AGENT_VERSION / f"{ticket_id}_{phash}.json"
    if path.exists():
        return {**json.loads(path.read_text(encoding="utf-8")), "cached": True}
    spend.check()
    try:
        r = agent.run_agent(ticket_id, batch_id="train", split=split, conn=_conn(), write_action=False,
                            **_run_kwargs(agent)).to_dict()
    except psycopg.OperationalError:              # Neon dropped the connection before the run started
        _local.conn = db.connect(autocommit=True)[0]
        r = agent.run_agent(ticket_id, batch_id="train", split=split, conn=_conn(), write_action=False,
                            **_run_kwargs(agent)).to_dict()
    if (r["status"] == "error" and "OperationalError" in (r.get("error") or "")) or \
            (r["status"] == "incomplete" and "timeout" in (r.get("error") or "")):
        return {**r, "cached": False}             # infra failure / hang: don't cache, rerun next time
    if r["status"] == "error" and "spend" in (r.get("error") or ""):
        raise spend.SpendCapExceeded(r["error"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(r, indent=1, default=str), encoding="utf-8")
    return {**r, "cached": False}


def run_all(agent, ids: list[str], split: str, workers: int) -> tuple[dict[str, dict], str | None]:
    phash = agent.prompt_hash()
    out: dict[str, dict] = {}
    stopped = None
    lock = threading.Lock()

    def one(tid):
        nonlocal stopped
        if stopped:
            return
        try:
            r = run_cached(agent, tid, split, phash)
        except spend.SpendCapExceeded as e:
            stopped = str(e)
            return
        except Exception as e:  # noqa: BLE001 - one broken run must not stop the eval
            r = {"status": "error", "ticket_id": tid, "error": f"{type(e).__name__}: {str(e)[:200]}", "answer": None,
                 "tool_calls": 0, "cost_usd": 0.0, "latency_s": 0.0, "trace_path": None, "cached": False}
        with lock:
            out[tid] = r
            done = len(out)
        if done % 10 == 0 or done == len(ids):
            print(f"[agent-eval] {split}: {done}/{len(ids)} · session spend ${spend.total():.3f}", flush=True)

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, ids))
    return out, stopped


# --- chaos judge ------------------------------------------------------------------------------

def judge_prompt_hash() -> str:
    return hashlib.sha256((base_llm()["model"] + JUDGE_PROMPT.read_text(encoding="utf-8")).encode()).hexdigest()[:12]


def parse_verdict(text: str) -> tuple[str, str] | None:
    m = re.search(r"\{.*\}", text, re.S)
    try:
        obj = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or obj.get("verdict") not in ("PASS", "FAIL") or not isinstance(obj.get("reason"), str):
        return None
    return obj["verdict"], obj["reason"]


def judge_chaos(ticket: dict, label: dict, run: dict, client=None) -> dict:
    key = hashlib.sha256(json.dumps([ticket, label, run.get("answer"), run.get("status")], sort_keys=True,
                                    default=str).encode()).hexdigest()[:12]
    path = CACHE / "judge" / f"{label['ticket_id']}_{judge_prompt_hash()}_{key}.json"
    if path.exists():
        return {**json.loads(path.read_text(encoding="utf-8")), "cached": True}
    spend.check()
    client = client or anthropic.Anthropic()
    cfg = base_llm()
    answer = ({k: run["answer"].get(k) for k in JUDGE_FIELDS} if run.get("answer")
              else {"note": f"no valid answer (status {run.get('status')}: {run.get('error')})"})
    if answer.get("l2_handoff"):
        answer["l2_handoff"] = {k: answer["l2_handoff"].get(k) for k in
                                ("summary", "evidence", "attempted_steps", "requested_action")}
    expected = {"chaos_type": label["chaos_type"], "expected_routing": label["expected_routing"],
                "expected_mode": label["expected_mode"], "expected_behavior": label["expected_behavior"],
                "common_behaviour_for_type": label.get("section_note")}
    user = (f"<ticket>\n{json.dumps(ticket, indent=1, default=str)}\n</ticket>\n\n<expected>\n"
            f"{json.dumps(expected, indent=1)}\n</expected>\n\n<agent_answer>\n{json.dumps(answer, indent=1)}\n"
            "</agent_answer>")
    verdict, cost = None, 0.0
    for _ in range(2):
        resp = client.messages.create(model=cfg["model"], max_tokens=2048, system=JUDGE_PROMPT.read_text(encoding="utf-8"),
                                      output_config={"effort": "medium"},
                                      messages=[{"role": "user", "content": user}])
        c = spend.cost_of(resp.usage.input_tokens, resp.usage.output_tokens)
        spend.record(c, "chaos_judge")
        cost += c
        verdict = parse_verdict("".join(b.text for b in resp.content if b.type == "text"))
        if verdict:
            break
    out = {"verdict": verdict[0] if verdict else "FAIL",
           "reason": verdict[1] if verdict else "judge produced no valid verdict", "judge_cost_usd": round(cost, 6)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return {**out, "cached": False}


# --- scoring ----------------------------------------------------------------------------------

def collapse(routing: str | None) -> str | None:
    if routing is None:
        return None
    return "l1" if routing.startswith("l1_") else ("l2" if routing == "l2_escalation" else "other")


@dataclass
class Scored:
    ticket_id: str
    run: dict
    label: dict | None
    fidelity: float | None
    n_steps: int
    n_grounded: int

    @property
    def ans(self) -> dict:
        return self.run.get("answer") or {}

    @property
    def ok(self) -> bool:
        return self.run["status"] == "ok"

    @property
    def pilot(self) -> bool:
        rb = [d for d in (self.label or {}).get("relevant_kb_docs", []) if d.startswith("runbooks/")]
        return bool(rb) and rb[0].startswith(PILOT_PREFIXES)

    @property
    def routing(self) -> str | None:
        """Scored routing: in 2.0 verify mode the routing class the rules give (verify_rules_routing)."""
        return self.ans.get("verify_rules_routing") or self.ans.get("routing")

    @property
    def routing_exact(self) -> bool:
        return self.ok and self.routing == self.label["correct_routing"]

    @property
    def routing_collapsed(self) -> bool:
        return self.ok and collapse(self.routing) == collapse(self.label["correct_routing"])

    @property
    def kb_hit(self) -> bool:
        return self.ok and self.ans.get("kb_doc") in (self.label["relevant_kb_docs"] or [])


def score_run(tid: str, run: dict, label: dict | None, corpus: str) -> Scored:
    steps = (run.get("answer") or {}).get("steps") or []
    g = sum(grounding.is_grounded(s, corpus) for s in steps)
    return Scored(tid, run, label, grounding.fidelity(steps, corpus), len(steps), g)


def metrics(rows: list[Scored]) -> dict:
    n = len(rows) or 1
    ok = [r for r in rows if r.ok]
    tp = sum(r.ok and r.ans.get("should_escalate") and r.label["should_escalate"] for r in rows)
    pred = sum(r.ok and bool(r.ans.get("should_escalate")) for r in rows)
    actual = sum(bool(r.label["should_escalate"]) for r in rows)
    steps = sum(r.n_steps for r in rows)
    return {
        "n": len(rows),
        "routing_exact": sum(r.routing_exact for r in rows) / n,
        "routing_l1_l2": sum(r.routing_collapsed for r in rows) / n,
        "esc_precision": tp / pred if pred else None,
        "esc_recall": tp / actual if actual else None,
        "kb_doc_hit": sum(r.kb_hit for r in rows) / n,
        "invalid_json": sum(r.run["status"] == "invalid_json" for r in rows) / n,
        "incomplete": sum(r.run["status"] == "incomplete" for r in rows) / n,
        "error": sum(r.run["status"] in ("error", "refused") for r in rows) / n,
        "avg_tool_calls": sum(r.run["tool_calls"] for r in rows) / n,
        "avg_cost": sum(r.run["cost_usd"] for r in rows) / n,
        "avg_latency": sum(r.run["latency_s"] for r in rows) / n,
        "median_latency": statistics.median([r.run["latency_s"] for r in rows]) if rows else 0.0,
        "step_fidelity": sum(r.n_grounded for r in rows) / steps if steps else None,
        "with_steps": sum(1 for r in ok if r.n_steps) / n,
    }


def severity(r: Scored, chaos: dict | None = None) -> tuple[int, list[str]]:
    why, s = [], 0
    if not r.ok:
        s += 3
        why.append(f"status {r.run['status']}: {(r.run.get('error') or '')[:80]}")
    if r.label:
        if r.label["should_escalate"] and r.ok and not r.ans.get("should_escalate"):
            s += 4
            why.append("missed escalation")
        elif r.ok and r.ans.get("should_escalate") and not r.label["should_escalate"]:
            s += 1
            why.append("unneeded escalation")
        if r.ok and not r.routing_collapsed:
            s += 1
            why.append(f"routing {r.ans.get('routing')} vs {r.label['correct_routing']}")
        if r.ok and r.ans.get("kb_doc") and not r.kb_hit:
            s += 1
            why.append(f"kb_doc {r.ans.get('kb_doc')} not relevant")
    if r.fidelity is not None and r.fidelity < 0.5:
        s += 1
        why.append(f"step fidelity {r.fidelity:.0%}")
    if chaos and chaos["verdict"] == "FAIL":
        s += 3
        why.append(f"chaos FAIL: {chaos['reason'][:100]}")
    return s, why


def routing_match(ans: dict, lab: dict) -> bool:
    """Chaos 'routing match': agent routing is one of the expected classes and, when the expected mode is
    verify, the agent is in verify mode. Verify-only labels (no expected routing) need verify mode only."""
    exp, mode = lab.get("expected_routing") or [], lab.get("expected_mode")
    if not ans:
        return False
    ans = {**ans, "routing": ans.get("verify_rules_routing") or ans.get("routing")}
    if not exp:
        return ans.get("mode") == "verify"
    return ans.get("routing") in exp and (mode != "verify" or ans.get("mode") == "verify")


def dump_rows(dev: list[Scored], chaos: list[tuple[Scored, dict, dict]]) -> dict:
    """Per-ticket results (JSON) for eval/agent_compare.py."""
    def run_info(r: Scored) -> dict:
        a = r.ans
        return {"status": r.run["status"], "error": r.run.get("error"), "routing": r.routing,
                "answer_routing": a.get("routing"),
                "mode": a.get("mode"), "should_escalate": a.get("should_escalate"), "priority": a.get("priority"),
                "kb_doc": a.get("kb_doc"), "n_steps": r.n_steps, "fidelity": r.fidelity,
                "fired": [f["id"] for f in a.get("fired_rules", []) if f.get("applied")],
                "flags": a.get("flags", []), "escalation_reason": a.get("escalation_reason"),
                "cost_usd": r.run["cost_usd"], "latency_s": r.run["latency_s"], "tool_calls": r.run["tool_calls"],
                "trace_path": r.run.get("trace_path")}
    return {
        "dev": {r.ticket_id: {**run_info(r), "label_routing": r.label["correct_routing"],
                              "label_escalate": r.label["should_escalate"], "relevant_kb_docs": r.label["relevant_kb_docs"],
                              "pilot": r.pilot, "routing_collapsed_ok": r.routing_collapsed, "kb_hit": r.kb_hit}
                for r in dev},
        "chaos": {s.ticket_id: {**run_info(s), "chaos_type": lab["chaos_type"], "verdict": j["verdict"],
                                "judge_reason": j["reason"], "expected_routing": lab["expected_routing"],
                                "expected_mode": lab["expected_mode"], "routing_match": routing_match(s.ans, lab)}
                  for s, j, lab in chaos},
    }


# --- report -----------------------------------------------------------------------------------

def _p(x) -> str:
    return "—" if x is None else f"{x:.1%}"


def _trace_link(run: dict) -> str:
    p = run.get("trace_path")
    if not p:
        return "—"
    rel = Path(p).resolve().relative_to(OUTPUTS_DIR.resolve()).as_posix() if Path(p).is_absolute() else p
    return f"[{Path(p).name}]({rel})"


def render(agent_name: str, scope: str, heldout_n: int, dev: list[Scored], chaos: list[tuple[Scored, dict, dict]],
           spent: float, stopped: str | None) -> str:
    L = [f"# AX-15 {agent_name} — agent eval", "",
         f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · model `{base_llm()['model']}` · scope: **{scope}** "
         f"({len(dev)} tickets run) · held-out: **{heldout_n} (never run)** · chaos: {len(chaos)} tickets._", ""]
    if stopped:
        L += [f"> **Stopped early:** {stopped}. Metrics cover the tickets that ran.", ""]
    rows = [("all dev", dev), ("pilot scope (pos-/printer- main runbook)", [r for r in dev if r.pilot])]
    L += ["## Dev split", "",
          "| Scope | n | Routing exact | Routing L1/L2 | Esc. precision | Esc. recall | kb_doc hit | Step fidelity | "
          "Invalid JSON | Incomplete | Error | Avg tools | Avg cost | Avg latency | Median latency |",
          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name, rs in rows:
        m = metrics(rs)
        L.append(f"| {name} | {m['n']} | {_p(m['routing_exact'])} | {_p(m['routing_l1_l2'])} | {_p(m['esc_precision'])} | "
                 f"{_p(m['esc_recall'])} | {_p(m['kb_doc_hit'])} | {_p(m['step_fidelity'])} | {_p(m['invalid_json'])} | "
                 f"{_p(m['incomplete'])} | {_p(m['error'])} | {m['avg_tool_calls']:.1f} | ${m['avg_cost']:.4f} | "
                 f"{m['avg_latency']:.1f}s | {m['median_latency']:.1f}s |")
    L += ["", "Routing exact compares with the train label (`l1_guided` / `l2_escalation`); L1/L2 collapses "
          "`l1_*` to L1 (train labels have no self-service / clarification / non-IT classes). kb_doc hit = the "
          "agent's `kb_doc` is in `relevant_kb_docs`. Step fidelity = share of all returned steps found verbatim "
          "(whitespace-normalised, list marker ignored) in `kb_chunks.text`.", ""]
    # routing confusion
    conf: dict[tuple[str, str], int] = {}
    for r in dev:
        k = (r.label["correct_routing"], r.routing or f"({r.run['status']})")
        conf[k] = conf.get(k, 0) + 1
    L += ["### Routing: label → agent", "", "| Label | Agent | n |", "|---|---|---:|"]
    L += [f"| {a} | {b} | {n} |" for (a, b), n in sorted(conf.items(), key=lambda x: -x[1])]
    L.append("")

    if chaos:
        passed = sum(j["verdict"] == "PASS" for _, j, _ in chaos)
        matched = sum(routing_match(s.ans, lab) for s, _, lab in chaos)
        L += [f"## Chaos set: {passed} / {len(chaos)} PASS (judge) · routing match {matched} / {len(chaos)}", "",
              "Routing match = the agent's routing is one of the expected classes (and verify mode when expected). "
              "The judge grades the whole expected behaviour, so the two counts differ.", "",
              "| Ticket | Chaos type | Expected | Agent routing | Mode | Routing match | Verdict | Judge reason | Trace |",
              "|---|---|---|---|---|---|---|---|---|"]
        for s, j, lab in sorted(chaos, key=lambda x: (x[2]["chaos_type"], x[0].ticket_id)):
            exp = " + ".join(lab["expected_routing"]) or "—"
            exp += " (verify)" if lab["expected_mode"] == "verify" else ""
            L.append(f"| {s.ticket_id} | {lab['chaos_type']} | {exp} | {s.ans.get('routing') or s.run['status']} | "
                     f"{s.ans.get('mode') or '—'} | {'yes' if routing_match(s.ans, lab) else 'no'} | **{j['verdict']}** | {j['reason'].replace('|', '/')} | "
                     f"{_trace_link(s.run)} |")
        by_type: dict[str, list[str]] = {}
        for _, j, lab in chaos:
            by_type.setdefault(lab["chaos_type"], []).append(j["verdict"])
        L += ["", "| Chaos type | PASS |", "|---|---:|"]
        L += [f"| {k} | {v.count('PASS')} / {len(v)} |" for k, v in sorted(by_type.items())]
        L.append("")

    scored = [(severity(r), r, None) for r in dev] + [(severity(s, j), s, lab) for s, j, lab in chaos]
    worst = sorted((x for x in scored if x[0][0] > 0), key=lambda x: (-x[0][0], -x[1].run["cost_usd"]))[:5]
    L += ["## 5 worst failures", "", "| # | Ticket | Set | Severity | What went wrong | Trace |", "|---:|---|---|---:|---|---|"]
    for i, ((sev, why), r, lab) in enumerate(worst, 1):
        L.append(f"| {i} | {r.ticket_id} | {'chaos/' + lab['chaos_type'] if lab else 'dev'} | {sev} | "
                 f"{'; '.join(why).replace('|', '/')} | {_trace_link(r.run)} |")
    fresh = sum(r.run["cost_usd"] for r in dev if not r.run.get("cached")) + \
        sum(s.run["cost_usd"] for s, _, _ in chaos if not s.run.get("cached"))
    total = sum(r.run["cost_usd"] for r in dev) + sum(s.run["cost_usd"] for s, _, _ in chaos)
    judge = sum(j.get("judge_cost_usd", 0) for _, j, _ in chaos)
    L += ["", "## Cost", "",
          f"- Agent runs: **${total:.4f}** total for {len(dev) + len(chaos)} tickets (${fresh:.4f} new on this run; "
          f"the rest from cache) · chaos judge: ${judge:.4f}",
          f"- Session spend ledger (all API calls today): ${spent:.4f} of the ${spend.cap():.2f} cap", ""]
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default="v1")
    ap.add_argument("--split", default="dev", choices=["dev"])            # held-out is never run here
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--chaos", action="store_true")
    ap.add_argument("--workers", type=int, default=6)
    a = ap.parse_args()
    agent = _agent(a.agent)
    conn = _conn()
    dev_ids = [r[0] for r in conn.execute(DEV_SQL).fetchall()]
    heldout_n = conn.execute(HELDOUT_SIZE_SQL).fetchone()[0]
    if not dev_ids:
        raise SystemExit("no dev split in eval.eval_splits; run python -m ax15.l0_data.eval_assets")
    dev_ids = dev_ids[: a.limit] if a.limit else dev_ids
    chaos_rows = conn.execute(CHAOS_SQL).fetchall() if a.chaos else []
    chaos_rows = chaos_rows[: a.limit] if (a.limit and a.chaos) else chaos_rows

    runs, stopped = run_all(agent, dev_ids, "dev", a.workers)
    chaos_runs, stopped2 = (run_all(agent, [c[0] for c in chaos_rows], "chaos", a.workers) if chaos_rows and not stopped
                            else ({}, None))
    stopped = stopped or stopped2

    # labels joined only now, after all runs (fresh connection: the idle one may have been dropped)
    try:
        conn.execute("SELECT 1")
    except psycopg.OperationalError:
        _local.conn = db.connect(autocommit=True)[0]
    conn = _conn()
    labels = {r[0]: {"correct_routing": r[1], "should_escalate": r[2], "relevant_kb_docs": list(r[3] or [])}
              for r in conn.execute(LABEL_SQL).fetchall()}
    corpus = grounding.load_corpus(conn)
    dev = [score_run(t, runs[t], labels[t], corpus) for t in dev_ids if t in runs and t in labels]

    chaos = []
    if chaos_runs:
        from ax15.tools.functions import get_ticket
        judge_client = anthropic.Anthropic()
        for c in chaos_rows:
            if c[0] not in chaos_runs or stopped:
                continue
            lab = {"ticket_id": c[0], "chaos_type": c[1], "expected_behavior": c[2], "expected_routing": list(c[3] or []),
                   "expected_mode": c[4], "confidence": c[5], "section_note": c[6]}
            tk = get_ticket(c[0], "train", conn=conn).model_dump(mode="json")
            try:
                j = judge_chaos(tk, lab, chaos_runs[c[0]], judge_client)
            except spend.SpendCapExceeded as e:
                stopped = str(e)
                break
            chaos.append((score_run(c[0], chaos_runs[c[0]], None, corpus), j, lab))

    scope = "dev split" + (f" (first {a.limit})" if a.limit else "")
    report = render(a.agent, scope, heldout_n, dev, chaos, spend.total(), stopped)
    out = OUTPUTS_DIR / f"agent_eval_{a.agent}.md"
    out.write_text(report, encoding="utf-8")
    (OUTPUTS_DIR / f"agent_eval_{a.agent}.json").write_text(json.dumps(dump_rows(dev, chaos), indent=1, default=str),
                                                           encoding="utf-8")
    m = metrics(dev)
    print(f"[agent-eval] dev n={m['n']} routing L1/L2={m['routing_l1_l2']:.3f} kb_doc={m['kb_doc_hit']:.3f} "
          f"fidelity={m['step_fidelity'] or 0:.3f} · chaos PASS {sum(j['verdict'] == 'PASS' for _, j, _ in chaos)}/"
          f"{len(chaos)} · spend ${spend.total():.3f}" + (f" · STOPPED: {stopped}" if stopped else ""))
    print(f"[agent-eval] wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
