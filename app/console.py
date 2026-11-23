"""AX-15 console — run 1.0 or 2.0 (or both side by side) on a new or existing ticket, inspect the trace,
review proposed actions.

    streamlit run app/console.py

Sidebar: agent selector (2.0 LangGraph workflow · 1.0 single agent · Compare) and service status.
Tabs: New ticket (creates a source='ui' ticket) · Existing ticket (dev or chaos tickets, id + subject only) ·
Batch · Ask the KB (plain RAG: search_with_stubs + answer, no evaluator view) · System (KB, tickets, models,
last eval aggregates, clear console tickets). No label fields are read or shown here: the dev and
chaos pick lists come from ticket ids in eval/splits.json and eval/chaos_labels.json, nothing else.
2.0 approve / reject resumes the paused LangGraph run (HITL interrupt) from its checkpoint.
"""
from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psycopg
import streamlit as st

from ax15 import db, status
from ax15.agents import v1_single, v2_graph
from ax15.config import base_llm, embedding_cfg, fallback_llm
from ax15.l0_data.normalise import CATEGORIES, TicketValidationError
from ax15.l0_data.ui_tickets import clear_ui_tickets, create_ui_ticket, ui_ticket_counts
from ax15.settings import OUTPUTS_DIR, ROOT
from ax15.tools import functions as tools_fn
from ax15.l0_data import batch as batch_mod
from ax15.l1_knowledge.answer import answer as kb_answer
from ax15.l1_knowledge.search import search_with_stubs
from ax15.trust import grounding, hitl

st.set_page_config(page_title="AX-15 Console", layout="wide")
REVIEWER = "console-user"
AGENTS = {"2.0 LangGraph workflow": "v2", "1.0 single agent": "v1", "Compare 1.0 vs 2.0": "compare"}


# --- connection -------------------------------------------------------------------------------

@st.cache_resource
def _holder() -> dict:
    return {"conn": None}


def get_conn() -> psycopg.Connection:
    """Live connection; reconnects if Neon closed the idle one."""
    h = _holder()
    c = h["conn"]
    if c is not None and not c.closed and not c.broken:
        try:
            c.execute("SELECT 1")
            return c
        except psycopg.OperationalError:
            pass
    if c is not None:
        c.close()
    h["conn"], _ = db.connect(autocommit=True)
    return h["conn"]


@st.cache_data(ttl=600)
def stores() -> list[tuple[str, str]]:
    return get_conn().execute("SELECT store_id, store_name FROM stores ORDER BY store_id").fetchall()


@st.cache_data(ttl=600)
def ticket_choices(kind: str) -> list[tuple[str, str]]:
    """(ticket_id, subject) for the dev split or the chaos set: ids from the eval asset files only."""
    if kind == "dev":
        ids = json.loads((ROOT / "eval" / "splits.json").read_text(encoding="utf-8"))["dev"]
    else:
        ids = [c["ticket_id"] for c in json.loads((ROOT / "eval" / "chaos_labels.json").read_text(encoding="utf-8"))]
    rows = get_conn().execute("SELECT ticket_id, subject FROM tickets WHERE batch_id = 'train' AND ticket_id = ANY(%s)"
                              " ORDER BY ticket_id", (ids,)).fetchall()
    return rows


@st.cache_resource
def corpus() -> str:
    return grounding.load_corpus(get_conn())


# --- formatting -------------------------------------------------------------------------------

def k(n: int | float | None) -> str:
    """46213 -> 46.2K (short enough for st.metric)."""
    n = n or 0
    return f"{n / 1e6:.1f}M" if n >= 1e6 else (f"{n / 1e3:.1f}K" if n >= 1e3 else str(int(n)))


def usd(x: float | None) -> str:
    return f"${(x or 0):.4f}"


ROUTING_SHORT = {"l1_guided": "L1 guided", "l1_self_service": "L1 self-serve", "l2_escalation": "Escalate L2",
                 "needs_clarification": "Clarify", "non_it": "Not IT"}


# --- sidebar ----------------------------------------------------------------------------------

with st.sidebar:
    st.header("AX-15 console")
    agent_label = st.radio("Agent", list(AGENTS), key="agent")
    AGENT = AGENTS[agent_label]
    st.markdown(f"**Model:** `{base_llm()['model']}` (effort {base_llm().get('effort', 'default')})  \n"
                f"**Fallback (2.0):** `{fallback_llm()['model']}`")
    st.markdown("#### Status")
    if st.button("Refresh", key="status_refresh") or "status" not in st.session_state:
        with st.spinner("Checking services (no paid calls)…"):
            st.session_state["status"] = status.console_checks()     # Neon ping also wakes a scaled-to-zero DB
            st.session_state["status_at"] = time.strftime("%H:%M:%S")
    CHECKS = st.session_state["status"]
    for c in CHECKS.values():
        st.markdown(f"{c.icon} **{c.name}**  \n<small>{c.detail}</small>", unsafe_allow_html=True)
    st.caption(f"Checked {st.session_state.get('status_at', '')}")
    RUN_OK = status.can_run(CHECKS)
    if not RUN_OK:
        st.error("Run is disabled: Neon and Anthropic must both be green.")

