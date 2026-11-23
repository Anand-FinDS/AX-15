"""L0-5 landing zone: read a test batch folder, load it, run an agent on it, write a report.

    ax15 run-batch incoming/<batch> [--agent v1|v2]        (default v2)

Files in incoming/<batch>/ (any mix):
- *.csv   train-like columns (the JSON ticket keys). List cells (tags, related_tickets, ticket_history) may be
          JSON strings; missing optional columns are fine. A malformed row is reported and skipped.
- *.json  one ticket object or an array of them (train JSON schema). labels.json = ground truth (optional).
- *.txt   one free-text ticket per file (the existing deterministic free-text normaliser).

Label / eval-only columns (correct_routing, should_escalate, relevant_kb_docs, chaos_type, ...) are removed
before the ticket is normalised, so they never reach the agent. When labels are present they are joined
after the runs and scored with the dev eval's code (eval/agent_eval.py: score_run + metrics).

Tickets are loaded with source='panel_test' and batch_id=<folder name>; re-running replaces the batch
(upsert per ticket; tickets no longer in the folder are removed). Runs use eval mode: no action_log
writes, no HITL pause, no episodic memory (same settings as the held-out eval).
Output: outputs/batch_<name>_report.md and outputs/batch_<name>_report.csv.
"""
from __future__ import annotations

import csv
import importlib.util
import io
import json
import re
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ax15.l0_data.leakage import LEAKAGE_KEYS
from ax15.l0_data.normalise import Normalised, TicketValidationError, from_json, from_text
from ax15.settings import OUTPUTS_DIR, ROOT

SOURCE = "panel_test"
LABEL_KEYS = ("correct_routing", "should_escalate", "relevant_kb_docs")
DROP_KEYS = LEAKAGE_KEYS | {"chaos_type", "filename"}           # never agent input (index CSV / labels file)
LIST_COLS = ("tags", "related_tickets", "ticket_history", "relevant_kb_docs")
BOOL_COLS = ("escalation_flag", "should_escalate")
_TRUE, _FALSE = {"true", "1", "yes", "y", "t"}, {"false", "0", "no", "n", "f", ""}
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


# --- intake -----------------------------------------------------------------------------------

@dataclass
class Intake:
    batch_id: str
    files: list[str] = field(default_factory=list)
    items: list[Normalised] = field(default_factory=list)
    refs: dict[str, str] = field(default_factory=dict)            # ticket_id -> "file:row"
    labels: dict[str, dict] = field(default_factory=dict)         # ticket_id -> label (eval only)
    rejected: list[dict] = field(default_factory=list)            # {"ref", "error"}
    warnings: list[str] = field(default_factory=list)
    dropped_columns: set[str] = field(default_factory=set)
    rows_read: int = 0


def batch_name(folder: Path) -> str:
    name = folder.name
    if not _NAME_RE.match(name) or name == "train" or name.startswith("ui-"):
        raise ValueError(f"batch folder name {name!r} not allowed (letters, digits, _ . -; not 'train' or 'ui-*')")
    return name


def _bool(v: Any) -> Any:
    if isinstance(v, str):
        s = v.strip().lower()
        if s in _TRUE:
            return True
        if s in _FALSE:
            return False
        raise ValueError(f"not a boolean: {v!r}")
    return v


def _list(col: str, v: Any) -> Any:
    if not isinstance(v, str):
        return v
    s = v.strip()
    if not s:
        return []
    if s.startswith("["):
        out = json.loads(s)                                       # JSONDecodeError -> row rejected
        if not isinstance(out, list):
            raise ValueError(f"{col} is not a list")
        return out
    if col == "ticket_history":
        raise ValueError("ticket_history must be a JSON array")
    return [x.strip() for x in re.split(r"[;,]", s) if x.strip()]


