"""AX-15 1.0: a single tool-calling agent on the plain Anthropic SDK (no framework).

    python -m ax15.agents.v1_single --ticket INC-00162

Loop: base model from config/models.yaml, tools from tools/registry.py (read-only tools only).
Limits from config agents.v1: max tool calls, per-call and whole-run output tokens, wall-clock
timeout; hitting any limit ends the run with status "incomplete". The final answer is a JSON
object validated with V1Answer (one repair turn if it doesn't validate).

The only write: after a valid answer, one log_action row with status "proposed"
(verified_by_human=False) and this run's trace_id. A human approves it in the console.
Agent code never imports eval/ or reads eval.*: the ticket comes from tools.get_ticket
(prompt-safe fields) or from a UI ticket dict filtered to the same fields.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

import anthropic
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ax15.config import base_llm, models
from ax15.settings import ROOT
from ax15.tools import functions as tools_fn
from ax15.tools import registry
from ax15.trust import spend, tracing

AGENT_VERSION = "v1"
PROMPT_PATH = ROOT / "prompts" / "v1_system.md"
MODEL_TOOLS = ["get_store", "get_asset", "list_store_assets", "get_recent_tickets", "get_sla", "kb_route", "kb_section"]
# Prompt-safe ticket fields given to the model (same set as tools.models.TicketOut).
TICKET_FIELDS = ["ticket_id", "store_id", "store_name", "submitted_by", "date_opened", "priority", "category",
                 "subcategory", "subject", "description", "asset_id", "system_version", "tags", "escalation_flag",
                 "related_tickets", "ticket_status", "resolution_notes", "history"]
_HIDDEN_KEYS = {"usage"}                   # tool output keys kept for tracing, not sent to the model

Routing = Literal["l1_guided", "l1_self_service", "l2_escalation", "needs_clarification", "non_it"]


class L2Handoff(BaseModel):
    summary: str
    evidence: list[str] = []
    attempted_steps: list[str] = []
    requested_action: str | None = None


class V1Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    routing: Routing
    mode: Literal["handle", "verify"] = "handle"
    should_escalate: bool
    escalation_reason: str | None = None
    priority: Literal["P1", "P2", "P3", "P4"]
    kb_doc: str | None = None
    kb_section_breadcrumb: str | None = None
    steps: list[str] = []
    reply_to_store: str
    l2_handoff: L2Handoff | None = None
    flags: list[str] = []
    confidence: Literal["high", "medium", "low"]
    tools_used: list[str] = Field(default_factory=list, description="filled in by the agent loop")

    @model_validator(mode="after")
    def _handoff_when_escalating(self):
        if self.should_escalate and self.l2_handoff is None:
            raise ValueError("l2_handoff is required when should_escalate is true")
        if not self.should_escalate and self.l2_handoff is not None:
            raise ValueError("l2_handoff must be null when should_escalate is false")
        return self


@dataclass
class RunResult:
    status: Literal["ok", "incomplete", "invalid_json", "error", "refused"]
    ticket_id: str
    trace_id: str
    answer: dict | None = None
    error: str | None = None
    tool_calls: int = 0
    tools_used: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    action_id: int | None = None
    trace_path: str | None = None
    langfuse_url: str | None = None
    raw_text: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def limits() -> dict:
    return {"max_tool_calls": 12, "max_tokens": 4096, "max_output_tokens": 24000, "timeout_s": 180,
            **models().get("agents", {}).get(AGENT_VERSION, {})}


def system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def tool_defs() -> list[dict]:
    by = {s["name"]: s for s in registry.schemas()}
    return [by[n] for n in MODEL_TOOLS]


def prompt_hash() -> str:
    """Changes whenever the model, prompt, tool set or limits change (eval cache key)."""
    blob = json.dumps({"model": base_llm()["model"], "effort": base_llm().get("effort"), "system": system_prompt(),
                       "tools": tool_defs(), "limits": limits()}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def prompt_safe_ticket(ticket: dict) -> dict:
    t = {k: ticket.get(k) for k in TICKET_FIELDS if ticket.get(k) not in (None, [], "")}
    if "ticket_status" not in t and ticket.get("status") not in (None, "", "ok", "not_found", "mismatch"):
        t["ticket_status"] = ticket["status"]
    return json.loads(json.dumps(t, default=str))


def _ticket_message(t: dict) -> str:
    return ("Work this support ticket. Use the tools, then give the final JSON answer.\n\n<ticket>\n"
            + json.dumps(t, indent=1, sort_keys=True, ensure_ascii=False) + "\n</ticket>")


_JSON_RE = re.compile(r"\{.*\}", re.S)


def parse_answer(text: str) -> V1Answer:
    """Final answer text -> V1Answer (tolerates a code fence or text around one JSON object)."""
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    m = _JSON_RE.search(s)
    if not m:
        raise ValueError("no JSON object in the final answer")
    data = json.loads(m.group(0))
    data.pop("tools_used", None)
    return V1Answer.model_validate(data)


def execute_tool(name: str, args: dict, conn=None) -> tuple[dict, bool]:
    """Run a model-requested tool. Returns (output, is_error); never raises."""
    if name not in MODEL_TOOLS:
        return {"status": "error", "message": f"unknown tool {name!r}"}, True
    try:
        kw = {"conn": conn} if conn is not None else {}
        return registry.call(name, args, **kw), False
    except ValidationError as e:
        errs = "; ".join(f"{'.'.join(str(p) for p in er['loc'])}: {er['msg']}" for er in e.errors()[:5])
        return {"status": "error", "message": f"invalid arguments: {errs}"}, True
    except Exception as e:  # noqa: BLE001 - surface tool failures to the model, don't crash the run
        return {"status": "error", "message": f"{type(e).__name__}: {str(e)[:200]}"}, True


def _usage(resp) -> dict:
    u = resp.usage
    return {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
            "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0}


def load_ticket(ticket: str | dict, batch_id: str | None = None, conn=None) -> tuple[dict | None, str | None]:
    """(prompt-safe ticket dict, batch_id) from a ticket id or a UI ticket dict."""
    if isinstance(ticket, dict):
        return prompt_safe_ticket(ticket), ticket.get("batch_id") or batch_id
    t = tools_fn.get_ticket(ticket, batch_id, conn=conn)
    if t.status != "ok":
        return None, batch_id
    return prompt_safe_ticket(t.model_dump(mode="json")), t.batch_id


def run_agent(ticket: str | dict, *, batch_id: str | None = None, split: str | None = None, conn=None,
              client: anthropic.Anthropic | None = None, write_action: bool = True) -> RunResult:
    """Run AX-15 1.0 on one ticket (id or UI ticket dict). Returns the answer + trace_id."""
    lim = limits()
    cfg = base_llm()
    t, batch = load_ticket(ticket, batch_id, conn)
    tid_name = ticket if isinstance(ticket, str) else str(ticket.get("ticket_id") or "ui-ticket")
    trace_id = tracing.start_trace(tid_name, AGENT_VERSION, split, input=t)
    res = RunResult(status="error", ticket_id=tid_name, trace_id=trace_id)
    t0 = time.perf_counter()
    try:
        if t is None:
            res.error = "ticket not found"
            return res
        spend.check()
        client = client or anthropic.Anthropic()
        system, tools = system_prompt(), tool_defs()
        messages: list[dict] = [{"role": "user", "content": _ticket_message(t)}]
        out_tokens, repaired = 0, False
        while True:
            remaining = lim["timeout_s"] - (time.perf_counter() - t0)
            if remaining <= 0:
                res.status, res.error = "incomplete", "timeout"
                return res
            if out_tokens >= lim["max_output_tokens"]:
                res.status, res.error = "incomplete", "output-token cap"
                return res
            c0 = time.perf_counter()
            resp = client.messages.create(
                model=cfg["model"], max_tokens=lim["max_tokens"], system=system, tools=tools, messages=messages,
                output_config={"effort": cfg.get("effort", "low")}, cache_control={"type": "ephemeral"},
                timeout=max(remaining, 5),
            )
            u = _usage(resp)
            out_tokens += u["output_tokens"]
            res.input_tokens += u["input_tokens"] + u["cache_read_input_tokens"] + u["cache_creation_input_tokens"]
            res.output_tokens += u["output_tokens"]
            text = "".join(b.text for b in resp.content if b.type == "text")
            tracing.generation(trace_id, cfg["model"], u, time.perf_counter() - c0, name="v1_agent",
                               output=text[:2000] or [b.name for b in resp.content if b.type == "tool_use"])
            if resp.stop_reason == "refusal":
                res.status, res.error = "refused", "model refused"
                return res
            messages.append({"role": "assistant", "content": resp.content})   # unchanged (keeps thinking)
            if resp.stop_reason == "max_tokens":
                res.status, res.error = "incomplete", "per-call max_tokens reached"
                return res
            if resp.stop_reason == "pause_turn":
                continue
            calls = [b for b in resp.content if b.type == "tool_use"]
            if not calls:                                                  # final answer turn
                res.raw_text = text
                try:
                    ans = parse_answer(text)
                except (ValueError, ValidationError) as e:
                    if repaired:
                        res.status, res.error = "invalid_json", str(e)[:500]
                        return res
                    repaired = True
                    messages.append({"role": "user", "content":
                                     f"Your final answer did not validate: {str(e)[:600]}\n"
                                     "Reply with the corrected JSON object only."})
                    continue
                ans.tools_used = list(dict.fromkeys(res.tools_used))
                res.answer, res.status = ans.model_dump(mode="json"), "ok"
                if write_action and batch:
                    res.action_id = _propose(t["ticket_id"], batch, ans, trace_id, conn)
                return res
            results = []
            for b in calls:
                if res.tool_calls >= lim["max_tool_calls"]:
                    res.status, res.error = "incomplete", f"tool-call limit ({lim['max_tool_calls']}) reached"
                    return res
                res.tool_calls += 1
                res.tools_used.append(b.name)
                with tracing.span(trace_id, b.name, dict(b.input)) as sp:
                    out, is_err = execute_tool(b.name, dict(b.input), conn)
                    sp.status = "error" if is_err else out.get("status", "ok")
                    sp.output = out
                if b.name == "kb_route" and (out.get("usage") or {}).get("input_tokens"):
                    tracing.generation(trace_id, cfg["model"], out["usage"], 0.0, name="picker")
                visible = {k: v for k, v in out.items() if k not in _HIDDEN_KEYS}
                results.append({"type": "tool_result", "tool_use_id": b.id, "is_error": is_err,
                                "content": json.dumps(visible, sort_keys=True, ensure_ascii=False, default=str)})
            messages.append({"role": "user", "content": results})
    except spend.SpendCapExceeded as e:
        res.status, res.error = "error", str(e)
        return res
    except anthropic.APIError as e:
        res.status, res.error = "error", f"{type(e).__name__}: {str(e)[:300]}"
        return res
    finally:
        res.latency_s = round(time.perf_counter() - t0, 2)
        summary = tracing.end_trace(trace_id, (res.answer or {}).get("routing"), res.status, output=res.answer,
                                    extra={"tools_used": res.tools_used, "error": res.error,
                                           "action_id": res.action_id})
        res.cost_usd = summary.get("total_cost_usd", 0.0)
        res.trace_path, res.langfuse_url = summary.get("trace_path"), summary.get("langfuse_url")


def _propose(ticket_id: str, batch_id: str, ans: V1Answer, trace_id: str, conn) -> int | None:
    action = "escalate" if ans.should_escalate else "update"
    payload = {"routing": ans.routing, "priority": ans.priority, "kb_doc": ans.kb_doc,
               "escalation_reason": ans.escalation_reason, "reply_to_store": ans.reply_to_store,
               "steps": ans.steps, "l2_handoff": ans.l2_handoff.model_dump() if ans.l2_handoff else None,
               "flags": ans.flags, "agent_version": AGENT_VERSION}
    out = tools_fn.log_action(ticket_id, action, payload, actor="v1_agent", verified_by_human=False,
                              trace_id=trace_id, batch_id=batch_id, conn=conn)
    return out.action_id if out.status == "ok" else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticket", required=True)
    ap.add_argument("--batch", default=None)
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args()
    r = run_agent(a.ticket, batch_id=a.batch, write_action=not a.no_write)
    print(json.dumps(r.to_dict(), indent=1, ensure_ascii=False, default=str))
    return 0 if r.status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