st.title("AX-15 IT Support Console")
try:
    get_conn()
    db_ok = True
except BaseException:  # noqa: BLE001 - SystemExit from db.connect when Neon is unreachable
    db_ok = False
if not db_ok:
    st.error("Database unavailable: see Status in the sidebar, then Refresh.")
    st.stop()


# --- result rendering -------------------------------------------------------------------------

def render_steps(ans: dict) -> None:
    st.markdown("#### Steps")
    if not ans["steps"]:
        st.caption("No KB steps for this answer.")
        return
    crumb = ans.get("kb_section_breadcrumb") or ans.get("kb_doc") or "—"
    st.caption(f"Source: `{ans.get('kb_doc') or '—'}` · {crumb}")
    cp = corpus()
    for i, s in enumerate(ans["steps"], 1):
        badge = "🟢 verbatim from KB" if grounding.is_grounded(s, cp) else "🔴 not found in KB"
        sid = (ans.get("step_ids") or [None] * len(ans["steps"]))[i - 1]
        st.markdown(f"**{i}.** {grounding.step_text(s)}  \n<small>{badge} · {sid + ' · ' if sid else ''}"
                    f"cites: {crumb}</small>", unsafe_allow_html=True)


def render_answer(res: dict, compact: bool = False) -> None:
    ans = res.get("answer")
    if res["status"] != "ok" or not ans:
        st.error(f"Run ended with status **{res['status']}**: {res.get('error') or 'no valid answer'}")
        return
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Routing", ROUTING_SHORT.get(ans["routing"], ans["routing"]))
    c2.metric("Priority", ans["priority"])
    c3.metric("Mode", ans["mode"])
    c4.metric("Confidence", ans["confidence"])
    if ans["should_escalate"]:
        st.warning(f"**Escalate:** {ans.get('escalation_reason') or '—'}")
    if ans.get("flags"):
        st.caption("Flags: " + " · ".join(f"`{f}`" for f in ans["flags"]))
    st.markdown("#### Reply to store")
    st.info(ans["reply_to_store"])
    render_steps(ans)
    if ans.get("interim_steps"):
        st.markdown("#### While L2 picks this up")
        cp = corpus()
        for i, s in enumerate(ans["interim_steps"], 1):
            badge = "🟢 verbatim from KB" if grounding.is_grounded(s, cp) else "🔴 not found in KB"
            st.markdown(f"**{i}.** {grounding.step_text(s)}  \n<small>{badge} · first-response step from "
                        f"`{ans.get('kb_doc')}`</small>", unsafe_allow_html=True)
    if ans.get("device_candidates"):
        st.caption("Devices of this type at the store: " + " · ".join(
            f"`{d.get('asset_id')}` {d.get('model') or d.get('system') or ''}" for d in ans["device_candidates"]))
    if ans.get("non_it_redirects"):
        st.caption("Redirected (not IT): " + " · ".join(ans["non_it_redirects"]))
    if ans.get("verify_rules_routing"):
        st.caption(f"Verify mode: the SOP rules would class this ticket as "
                   f"`{ans['verify_rules_routing']}` (information only, no action is proposed).")
    if ans.get("l2_handoff"):
        h = ans["l2_handoff"]
        with st.expander("L2 handoff", expanded=not compact):
            st.markdown(f"**Summary:** {h['summary']}")
            if h.get("evidence"):
                st.markdown("**Evidence:**\n" + "\n".join(f"- {e}" for e in h["evidence"]))
            if h.get("attempted_steps"):
                st.markdown("**Already attempted:**\n" + "\n".join(f"- {e}" for e in h["attempted_steps"]))
            if h.get("requested_action"):
                st.markdown(f"**Requested action:** {h['requested_action']}")
            extra = {x: h.get(x) for x in ("asset_id", "store", "priority", "sla_first_response_due", "notify",
                                            "missing_fields") if h.get(x)}
            if extra:
                st.caption(" · ".join(f"{a}: {', '.join(v) if isinstance(v, list) else v}" for a, v in extra.items()))