def _split_labels(d: dict, intake: Intake) -> tuple[dict, dict | None]:
    """Remove label + eval-only keys from a raw record. Returns (agent-safe record, label or None)."""
    label = {k: d[k] for k in LABEL_KEYS if k in d and d[k] not in (None, "", [])}
    dropped = {k for k in d if k in DROP_KEYS}
    intake.dropped_columns |= dropped
    clean = {k: v for k, v in d.items() if k not in dropped}
    if "correct_routing" not in label:
        return clean, None
    label.setdefault("should_escalate", label["correct_routing"] == "l2_escalation")
    label.setdefault("relevant_kb_docs", [])
    return clean, label


def _add(intake: Intake, ref: str, n: Normalised, label: dict | None) -> None:
    tid = n.ticket.ticket_id
    if tid in intake.refs:
        intake.warnings.append(f"{ref}: duplicate ticket id {tid} (also in {intake.refs[tid]}); the later one is used")
        intake.items = [x for x in intake.items if x.ticket.ticket_id != tid]
    intake.items.append(n)
    intake.refs[tid] = ref
    if label:
        intake.labels[tid] = label
    intake.warnings += [f"{ref} ({tid}): {w}" for w in n.warnings]


def _record(intake: Intake, ref: str, raw: dict, default_id: str) -> None:
    intake.rows_read += 1
    try:
        d, label = _split_labels(raw, intake)
        for c in LIST_COLS:
            if c in d:
                d[c] = _list(c, d[c])
        for c in BOOL_COLS:
            if c in d:
                d[c] = _bool(d[c])
        if label:
            label["should_escalate"] = _bool(label["should_escalate"])
            label["relevant_kb_docs"] = _list("relevant_kb_docs", label["relevant_kb_docs"])
        if not str(d.get("ticket_id") or "").strip():
            d["ticket_id"] = default_id
            intake.warnings.append(f"{ref}: no ticket_id; assigned {default_id}")
        n = from_json(d, source=SOURCE, batch_id=intake.batch_id)
    except (TicketValidationError, ValueError) as e:              # JSONDecodeError is a ValueError
        intake.rejected.append({"ref": ref, "error": str(e)[:300]})
        return
    _add(intake, ref, n, label)


def _read_csv(p: Path, intake: Intake) -> None:
    text = p.read_text(encoding="utf-8-sig")
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        intake.rejected.append({"ref": p.name, "error": "empty CSV"})
        return
    header = [h.strip() for h in header]
    for row in reader:
        if not any(c.strip() for c in row):
            continue
        ref = f"{p.name}:{reader.line_num}"
        if len(row) != len(header):
            intake.rows_read += 1
            intake.rejected.append({"ref": ref, "error": f"row has {len(row)} cells, header has {len(header)}"})
            continue
        _record(intake, ref, dict(zip(header, row)), f"{p.stem}-R{reader.line_num}")


def _read_json(p: Path, intake: Intake) -> None:
    try:
        data = json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as e:
        intake.rejected.append({"ref": p.name, "error": f"invalid JSON: {e}"})
        return
    if p.name.lower() == "labels.json":
        rows = data if isinstance(data, list) else [{"ticket_id": k, **v} for k, v in data.items()]
        for r in rows:
            if isinstance(r, dict) and r.get("ticket_id") and r.get("correct_routing"):
                lab = {k: r[k] for k in LABEL_KEYS if k in r}
                lab.setdefault("should_escalate", lab["correct_routing"] == "l2_escalation")
                lab.setdefault("relevant_kb_docs", [])
                intake.labels.setdefault(r["ticket_id"], lab)      # labels in a ticket row win
        return
    rows = data if isinstance(data, list) else [data]
    for i, r in enumerate(rows, 1):
        ref = f"{p.name}[{i}]" if isinstance(data, list) else p.name
        if not isinstance(r, dict):
            intake.rows_read += 1
            intake.rejected.append({"ref": ref, "error": "ticket must be a JSON object"})
            continue
        _record(intake, ref, r, f"{p.stem}-{i}")


