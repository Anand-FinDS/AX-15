"""AX-15 2.0: a LangGraph workflow. Deterministic nodes decide; three narrow LLM roles extract, select, write.

    python -m ax15.agents.v2_graph --ticket INC-00031 [--no-write] [--approve | --reject]

    load -> enrich -> classify -> policy -> route -> resolve -> decide -> compose -> guard -> hitl_propose -> hitl_review
                                          \\ (non_it / clarify skip route + resolve)  (verify / no doc skip resolve)

- load (code): get_ticket, or a UI ticket dict filtered to prompt-safe fields.
- enrich (code): get_store, get_asset(claimed_system), list_store_assets, get_recent_tickets (30 days before
  date_opened, excluding this ticket), get_sla, get_past_actions (human-verified only). Always all of them.
- classify (LLM, prompts/v2_classify.md): facts only (ClassifyOut). Ticket status is read in code.
- policy (code): builds facts (asset resolution, claim checks, unknown systems / error codes, recurrence,
  peak hour) and runs trust/sop_rules.evaluate.
- route (code + picker LLM): route_doc on the raw subject+description AND on classify's clean_symptom,
  merged (dedupe, max 5) -> pick_doc. routing_query records which query found the chosen doc.
- resolve (LLM, prompts/v2_resolve.md): picks step ids from the runbook items; code renders the text verbatim.
- decide (code): sop_rules.evaluate again with the route/resolve facts (no doc, L2-only, all steps tried,
  runbook escalation criteria) -> final routing, priority, steps, handoff fields.
- compose (LLM, prompts/v2_compose.md): reply_to_store + handoff text from structured facts; no steps.
- guard (code, trust/guard.py): verbatim steps, no L2-only text, no steps for unknown systems, reply ids /
  numbers / device names grounded in sources, schema. Strips + flags; escalates if nothing safe remains.
- hitl_propose (code): log_action status 'proposed' with this run's trace_id.
- hitl_review: LangGraph interrupt; approve / reject resumes the graph (trust/hitl.review_action).

Checkpointer (HITL runs): Postgres (langgraph-checkpoint-postgres, tables from migration 010) on the pooled
Neon connection; SQLite in outputs/ if Postgres is unavailable. Eval runs (write_action=False) stop after
guard and use no checkpointer. Agent code never imports eval/ or reads eval.*.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import json
import logging
import operator
import re
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal, TypedDict

import anthropic
from pydantic import ValidationError

from ax15.agents import llm
from ax15.agents.v1_single import prompt_safe_ticket
from ax15.agents.v2_schemas import ASSET_TYPE, ClassifyOut, ComposeOut, ResolveOut, V2Answer
from ax15.config import base_llm, models
from ax15.l1_knowledge import picker as picker_mod
from ax15.l1_knowledge.search import route_doc
from ax15.l1_knowledge.steps import doc_steps, render_for_prompt
from ax15.settings import OUTPUTS_DIR, ROOT
from ax15.tools import _db
from ax15.tools import functions as T
from ax15.tools.functions import parse_claimed
from ax15.trust import grounding, guard, hitl, sop_rules, spend, tracing

log = logging.getLogger("ax15.v2")
AGENT_VERSION = "v2"
PROMPTS = {n: ROOT / "prompts" / f"v2_{n}.md" for n in ("classify", "resolve", "compose")}
IST = timezone(timedelta(hours=5, minutes=30))          # SOP hours are IST (no DST)
CLOSED = ("resolved", "closed")
MAX_STEPS = 6
MAX_INTERIM = 3
_SOURCE_FILES = ["src/ax15/agents/v2_graph.py", "src/ax15/agents/v2_schemas.py", "src/ax15/trust/guard.py",
                 "src/ax15/trust/sop_rules.py", "src/ax15/l1_knowledge/steps.py", "config/sop_rules.yaml"]


def limits() -> dict:
    return {"max_llm_calls": 8, "timeout_s": 180, "route_max_candidates": 5,
            **models().get("agents", {}).get(AGENT_VERSION, {})}


def prompt_hash() -> str:
    """Eval cache key: changes with the model, prompts, rules, limits or the graph code."""
    h = hashlib.sha256()
    h.update(json.dumps({"model": base_llm()["model"], "effort": base_llm().get("effort"), "limits": limits(),
                         "nodes": models().get("nodes"), "picker": picker_mod.SYSTEM_N,
                         "routing": models().get("routing")}, sort_keys=True).encode())
    for p in list(PROMPTS.values()) + [ROOT / f for f in _SOURCE_FILES]:
        h.update(p.read_bytes())
    return h.hexdigest()[:16]


# --- run context (not checkpointed: connections, clients, deadline) --------------------------------

class RunTimeout(RuntimeError):
    pass


class LLMCap(RuntimeError):
    pass


@dataclass
class RunCtx:
    conn: Any = None
    deadline: float = 0.0
    llm_calls: int = 0
    max_llm_calls: int = 8
    tools: list = field(default_factory=list)


_CTX: dict[str, RunCtx] = {}
_CTX_LOCK = threading.Lock()


def _ctx(tid: str) -> RunCtx:
    with _CTX_LOCK:
        return _CTX.setdefault(tid, RunCtx(deadline=time.perf_counter() + limits()["timeout_s"]))


class State(TypedDict, total=False):
    trace_id: str
    ticket_ref: Any
    batch_id: str | None
    split: str | None
    write_action: bool
    memory: bool
    ticket: dict
    enrich: dict
    classify: dict | None
    facts: dict
    extra: dict
    policy: dict
    route: dict
    resolve: dict
    decision: dict
    compose: dict | None
    answer: dict
    guard: dict
    action_id: int | None
    review: dict
    final_facts: dict
    node_path: Annotated[list[str], operator.add]
    models: Annotated[list[str], operator.add]
    tool_calls: Annotated[int, operator.add]
    llm_failed: Annotated[list[str], operator.add]


def _short(v: Any, n: int = 1500) -> Any:
    s = json.dumps(v, default=str)
    return v if len(s) <= n else s[:n] + "…"


def node(name: str):
    """Wrap a node: deadline check, one 'node:<name>' span, append to node_path."""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(state: State) -> dict:
            tid = state["trace_id"]
            ctx = _ctx(tid)
            if time.perf_counter() > ctx.deadline:
                raise RunTimeout(f"timeout before node {name}")
            with tracing.span(tid, f"node:{name}", {"node": name}, kind="span") as sp:
                out = fn(state, ctx) or {}
                sp.output = _short({k: v for k, v in out.items() if k not in ("node_path", "tool_calls")})
            out["node_path"] = [name]
            return out
        return wrapper
    return deco


def _tool(tid: str, ctx: RunCtx, name: str, fn, **kw):
    """Run one L2 tool inside a tool span; returns the Pydantic output."""
    ctx.tools.append(name)
    with tracing.span(tid, name, {k: v for k, v in kw.items()}) as sp:
        out = fn(**kw, conn=ctx.conn)
        sp.status = getattr(out, "status", "ok")
        sp.output = out.model_dump(mode="json") if hasattr(out, "model_dump") else out
    return out


def _llm(tid: str, ctx: RunCtx, node_name: str, user: str, schema):
    if ctx.llm_calls >= ctx.max_llm_calls:
        raise LLMCap(f"LLM call cap ({ctx.max_llm_calls}) reached at {node_name}")
    res = llm.structured(f"v2_{node_name}", PROMPTS[node_name].read_text(encoding="utf-8"), user, schema, trace_id=tid)
    ctx.llm_calls += res.calls
    return res


def _dt(v) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


# --- cached reference text (KB corpus, L2-only text, CMDB vocabulary, SOP) ----------------------------

_REF: dict[str, Any] = {}
_REF_LOCK = threading.Lock()


def _ref(conn) -> dict:
    with _REF_LOCK:
        if not _REF:
            def q(c):
                corpus = grounding.load_corpus(c)
                l2 = [r[0] for r in c.execute("SELECT text FROM kb_chunks WHERE metadata->>'audience' = 'L2'")]
                vocab = {r[0] for r in c.execute("SELECT DISTINCT make FROM assets WHERE make IS NOT NULL")}
                vocab |= {r[0] for r in c.execute("SELECT DISTINCT model FROM assets WHERE model IS NOT NULL")}
                vocab |= {r[0] for r in c.execute("SELECT DISTINCT system FROM version_matrix")}
                sop = "\n".join(r[0] for r in c.execute(
                    "SELECT text FROM kb_chunks WHERE doc_path LIKE 'sop/%' ORDER BY doc_path, chunk_index"))
                return corpus, l2, sorted(v for v in vocab if v), sop
            corpus, l2, vocab, sop = _db.run(q, conn)
            _REF.update(corpus=corpus, corpus_lower=corpus.lower(), l2=l2, vocab=vocab, sop=sop)
        return _REF


def _norm(s: str | None) -> str:
    return re.sub(r"[\s\-_.]+", "", (s or "").lower())


_VER = re.compile(r"\s+(v?\d+(\.\d+)*[a-z]?)$", re.I)


def _base_name(name: str) -> str:
    n = name.strip()
    while _VER.search(n):
        n = _VER.sub("", n)
    return n


# --- nodes ---------------------------------------------------------------------------------------

@node("load")
def n_load(state: State, ctx: RunCtx) -> dict:
    ref = state["ticket_ref"]
    if isinstance(ref, dict):
        return {"ticket": prompt_safe_ticket(ref), "batch_id": ref.get("batch_id") or state.get("batch_id")}
    t = _tool(state["trace_id"], ctx, "get_ticket", T.get_ticket, ticket_id=ref, batch_id=state.get("batch_id"))
    if t.status != "ok":
        raise LookupError(f"ticket {ref} not found")
    return {"ticket": prompt_safe_ticket(t.model_dump(mode="json")), "batch_id": t.batch_id, "tool_calls": 1}


def _sla_now(opened: datetime | None) -> datetime | None:
    """Fresh tickets: now. Historical tickets (eval): their open time, so SLA clocks are as at intake."""
    if opened is None:
        return None
    return None if datetime.now(timezone.utc) - opened < timedelta(days=7) else opened


@node("enrich")
def n_enrich(state: State, ctx: RunCtx) -> dict:
    tid, t = state["trace_id"], state["ticket"]
    opened = _dt(t.get("date_opened"))
    store_id, asset_id = t.get("store_id"), t.get("asset_id")
    e: dict = {}
    n = 0
    e["store"] = _tool(tid, ctx, "get_store", T.get_store, store_id=store_id or "").model_dump(mode="json")
    n += 1
    if asset_id:
        e["asset"] = _tool(tid, ctx, "get_asset", T.get_asset, asset_id=asset_id,
                           claimed_system=t.get("system_version")).model_dump(mode="json")
        n += 1
    else:
        e["asset"] = {"status": "not_found", "asset_id": None, "message": "ticket has no asset id"}
    e["store_assets"] = _tool(tid, ctx, "list_store_assets", T.list_store_assets,
                              store_id=store_id or "").model_dump(mode="json")
    e["recent"] = _tool(tid, ctx, "get_recent_tickets", T.get_recent_tickets, store_id=store_id or "", asset_id=None,
                        days=30, as_of=opened, exclude_ticket_id=t.get("ticket_id")).model_dump(mode="json")
    e["sla"] = _tool(tid, ctx, "get_sla", T.get_sla, priority=t.get("priority") or "P3",
                     category=t.get("category") or "General", created_at=opened or datetime.now(timezone.utc),
                     now=_sla_now(opened)).model_dump(mode="json")
    n += 3
    if state.get("memory", True):
        e["past_actions"] = _tool(tid, ctx, "get_past_actions", T.get_past_actions, store_id=store_id or "",
                                  asset_id=None, days=90).model_dump(mode="json")
        n += 1
    else:
        e["past_actions"] = {"status": "ok", "actions": [], "message": "memory off for this run"}
    return {"enrich": e, "tool_calls": n}


def _context_for_classify(e: dict) -> dict:
    a = e.get("asset") or {}
    return {
        "asset": {k: a.get(k) for k in ("status", "asset_id", "asset_type", "vendor", "model", "system", "version",
                                         "known_bugs", "patch_available", "message")},
        "store_assets": [{k: x.get(k) for k in ("asset_id", "asset_type", "system", "version")}
                         for x in (e.get("store_assets") or {}).get("assets", [])],
        "recent_tickets_30d": [{k: x.get(k) for k in ("ticket_id", "date_opened", "category", "subject",
                                                       "ticket_status")}
                               for x in (e.get("recent") or {}).get("tickets", [])[:10]],
        "past_verified_actions": (e.get("past_actions") or {}).get("actions", [])[:5],
    }


@node("classify")
def n_classify(state: State, ctx: RunCtx) -> dict:
    t = {k: v for k, v in state["ticket"].items() if k != "ticket_status"}
    user = ("<ticket>\n" + json.dumps(t, indent=1, ensure_ascii=False, default=str) + "\n</ticket>\n\n<context>\n"
            + json.dumps(_context_for_classify(state["enrich"]), indent=1, ensure_ascii=False, default=str)
            + "\n</context>")
    res = _llm(state["trace_id"], ctx, "classify", user, ClassifyOut)
    out = {"models": [f"classify:{res.model}"]}
    if res.parsed is None:
        return {**out, "classify": None, "llm_failed": ["classify"]}
    return {**out, "classify": res.parsed.model_dump(mode="json")}


def resolve_asset(ticket: dict, e: dict, c: dict, ctx: RunCtx | None = None, tid: str | None = None) -> dict:
    """Which CMDB asset the ticket is about (code). Returns asset facts + flags."""
    a = e.get("asset") or {}
    assets = (e.get("store_assets") or {}).get("assets", [])
    want = ASSET_TYPE.get((c or {}).get("affected_device_type") or "")
    flags: list[str] = []
    out = {"asset": a, "asset_unverified": False, "system_mismatch": False, "asset_id": ticket.get("asset_id"),
           "retargeted_from": None}
    found = a.get("status") in ("ok", "mismatch")
    cands = [x for x in assets if want and x.get("asset_type") == want]
    out["candidates"] = [{k: x.get(k) for k in ("asset_id", "asset_type", "model", "system", "version")} for x in cands]
    out["ask_which_device"] = False
    out["needs_model_check"] = False
    out["cmdb_correction"] = None

    def ambiguous():
        """Several candidates: same model/system -> proceed and ask which; different models -> decide checks
        whether the chosen runbook covers every model (else clarify, listing the candidates)."""
        models_ = {_norm(x.get("model") or x.get("system")) for x in cands}
        if len(models_) == 1:
            flags.append(f"device_ambiguous:{len(cands)} {want} assets, same model")
            out["ask_which_device"] = True
        else:
            flags.append(f"device_ambiguous_models:{len(cands)} {want} assets, {len(models_)} models")
            out["needs_model_check"] = True

    def retarget(x):
        new = T.get_asset(x["asset_id"], claimed_system=ticket.get("system_version"),
                          conn=ctx.conn if ctx else None)
        flags.append(f"asset_retargeted:{ticket.get('asset_id') or 'none'}->{x['asset_id']}")
        out.update(asset=new.model_dump(mode="json"), asset_id=x["asset_id"], retargeted_from=ticket.get("asset_id"))

    if found and want and a.get("asset_type") != want:
        if len(cands) == 1:
            retarget(cands[0])
        elif not cands:
            flags.append(f"cmdb_gap:no {want} registered at store")
        else:
            ambiguous()
    elif not found:
        tid_asset = ticket.get("asset_id")
        if tid_asset:                                               # id given but not in the CMDB
            out["cmdb_correction"] = {"ticket_asset_id": tid_asset, "asset_type": want,
                                      "candidates": out["candidates"],
                                      "suggested": cands[0]["asset_id"] if len(cands) == 1 else None}
        if not tid_asset and not want:
            pass                                                    # nothing to verify (e.g. online ordering)
        elif len(cands) == 1:
            retarget(cands[0])
        elif want and not cands and not tid_asset:
            flags.append(f"cmdb_gap:no {want} registered at store")
        elif cands and not tid_asset:
            ambiguous()
        else:
            out["asset_unverified"] = True
            flags.append(f"asset_not_in_cmdb:{tid_asset or 'none'}")
    fa = out["asset"]
    if fa.get("status") == "mismatch" and fa.get("mismatch_kind") == "system":
        if out["retargeted_from"] is not None or any(f.startswith("asset_retargeted") for f in flags):
            flags.append("system_check_skipped:retargeted")         # the claimed system named the original device
        elif any(f.startswith("cmdb_gap") for f in flags):
            flags.append("system_mismatch_ignored:cmdb_gap")        # CMDB has no device of this type to match
        else:
            out["system_mismatch"] = True
    elif fa.get("status") == "mismatch" and fa.get("mismatch_kind") == "version":
        flags.append(f"version_mismatch:{fa.get('message')}")
    out["flags"] = flags
    return out


_CODE = re.compile(r"(?i)(0x[0-9a-f]{2,}|[a-z]{0,6}[-_ ]?\d{1,6}[a-z]?|code\s*\d+|err(or)?[-_ ]?\d+)")


def is_error_code(s: str) -> bool:
    """Code-shaped tokens only (0x8004, E-402, ERR-4521, Code 43, E04); error message text is not a code."""
    s = s.strip()
    return len(s) <= 20 and any(ch.isdigit() for ch in s) and bool(_CODE.fullmatch(s))


def build_facts(ticket: dict, e: dict, c: dict | None, ref: dict, ctx: RunCtx | None = None,
                tid: str | None = None, llm_failed: list[str] | None = None) -> tuple[dict, dict]:
    """Facts for sop_rules.evaluate (code only) + extra context (flags, claim checks, asset resolution)."""
    c = c or {}
    flags: list[str] = []
    status = (ticket.get("ticket_status") or "").strip()
    opened = _dt(ticket.get("date_opened"))
    f: dict = {k: v for k, v in c.items() if isinstance(v, (bool, int)) and k not in ("devices_affected",)}
    f.update(devices_affected=c.get("devices_affected"), ticket_status=status or "Open",
             ticket_closed=status.lower() in CLOSED, ticket_priority=ticket.get("priority"),
             is_it=any(i.get("kind") == "it" for i in c.get("intents") or []) if c else True,
             clarity_vague=c.get("clarity") == "vague",
             store_type=(e.get("store") or {}).get("store_type"),
             opened_hour_ist=None if opened is None else round(opened.astimezone(IST).hour
                                                               + opened.astimezone(IST).minute / 60, 2))
    if llm_failed:
        f["llm_failed"] = ", ".join(llm_failed)

    # asset
    ar = resolve_asset(ticket, e, c, ctx, tid)
    flags += ar["flags"]
    a = ar["asset"]
    f.update(asset_unverified=ar["asset_unverified"], system_mismatch=ar["system_mismatch"],
             asset_id=ar["asset_id"] or ticket.get("asset_id") or "none",
             claimed_system=ticket.get("system_version") or "?",
             cmdb_system=" ".join(x for x in (a.get("system"), a.get("version")) if x) or "?")

    # unknown systems / error codes (KB text, CMDB and version matrix)
    vocab = {_norm(v) for v in ref["vocab"]}
    names = list(c.get("systems_mentioned") or [])
    if ticket.get("system_version"):
        names.append(ticket["system_version"])
    unknown = []
    for nm in dict.fromkeys(names):
        base = _base_name(nm)
        if len(base) < 3:
            continue
        toks = set(re.findall(r"[a-z0-9]+", base.lower()))
        known = base.lower() in ref["corpus_lower"] or _norm(base) in vocab or any(
            _norm(base) in v or v in _norm(base) for v in vocab if len(v) >= 5) or any(
            (vt := set(re.findall(r"[a-z0-9]+", str(name).lower()))) and len(vt) >= 2 and vt <= toks
            for name in ref["vocab"])       # "ServeWell online ordering portal" ~ "ServeWell Online Portal"
        if not known:
            sys_name, _ = parse_claimed(nm, list(ref["vocab"]))
            if not (sys_name and (_norm(sys_name) in vocab or sys_name.lower() in ref["corpus_lower"])):
                unknown.append(base)
    f["unknown_system"] = bool(unknown)
    f["unknown_systems"] = unknown
    codes = [x.strip().strip("'\"") for x in (c.get("error_codes") or []) if x and x.strip()]
    codes = [x for x in codes if is_error_code(x)]          # messages like "No DHCP lease" are not codes
    f["unknown_error_codes"] = [x for x in codes if x.lower() not in ref["corpus_lower"]]
    if f["unknown_error_codes"]:
        flags.append("error_code_not_in_kb:" + ",".join(f["unknown_error_codes"][:3]))

    # recurrence: same issue at the store in the 30 days before (SOP: "more than twice in 30 days")
    sub, cat, aid = (ticket.get("subcategory") or "").lower(), ticket.get("category"), ticket.get("asset_id")
    same = {x["ticket_id"] for x in (e.get("recent") or {}).get("tickets", [])
            if (sub and (x.get("subcategory") or "").lower() == sub)
            or (aid and x.get("asset_id") == aid and x.get("category") == cat)}
    approved_esc = [p for p in (e.get("past_actions") or {}).get("actions", [])
                    if p.get("verdict") in ("approved", "executed") and p.get("action_type") == "escalate_to_l2"
                    and p.get("asset_id") and p.get("asset_id") == (ar["asset_id"] or aid)
                    and (_dt(p.get("date")) or datetime.now(timezone.utc)) >= datetime.now(timezone.utc) - timedelta(days=30)]
    for p in approved_esc:
        same.add(p["ticket_id"])
        flags.append(f"past_escalation:{p['ticket_id']} approved by {p.get('verified_by')}")
    same.discard(ticket.get("ticket_id"))
    f["recurrence_count"] = len(same)
    if same:
        flags.append(f"recurrence:{len(same)} in 30d")

    # proposed-rule facts (SOP-PROPOSED-NET, inactive: what-if only)
    sym = (c.get("clean_symptom") or "").lower()
    dev = c.get("affected_device_type")
    f["network_issue"] = bool(dev == "network_router" or c.get("network_infrastructure_down")
                              or re.search(r"network|wi-?fi|connect|dhcp|ip address|internet|router", sym))
    f["pos_affected"] = bool(dev in ("pos_terminal", "payment_terminal") or c.get("payment_failure")
                             or re.search(r"\bpos\b|terminal|checkout|payment", sym))
    f["during_service"] = bool(c.get("customer_facing") or (f["opened_hour_ist"] is not None and any(
        lo <= f["opened_hour_ist"] < hi for lo, hi in ((11, 14), (18, 21)))))

    # known issue with a documented fix
    patch = (a.get("patch_available") or "").strip().lower()
    f["known_issue_workaround"] = bool(c.get("matches_known_bug")) and patch.startswith(("yes", "partial"))

    # claims: check referenced tickets in code
    claims = []
    for cl in c.get("claims_to_verify") or []:
        ref_id = (cl.get("ticket_ref") or "").strip().upper()
        res = {"claim": cl.get("claim"), "ticket_ref": ref_id or None, "verified": False, "finding": "not checkable"}
        if re.fullmatch(r"INC-\d{3,6}", ref_id):
            rt = T.get_ticket(ref_id, conn=ctx.conn if ctx else None)
            if rt.status != "ok":
                res["finding"] = f"{ref_id} does not exist"
            else:
                st = (rt.ticket_status or "").lower()
                diff = rt.store_id != ticket.get("store_id")
                res["finding"] = (f"{ref_id} is {rt.ticket_status}" + (f", at another store ({rt.store_id})" if diff else "")
                                  + f", category {rt.category}")
                res["verified"] = st in CLOSED and not diff
        claims.append(res)
        if ref_id and not res["verified"]:
            flags.append(f"unverified_claim:{ref_id}")
    extra = {"flags": flags, "asset": a, "asset_id": ar["asset_id"], "retargeted_from": ar["retargeted_from"],
             "claims": claims, "candidates": ar["candidates"], "ask_which_device": ar["ask_which_device"],
             "needs_model_check": ar["needs_model_check"], "cmdb_correction": ar["cmdb_correction"],
             "non_it_redirects": [guard.redact(i.get("summary") or "",
                                               guard.employee_ids(ticket.get("description") or ""))
                                  for i in c.get("intents") or [] if i.get("kind") == "non_it"]}
    return f, extra


@node("policy")
def n_policy(state: State, ctx: RunCtx) -> dict:
    ref = _ref(ctx.conn)
    facts, extra = build_facts(state["ticket"], state["enrich"], state.get("classify"), ref, ctx, state["trace_id"],
                               state.get("llm_failed"))
    pol = sop_rules.evaluate(facts)
    return {"facts": facts, "extra": extra, "policy": pol}


def merge_candidates(raw: list, symptom: list, n: int = 5) -> list[dict]:
    """Union of two route_doc results (DocRoute lists): best score per doc, which queries found it, top n."""
    by: dict[str, dict] = {}
    for q, docs in (("raw", raw), ("symptom", symptom)):
        for d in docs:
            cur = by.setdefault(d.doc, {"doc": d.doc, "score": d.score, "found_by": [],
                                        "top_chunk_breadcrumbs": d.top_chunk_breadcrumbs})
            cur["score"] = max(cur["score"], d.score)
            cur["found_by"].append(q)
    return sorted(by.values(), key=lambda x: (-x["score"], x["doc"]))[:n]


def routing_query_of(found_by: list[str]) -> str:
    s = set(found_by)
    return "both" if s == {"raw", "symptom"} else ("raw" if "raw" in s else "symptom")


@node("route")
def n_route(state: State, ctx: RunCtx) -> dict:
    tid, t = state["trace_id"], state["ticket"]
    raw = "\n".join(x for x in (t.get("subject"), t.get("description")) if x)
    symptom = ((state.get("classify") or {}).get("clean_symptom") or "").strip()
    with tracing.span(tid, "route_doc:raw", {"query": raw[:300]}) as sp:
        r1 = _db.run(lambda c: route_doc(c, raw, {"audience": "store"}), ctx.conn)
        sp.output = [d.doc for d in r1.docs]
    r2_docs = []
    dual = bool((models().get("routing") or {}).get("dual_query", False))
    if dual and symptom and symptom != raw:
        with tracing.span(tid, "route_doc:symptom", {"query": symptom}) as sp:
            r2 = _db.run(lambda c: route_doc(c, symptom, {"audience": "store"}), ctx.conn)
            sp.output = [d.doc for d in r2.docs]
            r2_docs = r2.docs
    ctx.tools.append("route_doc")
    cands = merge_candidates(r1.docs, r2_docs, limits()["route_max_candidates"])
    flags: list[str] = []
    out: dict = {"candidates": cands, "raw_docs": [d.doc for d in r1.docs], "symptom_docs": [d.doc for d in r2_docs],
                 "symptom": symptom}
    if not cands:
        return {"route": {**out, "doc": None, "reason": "no runbook matched", "routing_query": None, "flags": flags},
                "tool_calls": 2}
    if ctx.llm_calls >= ctx.max_llm_calls:
        raise LLMCap("LLM call cap reached at route")
    c0 = time.perf_counter()
    ctx.tools.append("pick_doc")
    with tracing.span(tid, "pick_doc", {"candidates": [c["doc"] for c in cands]}) as sp:
        try:
            spend.check()
            pick = picker_mod.pick_doc(raw, [c["doc"] for c in cands], client=llm._anthropic())
            sp.output = pick.as_dict()
        except anthropic.APIError as e:                       # picker down: keep the top candidate, flag it
            pick = picker_mod.Pick(cands[0]["doc"], f"picker unavailable ({type(e).__name__}); top candidate used",
                                   valid=False)
            flags.append("picker_unavailable")
            sp.status, sp.output = "error", str(e)[:200]
    ctx.llm_calls += max(pick.attempts, 1)
    if pick.input_tokens:
        tracing.generation(tid, base_llm()["model"], {"input_tokens": pick.input_tokens,
                                                      "output_tokens": pick.output_tokens},
                           time.perf_counter() - c0, name="picker")
    rq = None
    if pick.doc:
        rq = routing_query_of(next(c["found_by"] for c in cands if c["doc"] == pick.doc))
    return {"route": {**out, "doc": pick.doc, "reason": pick.reason, "routing_query": rq, "picker_valid": pick.valid,
                      "flags": flags}, "models": [f"picker:{base_llm()['model']}"], "tool_calls": 3}


@node("resolve")
def n_resolve(state: State, ctx: RunCtx) -> dict:
    tid, t, c = state["trace_id"], state["ticket"], state.get("classify") or {}
    doc = state["route"]["doc"]
    sec = _tool(tid, ctx, "kb_section", T.kb_section, doc=doc, query=c.get("clean_symptom") or t.get("subject") or "",
                audience="store")
    prefer = None
    if sec.status == "ok" and sec.section:
        prefer = sec.section.h2
    ds = _db.run(lambda cn: doc_steps(cn, doc, "store", prefer_h2=prefer), ctx.conn)
    info = {"symptom": c.get("clean_symptom"), "device": c.get("affected_device_type"),
            "system": t.get("system_version"), "description": t.get("description"),
            "steps_already_tried": c.get("steps_already_tried") or [], "error_codes": c.get("error_codes") or [],
            "history": [h.get("note") for h in t.get("history") or []][-5:]}
    user = ("<ticket>\n" + json.dumps(info, indent=1, ensure_ascii=False, default=str) + "\n</ticket>\n\n"
            + (f"Search matched section: {sec.matched_breadcrumb}\n\n" if sec.status == "ok" else "")
            + render_for_prompt(ds))
    res = _llm(tid, ctx, "resolve", user, ResolveOut)
    base = {"doc": doc, "matched_breadcrumb": sec.matched_breadcrumb, "n_items": len(ds.items),
            "l2_stubs": ds.l2_stubs}
    if res.parsed is None:
        return {"resolve": {**base, "ok": False, "error": res.error}, "llm_failed": ["resolve"],
                "models": [f"resolve:{res.model}"], "tool_calls": 1}
    r = res.parsed
    item_ids = {i.id for i in ds.items}
    crit_ids = {i.id for i in ds.criteria}
    flags = []
    bad = [i for i in r.step_ids + r.skipped_already_tried if i not in item_ids] + \
          [i for i in r.escalation_criteria_met if i not in crit_ids]
    if bad:
        flags.append("resolve_invalid_ids:" + ",".join(bad[:5]))
    step_ids = [i for i in dict.fromkeys(r.step_ids) if i in item_ids]
    if len(step_ids) > MAX_STEPS:
        flags.append(f"steps_truncated:{len(step_ids)}->{MAX_STEPS}")
        step_ids = step_ids[:MAX_STEPS]
    skipped = [i for i in dict.fromkeys(r.skipped_already_tried) if i in item_ids and i not in step_ids]
    crit = [i for i in dict.fromkeys(r.escalation_criteria_met) if i in crit_ids]
    first = ds.item(step_ids[0]) if step_ids else (ds.item(skipped[0]) if skipped else None)
    return {"resolve": {
        **base, "ok": True, "section": r.section, "breadcrumb": first.breadcrumb if first else r.section,
        "step_ids": step_ids, "steps": [ds.item(i).text for i in step_ids],
        "step_titles": [ds.item(i).title for i in step_ids], "skipped_already_tried": skipped,
        "skipped_titles": [ds.item(i).title for i in skipped], "criteria_met": crit,
        "criteria_text": [ds.item(i).title for i in crit], "needs_l2_only": r.needs_l2_only and bool(ds.l2_stubs),
        "all_steps_tried": not step_ids and bool(skipped), "reason": r.reason, "flags": flags,
        "items_text": "\n".join(i.text for i in ds.items),
        "immediate": [i.text for i in ds.items if i.section_type == "immediate" and i.id not in skipped][:MAX_INTERIM],
        "doc_text": "\n".join(i.text for i in ds.items + ds.criteria),
    }, "models": [f"resolve:{res.model}"], "tool_calls": 1}


_ROUTING = {"escalate": "l2_escalation", "clarify": "needs_clarification", "non_it": "non_it", "verify": "l1_guided"}


def _impact_text(c: dict) -> str | None:
    parts = [label for key, label in (("store_wide_outage", "store-wide outage"), ("pos_outage_all", "all POS down"),
                                      ("payments_unavailable", "card payments unavailable"),
                                      ("payment_failure", "payment failures"), ("partial_outage", "partial outage"),
                                      ("multiple_locations", "multiple locations"),
                                      ("device_unusable", "device unusable"), ("customer_facing", "customer-facing"))
             if c.get(key)]
    if c.get("devices_affected"):
        parts.append(f"{c['devices_affected']} device(s) affected")
    return "; ".join(parts) or None


@node("decide")
def n_decide(state: State, ctx: RunCtx) -> dict:
    tid, t, c = state["trace_id"], state["ticket"], state.get("classify") or {}
    route, res = state.get("route") or {}, state.get("resolve") or {}
    facts = dict(state["facts"])
    if route:
        facts["kb_no_doc"] = route.get("doc") is None
    if res.get("ok"):
        facts.update(all_steps_tried=res["all_steps_tried"], l2_only_needed=res["needs_l2_only"],
                     l2_only="; ".join(res.get("l2_stubs") or [])[:200],
                     runbook_escalation_met=bool(res["criteria_met"]),
                     runbook_escalation_text="; ".join(res["criteria_text"])[:300])
    if state.get("llm_failed"):
        facts["llm_failed"] = ", ".join(state["llm_failed"])
    extra = state.get("extra") or {}
    xflags: list[str] = []
    if extra.get("needs_model_check"):                          # candidates of different models
        doc_text = _norm(res.get("doc_text") or "")
        models_ = {x.get("model") or x.get("system") for x in extra.get("candidates", [])}
        if route.get("doc") and doc_text and all(_norm(m) in doc_text for m in models_ if m):
            xflags.append("device_ambiguous:runbook covers all candidate models")
            extra = {**extra, "ask_which_device": True}
        else:
            facts["asset_unverified"] = True
    pol = sop_rules.evaluate(facts)
    d = pol["decision"]
    rules_routing = None
    if d == "verify":                    # information only: never escalate / hand off / propose on a closed ticket
        eff = pol["verify_decision"]
        rules_routing = pol["routing_hint"] or _ROUTING.get(eff, "l1_guided")
        routing, escalate = "l1_guided", False
    else:
        routing = pol["routing_hint"] or _ROUTING.get(d, "l1_guided")
        escalate = d == "escalate"
    steps = res.get("steps", []) if (d == "l1" and res.get("ok") and not pol["no_steps"]) else []
    step_ids = res.get("step_ids", []) if steps else []
    interim = (res.get("immediate", [])[:MAX_INTERIM]
               if escalate and res.get("ok") and not pol["no_steps"] and not facts.get("l2_only_needed")
               and not facts.get("asset_unverified") and not facts.get("system_mismatch") else [])
    # SLA at the final priority (for the handoff and the reply)
    opened = _dt(t.get("date_opened"))
    sla = state["enrich"].get("sla") or {}
    if pol["priority"] != t.get("priority") and opened:
        sla = _tool(tid, ctx, "get_sla", T.get_sla, priority=pol["priority"], category=t.get("category") or "General",
                    created_at=opened, now=_sla_now(opened)).model_dump(mode="json")
    spec = sop_rules.handoff_spec()
    store = state["enrich"].get("store") or {}
    handoff = None
    if escalate:
        asset_id = extra.get("asset_id") or t.get("asset_id")
        hf = {"asset_id": asset_id if not facts.get("asset_unverified") else None,
              "error_codes": c.get("error_codes") or [], "steps_tried": c.get("steps_already_tried") or [],
              "store_impact": _impact_text(c)}
        missing = [k for k in spec["mandatory"] if not hf.get(k)]
        notify = [spec["notify"]["l2"]] + ([spec["notify"]["p1_also"]] if pol["priority"] == "P1" else []) + [
            spec["notify"][k] for k in pol.get("notify", []) if k in spec["notify"]]        # e.g. facilities
        handoff = {"asset_id": hf["asset_id"], "error_codes": hf["error_codes"], "attempted_steps": hf["steps_tried"],
                   "store_impact": hf["store_impact"], "priority": pol["priority"],
                   "store": ", ".join(x for x in (store.get("store_name"), store.get("city")) if x) or t.get("store_id"),
                   "sla_first_response_due": str(sla.get("response_due")) if sla.get("response_due") else None,
                   "missing_fields": missing, "notify": notify}
    flags = list(dict.fromkeys(pol["flags"] + extra.get("flags", []) + xflags + route.get("flags", [])
                               + res.get("flags", [])
                               + ([f"handoff_missing:{m}" for m in handoff["missing_fields"]] if handoff else [])))
    return {"policy": pol, "decision": {
        "decision": d, "verify_decision": pol.get("verify_decision"), "routing": routing, "mode": pol["mode"],
        "should_escalate": escalate,
        "priority": pol["priority"], "steps": steps, "step_ids": step_ids, "kb_doc": route.get("doc"),
        "kb_section_breadcrumb": res.get("breadcrumb") if res.get("ok") else None, "handoff": handoff,
        "flags": flags, "sla": {k: sla.get(k) for k in ("priority", "first_response_minutes", "response_due",
                                                        "resolution_due", "breached")},
        "redirect": pol.get("redirect"), "routing_query": route.get("routing_query"),
        "interim_steps": interim, "verify_rules_routing": rules_routing,
        "ask_which_device": bool(extra.get("ask_which_device")), "candidates": extra.get("candidates", []),
        "non_it_redirects": extra.get("non_it_redirects", []),
    }, "final_facts": facts, "extra": extra, "tool_calls": 1 if sla is not state["enrich"].get("sla") else 0}


def _sla_ist(sla: dict) -> dict:
    """SLA for the reply: minutes and IST clock times (the store works in IST)."""
    out = {k: sla.get(k) for k in ("priority", "first_response_minutes", "breached")}
    for k in ("response_due", "resolution_due"):
        dt = _dt(sla.get(k))
        out[f"{k}_ist"] = dt.astimezone(IST).strftime("%d %b %Y %H:%M IST") if dt else None
    return out


def _compose_facts(state: State) -> dict:
    t, c, d = state["ticket"], state.get("classify") or {}, state["decision"]
    res, extra = state.get("resolve") or {}, state.get("extra") or {}
    pol = state["policy"]
    return {
        "ticket": {k: t.get(k) for k in ("ticket_id", "store_name", "submitted_by", "subject", "description",
                                          "ticket_status", "resolution_notes", "category")},
        "decision": d["decision"], "mode": d["mode"], "routing": d["routing"], "priority": d["priority"],
        "sop_reasons": [f["reason"] for f in pol["fired_rules"] if f["applied"]],
        "priority_reasons": [p["reason"] for p in pol["priority_rules"]],
        "sla": _sla_ist(d["sla"]), "step_titles_shown_below": res.get("step_titles", []) if d["steps"] else [],
        "steps_skipped_already_tried": res.get("skipped_titles", []),
        "clarifying_questions": c.get("clarifying_questions") or [],
        "non_it_redirects": d.get("non_it_redirects", []), "redirect_team": d.get("redirect"),
        "ask_which_device": d.get("ask_which_device"),
        "device_candidates": [f"{x.get('asset_id')} ({x.get('model') or x.get('system')})"
                              for x in d.get("candidates", [])],
        "first_response_titles_shown_below": [s.splitlines()[0] for s in d.get("interim_steps", [])],
        "verify_rules_routing": d.get("verify_rules_routing"),
        "asset": {k: (extra.get("asset") or {}).get(k) for k in ("asset_id", "asset_type", "system", "version")},
        "asset_retargeted_from": extra.get("retargeted_from"),
        "claims": extra.get("claims", []), "flags": d["flags"],
        "past_verified_actions": (state["enrich"].get("past_actions") or {}).get("actions", [])[:3],
        "requester_tone": c.get("requester_tone"), "handoff": d.get("handoff"),
        "safety_notice": pol.get("reply_prefix"),
    }


@node("compose")
def n_compose(state: State, ctx: RunCtx) -> dict:
    facts = _compose_facts(state)
    user = "<facts>\n" + json.dumps(facts, indent=1, ensure_ascii=False, default=str) + "\n</facts>"
    res = _llm(state["trace_id"], ctx, "compose", user, ComposeOut)
    out = {"models": [f"compose:{res.model}"]}
    if res.parsed is None:
        return {**out, "compose": None}
    return {**out, "compose": res.parsed.model_dump(mode="json")}


def _confidence(state: State, flags: list[str], d: dict) -> str:
    if d["decision"] == "clarify" or state.get("llm_failed") or any(f.startswith("guard:") for f in flags):
        return "low"
    if any(":" in m and "luna" in m for m in state.get("models", [])) or not (state.get("route") or {}).get(
            "picker_valid", True):
        return "low"
    soft = ("unverified_claim", "device_ambiguous", "cmdb_gap", "version_mismatch", "resolve_invalid_ids")
    return "medium" if any(f.startswith(soft) for f in flags) else "high"


@node("guard")
def n_guard(state: State, ctx: RunCtx) -> dict:
    d, comp = state["decision"], state.get("compose") or {}
    ref = _ref(ctx.conn)
    pol = state["policy"]
    handoff = None
    if d["should_escalate"]:
        ch = comp.get("l2_handoff") or {}
        h = d.get("handoff") or {}
        handoff = {"summary": ch.get("summary") or (state.get("classify") or {}).get("clean_symptom")
                   or state["ticket"].get("subject") or "Escalated by SOP rule",
                   "evidence": ch.get("evidence") or [f["reason"] for f in pol["fired_rules"] if f["applied"]],
                   "requested_action": ch.get("requested_action"), **h}
    fired = [{"id": f["id"], "reason": f["reason"], "source": f["source"], "applied": f["applied"]}
             for f in pol["fired_rules"]]
    ans = {"routing": d["routing"], "mode": d["mode"], "should_escalate": d["should_escalate"],
           "escalation_reason": (comp.get("escalation_reason") or "; ".join(
               f["reason"] for f in pol["fired_rules"] if f["applied"])[:400]) if d["should_escalate"] else None,
           "priority": d["priority"], "kb_doc": d["kb_doc"], "kb_section_breadcrumb": d["kb_section_breadcrumb"],
           "steps": d["steps"], "step_ids": d["step_ids"], "reply_to_store": comp.get("reply_to_store") or "",
           "l2_handoff": handoff, "flags": d["flags"], "fired_rules": fired,
           "priority_rules": [{"id": p["id"], "reason": f"{p['change']}: {p['reason']}", "source": p["source"]}
                              for p in pol["priority_rules"]],
           "routing_query": d.get("routing_query"),
           "interim_steps": d.get("interim_steps", []), "verify_rules_routing": d.get("verify_rules_routing"),
           "device_candidates": d.get("candidates", []) if (d.get("ask_which_device") or d["decision"] == "clarify")
           else [], "non_it_redirects": d.get("non_it_redirects", []),
           "model_used": list(dict.fromkeys(state.get("models", [])))}
    sources = "\n".join([
        json.dumps(state["ticket"], default=str), json.dumps(state["enrich"], default=str),
        json.dumps(state.get("classify") or {}, default=str), json.dumps(state.get("extra") or {}, default=str),
        json.dumps(d, default=str), json.dumps(_compose_facts(state), default=str),
        (state.get("resolve") or {}).get("items_text", ""), ref["sop"],
    ])
    if comp == {}:
        ans["flags"] = ans["flags"] + ["compose_failed"]
    guarded, rep = guard.check(ans, decision=d["decision"], sources=sources, corpus=ref["corpus"],
                               l2_texts=ref["l2"], vocab=ref["vocab"], no_steps=bool(state["facts"].get("unknown_system"))
                               or pol["no_steps"])
    if not guarded["steps"]:
        guarded["step_ids"] = []
    prefix = pol.get("reply_prefix")              # fixed rule text (e.g. AX-SAFETY), added after the model and guard
    if prefix and not (guarded.get("reply_to_store") or "").startswith(prefix):
        guarded["reply_to_store"] = (prefix + "\n\n" + (guarded.get("reply_to_store") or "")).strip()
    guarded["flags"] = list(dict.fromkeys(guarded["flags"] + rep.flags))
    guarded["confidence"] = _confidence(state, guarded["flags"], d)
    guarded["tools_used"] = list(dict.fromkeys(ctx.tools))
    guarded["node_path"] = list(state.get("node_path", [])) + ["guard"]
    try:
        final = V2Answer.model_validate(guarded).model_dump(mode="json")
    except ValidationError as e:
        rep.flags.append("guard:schema_invalid")
        final = V2Answer(routing="l2_escalation", should_escalate=True, priority=d["priority"],
                         escalation_reason="Answer failed schema validation; L2 to review.",
                         reply_to_store=(prefix + "\n\n" if prefix else "") + guard.TEMPLATES["escalate"],
                         confidence="low",
                         l2_handoff={"summary": str(e)[:300], "evidence": [], "attempted_steps": []},
                         flags=guarded["flags"] + ["guard:schema_invalid"]).model_dump(mode="json")
    return {"answer": final, "guard": rep.as_dict()}


@node("hitl_propose")
def n_propose(state: State, ctx: RunCtx) -> dict:
    a = state["answer"]
    payload = {k: a.get(k) for k in ("routing", "mode", "priority", "kb_doc", "kb_section_breadcrumb", "steps",
                                     "reply_to_store", "l2_handoff", "flags", "escalation_reason")}
    payload.update(fired_rules=[r["id"] for r in a.get("fired_rules", []) if r.get("applied")],
                   agent_version=AGENT_VERSION, node_path=state.get("node_path", []) + ["hitl_propose"])
    out = _tool(state["trace_id"], ctx, "log_action", T.log_action, ticket_id=state["ticket"]["ticket_id"],
                action="escalate" if a["should_escalate"] else "update", payload=payload, actor="v2_agent",
                verified_by_human=False, trace_id=state["trace_id"], batch_id=state.get("batch_id"))
    n = 1
    corr = (state.get("extra") or {}).get("cmdb_correction")
    if corr:                                          # proposed only; approving it never changes the CMDB
        _tool(state["trace_id"], ctx, "log_action", T.log_action, ticket_id=state["ticket"]["ticket_id"],
              action="cmdb_correction", payload={**corr, "store_id": state["ticket"].get("store_id"),
                                                 "agent_version": AGENT_VERSION, "applied": False},
              actor="v2_agent", verified_by_human=False, trace_id=state["trace_id"], batch_id=state.get("batch_id"))
        n += 1
    return {"action_id": out.action_id if out.status == "ok" else None, "tool_calls": n}


def n_review(state: State) -> dict:
    """HITL: pause until a reviewer approves or rejects the proposed action (LangGraph interrupt)."""
    from langgraph.types import interrupt
    a = state["answer"]
    decision = interrupt({"action_id": state.get("action_id"), "routing": a["routing"], "priority": a["priority"],
                          "should_escalate": a["should_escalate"]})
    verdict = (decision or {}).get("decision")
    reviewer = (decision or {}).get("reviewer") or "console-user"
    applied = False
    if verdict in ("approve", "reject") and state.get("action_id"):
        applied = _db.run(lambda c: hitl.review_action(c, state["action_id"], verdict, reviewer), None)
    return {"review": {"decision": verdict, "reviewer": reviewer, "applied": applied,
                       "at": datetime.now(timezone.utc).isoformat()}, "node_path": ["hitl_review"]}


# --- graph ---------------------------------------------------------------------------------------

def _after_policy(state: State) -> str:
    return "decide" if state["policy"]["decision"] in ("non_it", "clarify") else "route"


def _after_route(state: State) -> str:
    if state["route"].get("doc") is None or state["policy"]["decision"] == "verify" or state["policy"]["no_steps"]:
        return "decide"
    return "resolve"


def _after_guard(state: State) -> str:
    if state["answer"].get("mode") == "verify":       # Resolved/Closed: information only, no action proposed
        return "__end__"
    return "hitl_propose" if state.get("write_action") and state.get("batch_id") else "__end__"


def build_graph():
    from langgraph.graph import END, START, StateGraph
    g = StateGraph(State)
    for name, fn in (("load", n_load), ("enrich", n_enrich), ("classify", n_classify), ("policy", n_policy),
                     ("route", n_route), ("resolve", n_resolve), ("decide", n_decide), ("compose", n_compose),
                     ("guard", n_guard), ("hitl_propose", n_propose), ("hitl_review", n_review)):
        g.add_node(name, fn)
    g.add_edge(START, "load")
    g.add_edge("load", "enrich")
    g.add_edge("enrich", "classify")
    g.add_edge("classify", "policy")
    g.add_conditional_edges("policy", _after_policy, ["route", "decide"])
    g.add_conditional_edges("route", _after_route, ["resolve", "decide"])
    g.add_edge("resolve", "decide")
    g.add_edge("decide", "compose")
    g.add_edge("compose", "guard")
    g.add_conditional_edges("guard", _after_guard, ["hitl_propose", END])
    g.add_edge("hitl_propose", "hitl_review")
    g.add_edge("hitl_review", END)
    return g


_SAVER: dict[str, Any] = {}
_SAVER_LOCK = threading.Lock()


def checkpointer():
    """(kind, saver): Postgres on the pooled Neon connection, else SQLite in outputs/ (reason logged)."""
    with _SAVER_LOCK:
        s = _SAVER.get("saver")
        conn = _SAVER.get("conn")
        if s is not None and (conn is None or not (conn.closed or conn.broken)):
            return _SAVER["kind"], s
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg.rows import dict_row
            from ax15 import db
            conn, _ = db.connect(autocommit=True, prepare_threshold=0, row_factory=dict_row)
            _SAVER.update(kind="postgres", saver=PostgresSaver(conn), conn=conn, reason="ok")
        except Exception as e:  # noqa: BLE001 - fall back to a local checkpointer
            import sqlite3
            from langgraph.checkpoint.sqlite import SqliteSaver
            path = OUTPUTS_DIR / "checkpoints_v2.sqlite"
            path.parent.mkdir(parents=True, exist_ok=True)
            sconn = sqlite3.connect(str(path), check_same_thread=False)
            _SAVER.update(kind="sqlite", saver=SqliteSaver(sconn), conn=None, reason=f"{type(e).__name__}")
            log.warning("Postgres checkpointer unavailable (%s); using SQLite %s", type(e).__name__, path.name)
        return _SAVER["kind"], _SAVER["saver"]


_APPS: dict[Any, Any] = {}


def app(with_checkpointer: bool):
    if not with_checkpointer:
        if None not in _APPS:
            _APPS[None] = build_graph().compile()
        return _APPS[None], None
    kind, saver = checkpointer()
    key = id(saver)
    if key not in _APPS:
        _APPS[key] = build_graph().compile(checkpointer=saver)
    return _APPS[key], kind


# --- entry points ----------------------------------------------------------------------------------

@dataclass
class RunResult:
    status: Literal["ok", "incomplete", "error"]
    ticket_id: str
    trace_id: str
    answer: dict | None = None
    error: str | None = None
    tool_calls: int = 0
    tools_used: list[str] = field(default_factory=list)
    node_path: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    latency_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    action_id: int | None = None
    awaiting_review: bool = False
    checkpointer: str | None = None
    guard: dict | None = None
    route: dict | None = None
    classify: dict | None = None
    facts: dict | None = None
    trace_path: str | None = None
    langfuse_url: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def run_agent(ticket: str | dict, *, batch_id: str | None = None, split: str | None = None, conn=None,
              client=None, write_action: bool = True, memory: bool = True) -> RunResult:
    """Run AX-15 2.0 on one ticket (id or UI ticket dict). With write_action the run pauses at hitl_review
    (awaiting_review=True); resume() approves or rejects. memory=False skips get_past_actions (eval)."""
    tid_name = ticket if isinstance(ticket, str) else str(ticket.get("ticket_id") or "ui-ticket")
    trace_id = tracing.start_trace(tid_name, AGENT_VERSION, split, input={"ticket": tid_name})
    lim = limits()
    with _CTX_LOCK:
        _CTX[trace_id] = RunCtx(conn=conn, deadline=time.perf_counter() + lim["timeout_s"],
                                max_llm_calls=lim["max_llm_calls"])
    res = RunResult(status="error", ticket_id=tid_name, trace_id=trace_id)
    t0 = time.perf_counter()
    final: dict = {}
    try:
        spend.check()
        graph, kind = app(write_action)
        res.checkpointer = kind
        cfg = {"configurable": {"thread_id": trace_id}, "recursion_limit": 40}
        init: State = {"trace_id": trace_id, "ticket_ref": ticket, "batch_id": batch_id, "split": split,
                       "write_action": write_action, "memory": memory, "node_path": [], "models": [],
                       "tool_calls": 0, "llm_failed": []}
        final = graph.invoke(init, cfg)
        res.answer = final.get("answer")
        res.status = "ok" if res.answer else "error"
        res.awaiting_review = bool(final.get("__interrupt__"))
        res.action_id = final.get("action_id")
    except (RunTimeout, LLMCap) as e:
        res.status, res.error = "incomplete", str(e)
    except spend.SpendCapExceeded as e:
        res.status, res.error = "error", str(e)
    except LookupError as e:
        res.status, res.error = "error", str(e)
    except Exception as e:  # noqa: BLE001 - one broken run must not crash the caller
        res.status, res.error = "error", f"{type(e).__name__}: {str(e)[:300]}"
        log.exception("v2 run failed")
    finally:
        snap = final or {}
        res.node_path = list(snap.get("node_path") or [])
        res.tool_calls = int(snap.get("tool_calls") or 0)
        res.guard = snap.get("guard")
        res.classify = {k: (snap.get("classify") or {}).get(k) for k in
                        ("clean_symptom", "affected_device_type", "clarity", "intents")} if snap.get("classify") else None
        res.facts = snap.get("final_facts") or snap.get("facts")
        res.route = {k: v for k, v in (snap.get("route") or {}).items() if k in
                     ("candidates", "raw_docs", "symptom_docs", "doc", "reason", "routing_query", "symptom")} or None
        res.latency_s = round(time.perf_counter() - t0, 2)
        res.tools_used = list(_CTX.get(trace_id, RunCtx()).tools)
        summary = tracing.end_trace(trace_id, (res.answer or {}).get("routing"), res.status, output=res.answer,
                                    extra={"node_path": res.node_path, "error": res.error,
                                           "action_id": res.action_id, "awaiting_review": res.awaiting_review})
        res.cost_usd = summary.get("total_cost_usd", 0.0)
        res.input_tokens, res.output_tokens = summary.get("input_tokens", 0), summary.get("output_tokens", 0)
        res.trace_path, res.langfuse_url = summary.get("trace_path"), summary.get("langfuse_url")
        with _CTX_LOCK:
            _CTX.pop(trace_id, None)
    return res


def pending_review(thread_id: str) -> dict | None:
    """The interrupt payload if this thread is paused at hitl_review, else None."""
    graph, _ = app(True)
    st = graph.get_state({"configurable": {"thread_id": thread_id}})
    if "hitl_review" in (st.next or ()):
        intr = [i.value for t in st.tasks for i in (t.interrupts or ())]
        return intr[0] if intr else {}
    return None


def resume(thread_id: str, decision: Literal["approve", "reject"], reviewer: str = "console-user") -> dict:
    """Approve / reject the proposed action of a paused run: resumes the graph from its checkpoint."""
    from langgraph.types import Command
    graph, _ = app(True)
    cfg = {"configurable": {"thread_id": thread_id}}
    st = graph.get_state(cfg)
    if "hitl_review" not in (st.next or ()):
        return {"resumed": False, "message": "run is not waiting for review"}
    ticket_id = (st.values.get("ticket") or {}).get("ticket_id", "ticket")
    rid = tracing.start_trace(f"{ticket_id}-review", AGENT_VERSION, st.values.get("split"),
                              input={"thread_id": thread_id, "decision": decision})
    with tracing.span(rid, "node:hitl_review", {"thread_id": thread_id, "decision": decision}, kind="span") as sp:
        out = graph.invoke(Command(resume={"decision": decision, "reviewer": reviewer}), cfg)
        sp.output = out.get("review")
    tracing.end_trace(rid, None, "ok", output=out.get("review"), extra={"thread_id": thread_id})
    return {"resumed": True, **(out.get("review") or {}), "node_path": out.get("node_path", [])}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticket", required=True)
    ap.add_argument("--batch", default=None)
    ap.add_argument("--no-write", action="store_true", help="stop after guard (no action_log row, no HITL)")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--approve", action="store_true", help="approve the proposed action (resumes the graph)")
    g.add_argument("--reject", action="store_true")
    a = ap.parse_args()
    r = run_agent(a.ticket, batch_id=a.batch, write_action=not a.no_write)
    print(json.dumps(r.to_dict(), indent=1, ensure_ascii=False, default=str))
    if r.awaiting_review and (a.approve or a.reject):
        print(json.dumps(resume(r.trace_id, "approve" if a.approve else "reject", "human:cli"), indent=1,
                         default=str))
    return 0 if r.status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