def render_v2_details(res: dict) -> None:
    """2.0 only: node path, fired SOP rules with citations, routing query, models, guard."""
    ans = res.get("answer") or {}
    path = res.get("node_path") or ans.get("node_path") or []
    if res.get("review", {}).get("node_path"):
        path = res["review"]["node_path"]
    st.markdown("#### 2.0 workflow")
    st.markdown(" → ".join(f"`{n}`" for n in path) or "—")
    route = res.get("route") or {}
    if route:
        rq = route.get("routing_query")
        found = {"raw": "the raw ticket text", "symptom": "the cleaned symptom", "both": "both queries"}.get(rq, "—")
        st.markdown(f"**Runbook:** `{route.get('doc') or 'none'}` · found by **{found}**")
        if route.get("symptom"):
            st.caption(f"Clean symptom: {route['symptom']}")
        with st.expander("Routing candidates"):
            for c in route.get("candidates", []):
                st.markdown(f"- `{c['doc']}` · {c['score']:.4f} · found by {', '.join(c['found_by'])}")
            st.caption(f"Picker: {route.get('reason') or '—'}")
    fired = ans.get("fired_rules") or []
    st.markdown("**SOP rules**" + ("" if fired else ": none fired (default L1 troubleshooting)"))
    for f in fired:
        tag = "✅ applied" if f.get("applied", True) else "⏸ superseded"
        st.markdown(f"- `{f['id']}` · {tag} · {f['reason']}  \n  <small>source: {f['source']}</small>",
                    unsafe_allow_html=True)
    if ans.get("priority_rules"):
        st.markdown("**Priority:** " + " · ".join(f"`{p['id']}` {p['reason']}" for p in ans["priority_rules"]))
    if ans.get("model_used"):
        st.caption("Models: " + " · ".join(ans["model_used"]))
    g = res.get("guard") or {}
    if g.get("stripped_steps") or g.get("stripped_sentences"):
        with st.expander(f"Guard removed {len(g.get('stripped_steps', []))} step(s), "
                         f"{len(g.get('stripped_sentences', []))} sentence(s)"):
            for s in g.get("stripped_steps", []) + g.get("stripped_sentences", []):
                st.markdown(f"- ~~{s[:300]}~~")


STATUS_ICON = {"ok": "🟢", "not_found": "🟡", "mismatch": "🟠", "error": "🔴"}


def render_trace(res: dict) -> None:
    st.markdown("#### Trace")
    path = res.get("trace_path")
    trace = json.loads(Path(path).read_text(encoding="utf-8")) if path and Path(path).exists() else None
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tool calls", res["tool_calls"])
    c2.metric("Tokens in/out", f"{k(res['input_tokens'])}/{k(res['output_tokens'])}")
    c3.metric("Cost", usd(res["cost_usd"]))
    c4.metric("Time", f"{res['latency_s']:.1f}s")
    if res.get("langfuse_url"):
        st.markdown(f"[Open trace in Langfuse]({res['langfuse_url']}) · trace id `{res['trace_id']}`")
    elif path:
        st.caption(f"Local trace: `{Path(path).relative_to(ROOT) if Path(path).is_relative_to(ROOT) else path}`")
    if not trace:
        return
    lines = []
    for e in trace["events"]:
        if e["type"] == "span":
            args = ", ".join(f"{a}={str(v)[:40]}" for a, v in e["args"].items())
            lines.append(f"{STATUS_ICON.get(e['status'], '⚪')} **{e['name']}**({args}) · `{e['status']}` · "
                         f"{e['latency_ms']} ms")
        elif e["type"] == "node":
            lines.append(f"▶ **{e['name']}** · {e['latency_ms']} ms")
        else:
            lines.append(f"🧠 _{e['name']}_ `{e.get('model', '')}` · "
                         f"{k(e['input_tokens'] + e['cache_read_tokens'] + e['cache_write_tokens'])} in "
                         f"({k(e['cache_read_tokens'])} cached) / {k(e['output_tokens'])} out · {e['latency_ms']} ms")
    with st.expander("Timeline", expanded=True):
        st.markdown("  \n".join(lines))