def _read_txt(p: Path, intake: Intake, known_versions: list[str]) -> None:
    intake.rows_read += 1
    try:
        n = from_text(p.read_text(encoding="utf-8-sig"), source=SOURCE, batch_id=intake.batch_id,
                      known_versions=known_versions)
    except TicketValidationError as e:
        intake.rejected.append({"ref": p.name, "error": str(e)})
        return
    _add(intake, p.name, n, None)


def read_batch(folder: Path, known_versions: list[str] | None = None) -> Intake:
    """Parse every .csv / .json / .txt file in the folder. Never raises for bad rows (they are reported)."""
    folder = Path(folder)
    if not folder.is_dir():
        raise FileNotFoundError(f"batch folder not found: {folder}")
    intake = Intake(batch_id=batch_name(folder))
    for p in sorted(folder.iterdir()):
        suffix = p.suffix.lower()
        if not p.is_file() or suffix not in (".csv", ".json", ".txt"):
            if p.is_file() and p.name.lower() != "readme.md":
                intake.warnings.append(f"{p.name}: skipped (not .csv / .json / .txt)")
            continue
        intake.files.append(p.name)
        if suffix == ".csv":
            _read_csv(p, intake)
        elif suffix == ".json":
            _read_json(p, intake)
        else:
            _read_txt(p, intake, known_versions or [])
    intake.labels = {t: v for t, v in intake.labels.items() if t in intake.refs}
    return intake


# --- load -------------------------------------------------------------------------------------

def load_batch(conn, intake: Intake, report_path: str | None = None) -> dict[str, int]:
    """Upsert the batch (source='panel_test'); tickets of this batch no longer in the folder are removed."""
    from ax15.l0_data.tickets_db import finish_batch_run, load_tickets, upsert_batch_run
    with conn.transaction():
        upsert_batch_run(conn, intake.batch_id, intake.batch_id, SOURCE)
        uids = load_tickets(conn, [n.ticket for n in intake.items])
        conn.execute("DELETE FROM tickets WHERE source = %s AND batch_id = %s AND NOT (ticket_id = ANY(%s))",
                     (SOURCE, intake.batch_id, list(intake.refs)))
        finish_batch_run(conn, intake.batch_id, total=intake.rows_read, loaded=len(intake.items),
                         rejected=len(intake.rejected), errors=intake.rejected, report_path=report_path)
    return uids


def clear_batch(conn, batch_id: str) -> int:
    """Delete one panel_test batch (tickets, history, batch_runs row). Train / ui data is never touched."""
    with conn.transaction():
        n = conn.execute("DELETE FROM tickets WHERE source = %s AND batch_id = %s", (SOURCE, batch_id)).rowcount
        conn.execute("DELETE FROM batch_runs WHERE batch_id = %s AND source = %s", (batch_id, SOURCE))
    return n


def known_versions(conn) -> list[str]:
    try:
        return [f"{r[0]} {r[1]}" for r in conn.execute("SELECT system, version FROM version_matrix").fetchall()]
    except Exception:  # noqa: BLE001 - optional hint for the free-text normaliser
        return []


# --- run --------------------------------------------------------------------------------------

def agent_module(name: str):
    if name == "v1":
        from ax15.agents import v1_single
        return v1_single
    if name == "v2":
        from ax15.agents import v2_graph
        return v2_graph
    raise ValueError(f"unknown agent {name!r} (v1 or v2)")


