"""Tracing: one small interface for the agents.

    tid = start_trace(ticket_id, agent_version="v1", split="dev")
    with span(tid, "get_store", args) as s: ...; s.status = out["status"]
    generation(tid, model, usage, latency_s)
    end_trace(tid, routing="l2_escalation", status="ok")

Sinks:
- local JSON, always: outputs/traces/<version>/<ticket>_<trace_id>.json
- Langfuse, when LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST are set. Spans and
  generations are emitted live (real timings) under one root "agent" observation whose trace id
  is our trace_id; traces are tagged with agent version, ticket_id and split.
Any Langfuse problem (missing keys, unreachable host, SDK error) logs one warning, disables that
sink and never fails the run. The trace_id is also written to action_log.trace_id.
Costs come from config prices and are recorded in the session spend ledger (trust.spend).
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ax15.settings import OUTPUTS_DIR
from ax15.trust import spend

log = logging.getLogger("ax15.tracing")
TRACES_DIR = OUTPUTS_DIR / "traces"
_MAX_FIELD = 4000                  # chars of tool output kept per span in the local trace


# --- Langfuse sink ----------------------------------------------------------------------------

class _Langfuse:
    def __init__(self):
        self.client = None
        self.enabled = False
        self.reason = "not initialised"
        self._warned = False
        self._lock = threading.Lock()
        self._checked = False

    def keys_present(self) -> bool:
        return all(os.getenv(k) for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"))

    def get(self):
        with self._lock:
            if self._checked:
                return self.client if self.enabled else None
            self._checked = True
            if os.getenv("AX15_LANGFUSE", "1") == "0":
                self.reason = "disabled by AX15_LANGFUSE=0"
            elif not self.keys_present():
                self.reason = "LANGFUSE_* keys not set"
            else:
                try:
                    from langfuse import Langfuse
                    c = Langfuse(public_key=os.getenv("LANGFUSE_PUBLIC_KEY"), secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
                                 host=os.getenv("LANGFUSE_HOST"), timeout=5)
                    if not c.auth_check():
                        raise RuntimeError("auth_check failed")
                    self.client, self.enabled, self.reason = c, True, "ok"
                except Exception as e:  # noqa: BLE001 - tracing must never fail a run
                    self.reason = f"unavailable: {type(e).__name__}"
            if not self.enabled:
                self.warn(self.reason)
            return self.client if self.enabled else None

    def warn(self, reason: str) -> None:
        if not self._warned:
            self._warned = True
            log.warning("Langfuse tracing off (%s); local JSON traces only", reason)

    def fail(self, e: Exception) -> None:
        self.enabled = False
        self.reason = f"error: {type(e).__name__}"
        self.warn(self.reason)


LANGFUSE = _Langfuse()


def langfuse_status() -> tuple[bool, str]:
    LANGFUSE.get()
    return LANGFUSE.enabled, LANGFUSE.reason


# --- trace state ------------------------------------------------------------------------------

@dataclass
class Span:
    name: str
    args: dict
    started: float = field(default_factory=time.perf_counter)
    status: str = "ok"
    output: Any = None
    latency_ms: int | None = None
    error: str | None = None
    lf: Any = None


@dataclass
class Trace:
    trace_id: str
    ticket_id: str
    agent_version: str
    split: str | None
    started_at: str
    t0: float
    events: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    lf_root: Any = None
    lf_stack: Any = None
    path: Path | None = None


_traces: dict[str, Trace] = {}
_tlock = threading.Lock()


def _lf_safe(fn, *a, **kw):
    if not LANGFUSE.enabled:
        return None
    try:
        return fn(*a, **kw)
    except Exception as e:  # noqa: BLE001
        LANGFUSE.fail(e)
        return None


def start_trace(ticket_id: str, agent_version: str = "v1", split: str | None = None,
                input: dict | None = None, trace_id: str | None = None) -> str:
    tid = trace_id or uuid.uuid4().hex           # 32 lowercase hex = valid Langfuse/W3C trace id
    tr = Trace(tid, ticket_id, agent_version, split, datetime.now(timezone.utc).isoformat(), time.perf_counter())
    client = LANGFUSE.get()
    if client is not None:
        def _open():
            from langfuse import propagate_attributes
            stack = contextlib.ExitStack()
            root = stack.enter_context(client.start_as_current_observation(
                trace_context={"trace_id": tid}, name=f"ax15-{agent_version}", as_type="agent", input=input,
                metadata={"ticket_id": ticket_id, "split": split or ""}))
            stack.enter_context(propagate_attributes(
                trace_name=f"ax15-{agent_version} {ticket_id}", version=agent_version,
                tags=[f"agent:{agent_version}", f"ticket:{ticket_id}", f"split:{split or 'none'}"],
                metadata={"ticket_id": ticket_id, "split": split or "none"}))
            tr.lf_root, tr.lf_stack = root, stack
        _lf_safe(_open)
    with _tlock:
        _traces[tid] = tr
    return tid


def _get(tid: str) -> Trace | None:
    with _tlock:
        return _traces.get(tid)


@contextlib.contextmanager
def span(tid: str, name: str, args: dict, kind: str = "tool"):
    """Wrap one tool call (kind="tool") or graph node (kind="span"). Set .status / .output on the yielded Span."""
    tr = _get(tid)
    s = Span(name, args)
    if tr is not None and tr.lf_root is not None:
        s.lf = _lf_safe(tr.lf_root.start_observation, name=name, as_type=kind, input=args)
    try:
        yield s
    except Exception as e:
        s.status, s.error = "error", f"{type(e).__name__}: {e}"
        raise
    finally:
        s.latency_ms = int((time.perf_counter() - s.started) * 1000)
        if tr is not None:
            out = s.output
            text = out if isinstance(out, str) else json.dumps(out, default=str)
            tr.events.append({"type": "span" if kind == "tool" else "node", "name": name, "args": args, "status": s.status,
                              "latency_ms": s.latency_ms, "error": s.error,
                              "output": text[:_MAX_FIELD] + ("…" if len(text) > _MAX_FIELD else "")})
        if s.lf is not None:
            _lf_safe(lambda: s.lf.update(output=s.output, level="ERROR" if s.status == "error" else None,
                                         status_message=s.status).end())


def generation(tid: str, model: str, usage: dict, latency_s: float, name: str = "llm",
               input: Any = None, output: Any = None, price: dict | None = None) -> float:
    """Record one LLM call; usage keys: input_tokens, output_tokens, cache_read_input_tokens,
    cache_creation_input_tokens. Returns its cost (also added to the session spend ledger)."""
    it, ot = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    cr, cw = int(usage.get("cache_read_input_tokens") or 0), int(usage.get("cache_creation_input_tokens") or 0)
    cost = spend.cost_of(it, ot, cr, cw, price=price)
    spend.record(cost, name)
    tr = _get(tid)
    if tr is not None:
        tr.cost_usd += cost
        tr.input_tokens += it
        tr.output_tokens += ot
        tr.cache_read_tokens += cr
        tr.cache_write_tokens += cw
        tr.events.append({"type": "generation", "name": name, "model": model, "input_tokens": it,
                          "output_tokens": ot, "cache_read_tokens": cr, "cache_write_tokens": cw,
                          "cost_usd": round(cost, 6), "latency_ms": int(latency_s * 1000)})
        if tr.lf_root is not None:
            def _gen():
                g = tr.lf_root.start_observation(
                    name=name, as_type="generation", model=model, input=input, output=output,
                    usage_details={"input": it, "output": ot, "cache_read_input_tokens": cr,
                                   "cache_creation_input_tokens": cw},
                    cost_details={"total": cost})
                g.end()
            _lf_safe(_gen)
    return cost


def end_trace(tid: str, routing: str | None, status: str, output: Any = None, extra: dict | None = None) -> dict:
    """Close the trace, write the local JSON file, flush Langfuse. Returns the trace summary."""
    tr = _get(tid)
    if tr is None:
        return {}
    total_s = time.perf_counter() - tr.t0
    summary = {
        "trace_id": tid, "ticket_id": tr.ticket_id, "agent_version": tr.agent_version, "split": tr.split,
        "started_at": tr.started_at, "status": status, "routing": routing,
        "total_latency_s": round(total_s, 2), "total_cost_usd": round(tr.cost_usd, 6),
        "input_tokens": tr.input_tokens, "output_tokens": tr.output_tokens,
        "cache_read_tokens": tr.cache_read_tokens, "cache_write_tokens": tr.cache_write_tokens,
        "tool_calls": sum(1 for e in tr.events if e["type"] == "span"),
        "langfuse": LANGFUSE.enabled and tr.lf_root is not None,
        "langfuse_url": None, **(extra or {}),
    }
    if tr.lf_root is not None:
        def _close():
            tr.lf_root.update(output=output if output is not None else {"routing": routing, "status": status},
                              metadata={"status": status, "routing": routing or "",
                                        "cost_usd": round(tr.cost_usd, 6)})
            tr.lf_stack.close()
            LANGFUSE.client.flush()
            summary["langfuse_url"] = LANGFUSE.client.get_trace_url(trace_id=tid)
        _lf_safe(_close)
    summary["langfuse"] = bool(summary["langfuse_url"])
    path = TRACES_DIR / tr.agent_version / f"{tr.ticket_id}_{tid}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**summary, "events": tr.events, "final_output": output}, indent=1, default=str),
                        encoding="utf-8")
        summary["trace_path"] = str(path)
    except OSError as e:
        log.warning("could not write local trace %s: %s", path, e)
    with _tlock:
        _traces.pop(tid, None)
    return summary