def render_actions(res: dict, key: str) -> None:
    st.markdown("#### Proposed actions")
    acts = hitl.proposed_actions(get_conn(), res["trace_id"])
    if not acts:
        st.caption("No action was proposed for this run.")
    is_v2 = res.get("agent") == "v2"
    for a in acts:
        p = a["payload"] or {}
        box = st.container(border=True)
        box.markdown(f"**#{a['action_id']} · {a['action_type']}** · status `{a['status']}` · by {a['actor']}"
                     + (f" · reviewed by {a['verified_by']}" if a["verified_by"] else ""))
        if a["action_type"] == "propose_cmdb_correction":
            box.caption(f"CMDB correction (proposed only, never applied): ticket asset "
                        f"`{p.get('ticket_asset_id')}` not in CMDB · suggested `{p.get('suggested') or '—'}` · "
                        f"candidates {', '.join(c.get('asset_id', '') for c in p.get('candidates') or []) or 'none'}")
        box.caption(f"routing {p.get('routing')} · priority {p.get('priority')} · doc {p.get('kb_doc') or '—'}"
                    + (" · paused at hitl_review (approve/reject resumes the graph)" if is_v2 and a["status"] ==
                       "proposed" else ""))
        if a["status"] == "proposed":
            b1, b2, _ = box.columns([2, 2, 3])
            for btn, decision, kw in ((b1, "approve", {"type": "primary"}), (b2, "reject", {})):
                if btn.button(decision.capitalize(), key=f"{decision}_{a['action_id']}", width="stretch", **kw):
                    if is_v2 and a["action_id"] == res.get("action_id"):   # the run's paused action
                        out = v2_graph.resume(res["trace_id"], decision, REVIEWER)
                        if not out.get("resumed"):              # checkpoint gone: fall back to a direct review
                            hitl.review_action(get_conn(), a["action_id"], decision, REVIEWER)
                        st.session_state[key] = {**res, "review": out}
                    else:
                        hitl.review_action(get_conn(), a["action_id"], decision, REVIEWER)
                    st.rerun()
    if res.get("review"):
        r = res["review"]
        st.caption(f"Graph resumed: {r.get('decision')} by {r.get('reviewer')} · applied {r.get('applied')}")


def _run(agent: str, ticket_id: str, batch_id: str, write: bool, conn=None) -> dict:
    mod = v2_graph if agent == "v2" else v1_single
    return {**mod.run_agent(ticket_id, batch_id=batch_id, conn=conn, write_action=write).to_dict(), "agent": agent}


def run_and_store(ticket_id: str, batch_id: str, key: str) -> None:
    if AGENT == "compare":
        with st.spinner(f"Running 1.0 and 2.0 on {ticket_id} (no actions are proposed in compare mode)…"):
            with ThreadPoolExecutor(2) as pool:
                f1 = pool.submit(_run, "v1", ticket_id, batch_id, False)
                f2 = pool.submit(_run, "v2", ticket_id, batch_id, False)
                st.session_state[key] = {"compare": [f1.result(), f2.result()], "ticket_id": ticket_id}
        return
    with st.spinner(f"AX-15 {'2.0' if AGENT == 'v2' else '1.0'} is working ticket {ticket_id}…"):
        st.session_state[key] = _run(AGENT, ticket_id, batch_id, True, get_conn())


def render_result(key: str) -> None:
    res = st.session_state.get(key)
    if not res:
        return
    st.divider()
    if "compare" in res:
        st.subheader(f"Compare · {res['ticket_id']}")
        cols = st.columns(2)
        for col, r, title in zip(cols, res["compare"], ("1.0 single agent", "2.0 LangGraph workflow")):
            with col:
                st.markdown(f"### {title}")
                st.caption(f"{r['status']} · {r['tool_calls']} tool calls · {usd(r['cost_usd'])} · "
                           f"{r['latency_s']:.1f}s · tokens {k(r['input_tokens'])}/{k(r['output_tokens'])}")
                render_answer(r, compact=True)
                if r["agent"] == "v2":
                    render_v2_details(r)
                if r.get("langfuse_url"):
                    st.markdown(f"[Langfuse trace]({r['langfuse_url']})")
        return
    st.subheader(f"Result · {res['ticket_id']} · AX-15 {'2.0' if res.get('agent') == 'v2' else '1.0'}")
    left, right = st.columns([3, 2])
    with left:
        render_answer(res)
        if res.get("agent") == "v2":
            render_v2_details(res)
    with right:
        render_trace(res)
        render_actions(res, key)


def show_ticket(ticket_id: str, batch_id: str) -> None:
    t = tools_fn.get_ticket(ticket_id, batch_id, conn=get_conn())
    if t.status != "ok":
        st.warning("Ticket not found.")
        return
    st.markdown(f"**{t.subject}**  \n`{t.priority}` · `{t.category}` / {t.subcategory or '—'} · store `{t.store_id}` · "
                f"asset `{t.asset_id or '—'}` · `{t.system_version or '—'}` · status {t.ticket_status or '—'}")
    with st.expander("Description and history"):
        st.write(t.description)
        for h in t.history:
            st.caption(f"{h.ts:%Y-%m-%d %H:%M} · {h.actor}: {h.note}" if h.ts else f"{h.actor}: {h.note}")