def _eval_module():
    """eval/agent_eval.py (not a package): its score_run / metrics score labelled batches like the dev eval."""
    if "agent_eval" in sys.modules:
        return sys.modules["agent_eval"]
    spec = importlib.util.spec_from_file_location("agent_eval", ROOT / "eval" / "agent_eval.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["agent_eval"] = mod
    spec.loader.exec_module(mod)
    return mod


def run_tickets(agent: str, batch_id: str, ids: list[str], workers: int = 4,
                progress: Callable[[int, int], None] | None = None) -> tuple[dict[str, dict], str | None]:
    """Run the agent on each ticket (eval mode: no action_log, no HITL, no memory). One DB connection per thread."""
    from ax15 import db
    from ax15.trust import spend
    mod = agent_module(agent)
    kwargs = {"memory": False} if agent == "v2" else {}
    local = threading.local()
    out: dict[str, dict] = {}
    lock = threading.Lock()
    stopped: list[str] = []

    def conn():
        c = getattr(local, "conn", None)
        if c is None or c.closed or c.broken:
            local.conn, _ = db.connect(autocommit=True)
        return local.conn

    def one(tid: str) -> None:
        if stopped:
            return
        try:
            spend.check()
            r = mod.run_agent(tid, batch_id=batch_id, split=SOURCE, conn=conn(), write_action=False, **kwargs).to_dict()
            if r["status"] == "error" and "spend" in (r.get("error") or "").lower():
                stopped.append(r["error"])
        except spend.SpendCapExceeded as e:
            stopped.append(str(e))
            return
        except Exception as e:  # noqa: BLE001 - one broken run must not stop the batch
            r = {"status": "error", "ticket_id": tid, "trace_id": "", "error": f"{type(e).__name__}: {str(e)[:200]}",
                 "answer": None, "tool_calls": 0, "cost_usd": 0.0, "latency_s": 0.0, "input_tokens": 0,
                 "output_tokens": 0, "trace_path": None, "langfuse_url": None}
        with lock:
            out[tid] = {**r, "agent": agent}
            done = len(out)
        if progress:
            progress(done, len(ids))

    with ThreadPoolExecutor(max(1, workers)) as pool:
        list(pool.map(one, ids))
    return out, (stopped[0] if stopped else None)


# --- report -----------------------------------------------------------------------------------

def _trace_link(run: dict) -> str:
    if run.get("langfuse_url"):
        return run["langfuse_url"]
    p = run.get("trace_path")
    if not p:
        return ""
    pp = Path(p)
    try:
        return pp.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        return str(p)


def ticket_rows(intake: Intake, runs: dict[str, dict]) -> list[dict]:
    """One row per loaded ticket: agent output only (no label fields)."""
    rows = []
    for n in intake.items:
        t = n.ticket
        r = runs.get(t.ticket_id) or {"status": "not_run"}
        a = r.get("answer") or {}
        fired = [f["id"] for f in a.get("fired_rules", []) if f.get("applied", True)]
        rows.append({
            "ticket_id": t.ticket_id, "source_ref": intake.refs[t.ticket_id], "status": r.get("status"),
            "routing": a.get("routing") or "", "verify_rules_routing": a.get("verify_rules_routing") or "",
            "mode": a.get("mode") or "", "escalate": a.get("should_escalate"),
            "priority_ticket": t.priority or "", "priority_agent": a.get("priority") or "",
            "fired_rules": " ".join(fired), "runbook": a.get("kb_doc") or "", "flags": " ".join(a.get("flags") or []),
            "cost_usd": round(r.get("cost_usd") or 0.0, 6), "latency_s": r.get("latency_s") or 0.0,
            "trace": _trace_link(r), "error": (r.get("error") or "")[:200],
        })
    return rows


def score(intake: Intake, runs: dict[str, dict]) -> tuple[dict | None, dict[str, dict]]:
    """Score labelled tickets with the dev eval's code. Returns (metrics, {ticket_id: per-ticket score})."""
    ids = [t for t in intake.refs if t in intake.labels and t in runs]
    if not ids:
        return None, {}
    from ax15 import db
    from ax15.trust import grounding
    ae = _eval_module()
    conn, _ = db.connect(autocommit=True)
    try:
        corpus = grounding.load_corpus(conn)
    finally:
        conn.close()
    scored = [ae.score_run(t, runs[t], intake.labels[t], corpus) for t in ids]
    per = {s.ticket_id: {"label_routing": s.label["correct_routing"], "label_escalate": s.label["should_escalate"],
                         "routing_ok": s.routing_collapsed, "kb_hit": s.kb_hit} for s in scored}
    return ae.metrics(scored), per


@dataclass
class BatchResult:
    batch_id: str
    agent: str
    intake: Intake
    runs: dict[str, dict]
    rows: list[dict]
    metrics: dict | None
    per_ticket_scores: dict[str, dict]
    stopped: str | None
    report_md: Path | None = None
    report_csv: Path | None = None

    def summary(self) -> dict:
        ok = [r for r in self.rows if r["status"] == "ok"]
        routing: dict[str, int] = {}
        for r in ok:
            routing[r["routing"]] = routing.get(r["routing"], 0) + 1
        lat = [r["latency_s"] for r in self.rows if r["status"] != "not_run"]
        return {"files": len(self.intake.files), "rows_read": self.intake.rows_read, "loaded": len(self.intake.items),
                "rejected": len(self.intake.rejected), "ran": len(self.runs), "ok": len(ok),
                "failed": len(self.runs) - len(ok), "routing": routing,
                "escalations": sum(bool(r["escalate"]) for r in ok), "with_flags": sum(bool(r["flags"]) for r in ok),
                "priority_changed": sum(r["priority_agent"] != r["priority_ticket"] for r in ok if r["priority_agent"]),
                "cost_usd": sum(r["cost_usd"] for r in self.rows),
                "median_latency_s": statistics.median(lat) if lat else 0.0, "labelled": len(self.per_ticket_scores)}


def agent_version(agent: str) -> str:
    try:
        return f"{'2.0' if agent == 'v2' else '1.0'} (prompt hash {agent_module(agent).prompt_hash()})"
    except Exception:  # noqa: BLE001
        return agent


def _pct(x) -> str:
    return "—" if x is None else f"{x:.1%}"


def render_md(res: BatchResult) -> str:
    s = res.summary()
    route = ", ".join(f"{k} {v}" for k, v in sorted(res.summary()["routing"].items(), key=lambda x: -x[1])) or "—"
    L = [f"# AX-15 batch report: `{res.batch_id}`", "",
         f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · agent **{agent_version(res.agent)}** · "
         f"source `panel_test`, batch_id `{res.batch_id}` · eval mode (no action_log writes)._", "",
         "## Summary", "",
         "| | |", "|---|---|",
         f"| Files read | {s['files']} ({', '.join(res.intake.files) or '—'}) |",
         f"| Tickets read / loaded / rejected | {s['rows_read']} / {s['loaded']} / {s['rejected']} |",
         f"| Agent runs ok / failed | {s['ok']} / {s['failed']} |",
         f"| Routing | {route} |",
         f"| Escalations to L2 | {s['escalations']} |",
         f"| Priority changed by agent | {s['priority_changed']} |",
         f"| Answers with flags | {s['with_flags']} |",
         f"| Cost (total) | ${s['cost_usd']:.4f} |",
         f"| Median latency | {s['median_latency_s']:.1f} s |"]
    if res.intake.dropped_columns:
        L.append(f"| Columns removed before the agent (eval-only) | {', '.join(sorted(res.intake.dropped_columns))} |")
    if res.metrics:
        m = res.metrics
        L += [f"| **Scored against labels** ({m['n']} tickets) | routing L1/L2 {_pct(m['routing_l1_l2'])} · exact "
              f"{_pct(m['routing_exact'])} · escalation precision {_pct(m['esc_precision'])} / recall "
              f"{_pct(m['esc_recall'])} · kb_doc hit {_pct(m['kb_doc_hit'])} · step fidelity "
              f"{_pct(m['step_fidelity'])} |"]
    else:
        L.append("| Scored against labels | no labels in this batch |")
    if res.stopped:
        L += ["", f"> **Stopped early:** {res.stopped}. The table covers the tickets that ran."]
    if res.intake.rejected:
        L += ["", f"## Rejected ({len(res.intake.rejected)}): reported and skipped", "", "| Where | Why |", "|---|---|"]
        L += [f"| `{e['ref']}` | {e['error'].replace('|', '/')} |" for e in res.intake.rejected]
    if res.intake.warnings:
        L += ["", "## Intake warnings", ""] + [f"- {w}" for w in res.intake.warnings]
    L += ["", "## Tickets", "",
          "| Ticket | From | Routing | Priority (ticket → agent) | Fired rules | Runbook | Flags | Cost | Latency | Trace |"
          + (" Label | L1/L2 ok |" if res.per_ticket_scores else ""),
          "|---|---|---|---|---|---|---|---:|---:|---|" + ("---|---|" if res.per_ticket_scores else "")]
    for r in res.rows:
        routing = r["routing"] or f"({r['status']})"
        if r["verify_rules_routing"]:
            routing += f" · verify: rules say {r['verify_rules_routing']}"
        if r["escalate"]:
            routing += " · escalate"
        trace = r["trace"]
        tl = (f"[Langfuse]({trace})" if trace.startswith("http") else f"`{Path(trace).name}`") if trace else "—"
        line = (f"| {r['ticket_id']} | `{r['source_ref']}` | {routing} | {r['priority_ticket'] or '—'} → "
                f"{r['priority_agent'] or '—'} | {r['fired_rules'] or '—'} | {r['runbook'] or '—'} | "
                f"{r['flags'].replace('|', '/') or '—'} | ${r['cost_usd']:.4f} | {r['latency_s']:.1f}s | {tl} |")
        if res.per_ticket_scores:
            p = res.per_ticket_scores.get(r["ticket_id"])
            line += (f" {p['label_routing']} | {'yes' if p['routing_ok'] else '**no**'} |" if p else " — | — |")
        L.append(line)
        if r["error"]:
            L.append(f"| | ↳ {r['error'].replace('|', '/')} | | | | | | | | |")
    return "\n".join(L) + "\n"


def write_reports(res: BatchResult, out_dir: Path | None = None) -> None:
    out_dir = out_dir or OUTPUTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    res.report_md = out_dir / f"batch_{res.batch_id}_report.md"
    res.report_csv = out_dir / f"batch_{res.batch_id}_report.csv"
    res.report_md.write_text(render_md(res), encoding="utf-8")
    cols = list(res.rows[0]) if res.rows else ["ticket_id"]
    extra = ["label_routing", "label_escalate", "routing_ok", "kb_hit"] if res.per_ticket_scores else []
    with res.report_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols + extra)
        w.writeheader()
        for r in res.rows:
            w.writerow({**r, **res.per_ticket_scores.get(r["ticket_id"], {})})


def run_batch(folder: Path | str, agent: str = "v2", workers: int = 4, conn=None,
              progress: Callable[[int, int], None] | None = None, out_dir: Path | None = None) -> BatchResult:
    """Intake -> load (panel_test) -> run (eval mode) -> score if labelled -> reports."""
    from ax15 import db
    agent_module(agent)                                           # fail fast on a bad --agent
    own = conn is None
    if own:
        conn, _ = db.connect(autocommit=True)
    try:
        intake = read_batch(Path(folder), known_versions(conn))
        report_rel = f"outputs/batch_{intake.batch_id}_report.md"
        load_batch(conn, intake, report_rel)
    finally:
        if own:
            conn.close()
    runs, stopped = run_tickets(agent, intake.batch_id, list(intake.refs), workers, progress)
    metrics_, per = score(intake, runs)
    res = BatchResult(intake.batch_id, agent, intake, runs, ticket_rows(intake, runs), metrics_, per, stopped)
    write_reports(res, out_dir)
    return res