def heldout_summary(text: str) -> dict:
    """Aggregates only from outputs/heldout_variance.md (its per-ticket tables are not read): per agent
    ('1.0', '2.0') the run count, routing mean/min/max, escalation P/R means (verify excluded) and the
    count of tickets whose routing changed between runs; plus ticket count, exclusions and partial runs."""
    import re
    out: dict = {"agents": {}, "tickets": None, "excluded": None, "partial": []}
    if m := re.search(r"_(\d+) held-out tickets \(split `[^`]+` minus ([^)]+)\)", text):
        out["tickets"], out["excluded"] = int(m[1]), m[2]
    if m := re.search(r"\*\*Partial:\*\* (.+?): stopped", text):
        out["partial"] = [p.strip() for p in m[1].split(";")]
    cur = None
    for ln in text.splitlines():
        if ln.startswith("## "):
            cur = ln[3:].strip() if ln[3:].strip() in ("1.0", "2.0") else None
            if cur:
                out["agents"][cur] = {"runs": 0}
        elif cur is None:
            continue
        elif ln.startswith("| Metric |"):
            out["agents"][cur]["runs"] = sum(c.strip().startswith("Run ") for c in ln.split("|"))
        elif ln.startswith("| "):
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            n, a = out["agents"][cur]["runs"], out["agents"][cur]
            if cells[0] == "Routing L1/L2":
                a["routing"] = tuple(cells[n + 1:n + 4])
            elif cells[0] == "Escalation precision (verify excluded)":
                a["esc_p"] = cells[n + 1]
            elif cells[0] == "Escalation recall (verify excluded)":
                a["esc_r"] = cells[n + 1]
        elif m := re.match(r"\*\*Routing changed between the \d+ runs on (\d+) of (\d+) tickets", ln):
            out["agents"][cur]["changed"] = (int(m[1]), int(m[2]))
    return out


RESULTS_DIR = ROOT / "docs" / "results"           # shipped reports, used when outputs/ has none (fresh checkout)


def _report(generated: str, shipped: str):
    p = OUTPUTS_DIR / generated
    return p if p.exists() else RESULTS_DIR / shipped


HELDOUT_REPORT = _report("heldout_variance.md", "heldout_variance.md")


def eval_aggregates(agent: str) -> None:
    """Aggregates only: the per-ticket tables in the reports carry expected (label) values."""
    rep = _report(f"agent_eval_{agent}.md", {"v1": "agent_eval_v1.md", "v2": "agent_eval_v2_final.md"}.get(agent, ""))
    if not rep.exists():
        st.caption(f"No {agent} eval yet: `python eval/agent_eval.py --agent {agent} --split dev --chaos`.")
        return
    lines = rep.read_text(encoding="utf-8").splitlines()
    name = {"v1": "1.0", "v2": "2.0"}.get(agent, agent)
    ho = heldout_summary(HELDOUT_REPORT.read_text(encoding="utf-8")) if HELDOUT_REPORT.exists() else None
    ha = (ho or {}).get("agents", {}).get(name)
    header = next((ln for ln in lines if ln.startswith("_Generated")), "")
    held = (f" · held-out: **{ha['runs']} runs × {ho['tickets']} tickets ({ho['excluded']} excluded)**" if ha
            else " · held-out: no variance runs yet (`python eval/heldout_variance.py`)")
    header = re.sub(r" · held-out: \*\*[^*]+\*\*", held, header)
    rename = {"| all dev |": "| dev (tuned on) |",
              "| pilot scope (pos-/printer- main runbook) |": "| dev pilot (tuned on): pos-/printer- main runbook |"}
    table = [next((ln.replace(k, v) for k, v in rename.items() if ln.startswith(k)), ln)
             for ln in lines if ln.startswith("| all dev") or ln.startswith("| pilot scope")]
    head_idx = next((i for i, ln in enumerate(lines) if ln.startswith("| Scope |")), None)
    if head_idx is not None and ha and "routing" in ha:
        ncol = len(lines[head_idx].strip().strip("|").split("|"))
        mean, lo, hi = ha["routing"]
        row = [f"held-out (never tuned on): mean of {ha['runs']} runs", str(ho["tickets"]), "—",
               f"{mean} ({lo}–{hi})", ha.get("esc_p", "—"), ha.get("esc_r", "—")]
        table.append("| " + " | ".join(row + ["—"] * (ncol - len(row))) + " |")
    st.markdown(f"**AX-15 {agent}**")
    if header:
        st.caption(header.strip("_"))
    if head_idx is not None and table:
        st.markdown("\n".join([lines[head_idx], lines[head_idx + 1], *table]))
    if ha:
        changed = ha.get("changed")
        note = f"Held-out: routing changed between the {ha['runs']} runs on {changed[0]} / {changed[1]} tickets"
        note += "".join(f" · partial: {p}" for p in ho["partial"] if p.startswith(name + " "))
        st.caption(note + ". Escalation precision / recall are means with verify-mode answers excluded. "
                   f"Source: `{HELDOUT_REPORT.relative_to(ROOT)}`.")
    chaos_line = next((ln for ln in lines if ln.startswith("## Chaos set")), None)
    if chaos_line:
        st.markdown(f"**{chaos_line.removeprefix('## ')}**")
    st.caption(f"Full report (evaluator view): `{rep.relative_to(ROOT)}`")


# --- tabs -------------------------------------------------------------------------------------

tab_new, tab_existing, tab_batch, tab_kb, tab_system = st.tabs(
    ["New ticket", "Existing ticket", "Batch", "Ask the KB", "System"])

with tab_new:
    st.markdown("Create a ticket as a store would, then run the agent on it. Saved with `source='ui'`.")
    store_rows = stores()
    store_name = dict(store_rows)
    store_id = st.selectbox("Store", [s for s, _ in store_rows], format_func=lambda s: f"{s} · {store_name[s]}",
                            key="new_store")
    assets = tools_fn.list_store_assets(store_id, conn=get_conn()).assets
    asset_opts = [None] + [a.asset_id for a in assets]
    asset_label = {a.asset_id: f"{a.asset_id} · {a.asset_type} · {a.system} {a.version or ''}" for a in assets}
    asset_id = st.selectbox("Asset", asset_opts, format_func=lambda a: "— not sure —" if a is None else asset_label[a],
                            key="new_asset")
    c1, c2 = st.columns(2)
    category = c1.selectbox("Category", sorted(CATEGORIES), key="new_category")
    priority = c2.selectbox("Priority", ["P1", "P2", "P3", "P4"], index=2, key="new_priority")
    subject = st.text_input("Subject", key="new_subject", placeholder="e.g. Receipt printer offline on terminal 1")
    description = st.text_area("Description", key="new_description",
                               placeholder="What happened, what you already tried, how it affects service")
    if st.button("Create ticket and run AX-15", type="primary", key="new_run",
                 disabled=not (subject.strip() and description.strip()) or not RUN_OK):
        try:
            ticket, _uid = create_ui_ticket(get_conn(), store_id=store_id, asset_id=asset_id, category=category,
                                            priority=priority, subject=subject, description=description,
                                            submitted_by=REVIEWER)
            st.session_state["new_ticket"] = (ticket.ticket_id, ticket.batch_id)
            run_and_store(ticket.ticket_id, ticket.batch_id, "new_result")
        except TicketValidationError as e:
            st.error(f"Ticket not valid: {e}")
    if st.session_state.get("new_ticket"):
        tid, bid = st.session_state["new_ticket"]
        st.caption(f"Created `{tid}` in batch `{bid}`")
    render_result("new_result")

with tab_existing:
    kind = st.radio("Ticket set", ["Dev split", "Chaos set"], horizontal=True, key="ex_kind")
    choices = ticket_choices("dev" if kind == "Dev split" else "chaos")
    subj = dict(choices)
    tid = st.selectbox("Ticket", [c[0] for c in choices], format_func=lambda t: f"{t} · {subj[t]}", key="ex_ticket")
    show_ticket(tid, "train")
    if st.button("Run AX-15", type="primary", key="ex_run", disabled=not RUN_OK):
        run_and_store(tid, "train", "ex_result")
    render_result("ex_result")

with tab_batch:
    st.markdown("Run a test batch: upload CSV / JSON / .txt tickets (or pick a folder already in `incoming/`). "
                "Tickets are loaded with `source='panel_test'`; runs use eval mode (no actions are proposed). "
                "Same path as `ax15 run-batch incoming/<batch>`.")
    b_agent = "v1" if AGENT == "v1" else "v2"
    if AGENT == "compare":
        st.caption("Compare mode: batches run with 2.0.")
    incoming = ROOT / "incoming"
    b_src = st.radio("Input", ["Upload files", "Folder in incoming/"], horizontal=True, key="batch_src")
    b_files = None
    if b_src == "Upload files":
        b_name = st.text_input("Batch name", value=f"panel-{time.strftime('%Y%m%d')}", key="batch_name")
        b_files = st.file_uploader("Ticket files", type=["csv", "json", "txt"], accept_multiple_files=True,
                                   key="batch_files")
        b_ready = bool(b_files and b_name.strip())
    else:
        folders = sorted(p.name for p in incoming.iterdir() if p.is_dir()) if incoming.exists() else []
        b_name = st.selectbox("Folder", folders, key="batch_folder") if folders else None
        if not folders:
            st.caption("No batch folders in `incoming/` yet.")
        b_ready = bool(b_name)
    if st.button(f"Run batch with AX-15 {'2.0' if b_agent == 'v2' else '1.0'}", type="primary", key="batch_run",
                 disabled=not b_ready or not RUN_OK):
        folder = incoming / b_name.strip()
        try:
            batch_mod.batch_name(folder)
            if b_files:
                folder.mkdir(parents=True, exist_ok=True)
                for f in b_files:
                    (folder / Path(f.name).name).write_bytes(f.getvalue())
            with st.spinner(f"Loading and running batch `{folder.name}`…"):
                st.session_state["batch_result"] = batch_mod.run_batch(folder, agent=b_agent)
        except (ValueError, FileNotFoundError) as e:
            st.error(str(e))
    bres = st.session_state.get("batch_result")
    if bres:
        s = bres.summary()
        st.markdown(f"#### Batch `{bres.batch_id}` · AX-15 {'2.0' if bres.agent == 'v2' else '1.0'}")
        c = st.columns(6)
        c[0].metric("Loaded", f"{s['loaded']}/{s['rows_read']}")
        c[1].metric("Rejected", s["rejected"])
        c[2].metric("Escalations", s["escalations"])
        c[3].metric("Priority changed", s["priority_changed"])
        c[4].metric("Cost", usd(s["cost_usd"]))
        c[5].metric("Median time", f"{s['median_latency_s']:.1f}s")
        st.caption("Routing: " + (" · ".join(f"{ROUTING_SHORT.get(a, a)} {n}" for a, n in s["routing"].items()) or "—"))
        if bres.metrics:
            m = bres.metrics
            rec = "—" if m["esc_recall"] is None else f"{m['esc_recall']:.1%}"
            st.caption(f"Scored against the batch's own labels ({m['n']} tickets): routing L1/L2 "
                       f"{m['routing_l1_l2']:.1%} · escalation recall {rec}")
        if bres.stopped:
            st.warning(f"Stopped early: {bres.stopped}")
        if bres.intake.rejected:
            with st.expander(f"Rejected ({len(bres.intake.rejected)}): reported and skipped", expanded=True):
                st.table(bres.intake.rejected)
        if bres.intake.warnings:
            with st.expander(f"Intake warnings ({len(bres.intake.warnings)})"):
                st.markdown("\n".join(f"- {w}" for w in bres.intake.warnings))
        table = [{"ticket": r["ticket_id"], "from": r["source_ref"],
                  "routing": ROUTING_SHORT.get(r["routing"], r["routing"] or r["status"]),
                  "escalate": "yes" if r["escalate"] else "",
                  "priority": f"{r['priority_ticket'] or '—'} → {r['priority_agent'] or '—'}",
                  "fired rules": r["fired_rules"], "runbook": r["runbook"], "flags": r["flags"],
                  "cost": usd(r["cost_usd"]), "time": f"{r['latency_s']:.1f}s"} for r in bres.rows]
        st.caption("Click a row to open its answer and trace.")
        ev = st.dataframe(table, on_select="rerun", selection_mode="single-row", key="batch_table",
                          hide_index=True, width="stretch")
        st.caption(f"Report: `outputs/batch_{bres.batch_id}_report.md` and `.csv`")
        selection = (ev.get("selection") if isinstance(ev, dict) else getattr(ev, "selection", None)) or {}
        sel = list(selection.get("rows", []) if isinstance(selection, dict) else selection.rows)
        if sel and sel[0] < len(bres.rows):
            tid = bres.rows[sel[0]]["ticket_id"]
            run = bres.runs.get(tid)
            st.divider()
            st.subheader(f"{tid} · AX-15 {'2.0' if bres.agent == 'v2' else '1.0'}")
            show_ticket(tid, bres.batch_id)
            if run:
                left, right = st.columns([3, 2])
                with left:
                    render_answer(run)
                    if run.get("agent") == "v2":
                        render_v2_details(run)
                with right:
                    render_trace(run)

with tab_kb:
    st.caption("Retrieval layer on its own (plain RAG). The agents add select-don't-write on top.")
    ASK_OK = RUN_OK and bool(CHECKS.get("openai") and CHECKS["openai"].ok)
    if not ASK_OK:
        st.warning("Ask is disabled: Neon, Anthropic and OpenAI (query embeddings) must be green.")
    kq = st.text_area("Question", key="kb_question",
                      placeholder="e.g. Receipt printer stopped printing after a driver update")
    c1, c2 = st.columns(2)
    kb_audience = c1.radio("Who is asking?", ["Store staff", "L2 agent"], horizontal=True, key="kb_audience",
                           help="Store staff never see sections tagged L2-only; they get a withheld stub instead.")
    kb_k = c2.slider("Sections to retrieve", 3, 10, 5, key="kb_k")
    if st.button("Ask", type="primary", key="kb_ask", disabled=not (kq or "").strip() or not ASK_OK):
        aud = "store" if kb_audience == "Store staff" else "agent"
        with st.spinner("Searching the knowledge base…"):
            found = search_with_stubs(get_conn(), kq, k=kb_k, audience=aud)
        with st.spinner("Writing a short cited answer…"):
            st.session_state["kb_result"] = {"q": kq, "hits": found.hits, "stubs": found.stubs,
                                             "answer": kb_answer(kq, found.hits, stubs=found.stubs)}
    res = st.session_state.get("kb_result")
    if res:
        st.markdown("#### Top sections")
        if not res["hits"]:
            st.info("No sections matched.")
        for i, h in enumerate(res["hits"], 1):
            tag = " · 🔒 L2-only" if h.audience == "L2" else ""
            with st.expander(f"[{i}] {h.breadcrumb}  —  score {h.score:.4f}{tag}"):
                st.caption(f"`{h.doc_path}` · section: {h.section_type}")
                st.markdown(h.text)
        for s_ in res["stubs"]:
            st.info(f"🔒 L2-only: {s_.line}  \n`{s_.doc_path}` · would have ranked [{s_.rank}] · "
                    "text withheld from store staff")
        st.markdown("#### Answer")
        a = res["answer"]
        (st.success if a.covered else st.warning)(a.text)
        st.caption(f"{a.model} · {a.input_tokens} in / {a.output_tokens} out tokens · "
                   "uses only the sections above; [n] refers to the section numbers")

with tab_system:
    conn = get_conn()
    docs, chunks, l2 = conn.execute(
        "SELECT (SELECT count(*) FROM kb_documents), count(*), count(*) FILTER (WHERE metadata->>'audience' = 'L2')"
        " FROM kb_chunks").fetchone()
    c1, c2, c3 = st.columns(3)
    c1.metric("KB documents", docs)
    c2.metric("KB chunks", chunks)
    c3.metric("L2-only chunks", l2)
    st.markdown("#### Tickets by source")
    rows = conn.execute("SELECT source, batch_id, count(*) FROM tickets GROUP BY 1, 2 ORDER BY 1, 2").fetchall()
    st.table([{"source": s, "batch": b, "tickets": n} for s, b, n in rows])

    st.markdown("#### Console tickets")
    counts = ui_ticket_counts(conn)
    st.caption(f"{counts['tickets']} ticket(s) with source='ui' · {counts['actions']} action_log row(s)")
    if st.button("Clear console tickets", key="clear_ui", disabled=counts["tickets"] == 0):
        st.session_state["confirm_clear"] = True
    if st.session_state.get("confirm_clear"):
        st.warning(f"Delete {counts['tickets']} console ticket(s) and their {counts['actions']} action_log row(s)? "
                   "Train and eval tickets are not touched. This cannot be undone.")
        y, n_, _ = st.columns([1, 1, 6])
        if y.button("Yes, delete", type="primary", key="clear_yes"):
            out = clear_ui_tickets(conn)
            st.session_state["confirm_clear"] = False
            for key in ("new_result", "new_ticket"):
                st.session_state.pop(key, None)
            st.session_state["cleared"] = out
            st.rerun()
        if n_.button("Cancel", key="clear_no"):
            st.session_state["confirm_clear"] = False
            st.rerun()
    if st.session_state.get("cleared"):
        o = st.session_state["cleared"]
        st.success(f"Deleted {o['tickets']} ticket(s), {o['actions']} action(s), {o['checkpoint_rows']} checkpoint row(s).")

    st.markdown("#### Models (config/models.yaml)")
    llm, emb, fb = base_llm(), embedding_cfg(), fallback_llm()
    st.table([{"role": "base LLM", "model": llm["model"], "notes": f"effort {llm.get('effort')}"},
              {"role": "fallback (2.0)", "model": fb["model"], "notes": "on Anthropic API errors"},
              {"role": "embeddings", "model": emb["model"], "notes": f"{emb['dimensions']} dims"}])
    st.markdown("#### Last eval")
    for ag in ("v1", "v2"):
        eval_aggregates(ag)
