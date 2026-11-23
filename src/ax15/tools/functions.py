"""L2 tool functions: plain typed Python, shared by 1.0 (model tool calls) and 2.0 (graph nodes).

Rules: never raise on a missing record (return status "not_found" / "mismatch"); read-only except
log_action (the only write: simulated ITSM into action_log); no LLM calls except inside kb_route.
Every function takes an optional `conn` (tests, transactions); otherwise a shared connection is used.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone

import psycopg

from ax15.l1_knowledge.picker import route_and_pick
from ax15.l1_knowledge.search import search_kb
from ax15.tools import _db
from ax15.tools.models import (AssetOut, AssetSummary, HistoryNote, KbRouteOut, KbSectionOut, LogActionOut,
                               PastAction, PastActionsOut, RecentTicket, RecentTicketsOut, RouteCandidate, SectionBlock, SectionChunk, SlaOut,
                               StoreAssetsOut, StoreOut, TicketOut)

Conn = psycopg.Connection | None


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --- tickets ----------------------------------------------------------------------------------

# Explicit prompt-safe column list (tickets has no label columns; eval.* is never touched here).
_TICKET_COLS = ("ticket_uid, ticket_id, batch_id, source, store_id, store_name, submitted_by, date_opened, priority,"
                " category, subcategory, subject, description, asset_id, system_version, tags, escalation_flag,"
                " related_tickets, status, resolution_notes")


def _find_ticket(conn, ticket_id: str, batch_id: str | None):
    return conn.execute(
        f"SELECT {_TICKET_COLS} FROM public.tickets WHERE ticket_id = %s AND (%s::text IS NULL OR batch_id = %s)"
        " ORDER BY loaded_at DESC LIMIT 1", (ticket_id, batch_id, batch_id),
    ).fetchone()


def get_ticket(ticket_id: str, batch_id: str | None = None, *, conn: Conn = None) -> TicketOut:
    """Prompt-safe ticket fields + history notes. Never labels, chaos_type or eval.*"""
    def q(c):
        r = _find_ticket(c, ticket_id, batch_id)
        if r is None:
            return TicketOut(status="not_found", ticket_id=ticket_id, message="no such ticket")
        hist = c.execute("SELECT seq, ts, actor, note FROM ticket_history WHERE ticket_uid = %s ORDER BY seq",
                         (r[0],)).fetchall()
        return TicketOut(
            status="ok", ticket_id=r[1], batch_id=r[2], source=r[3], store_id=r[4], store_name=r[5],
            submitted_by=r[6], date_opened=r[7], priority=r[8], category=r[9], subcategory=r[10], subject=r[11],
            description=r[12], asset_id=r[13], system_version=r[14], tags=list(r[15] or []),
            escalation_flag=r[16], related_tickets=list(r[17] or []), ticket_status=r[18], resolution_notes=r[19],
            history=[HistoryNote(seq=h[0], ts=h[1], actor=h[2], note=h[3]) for h in hist],
        )
    return _db.run(q, conn)


def get_recent_tickets(store_id: str, asset_id: str | None = None, days: int = 30, as_of: datetime | None = None,
                       exclude_ticket_id: str | None = None, *, conn: Conn = None) -> RecentTicketsOut:
    """Tickets at the store (optionally the same asset) in the `days` before `as_of`.
    Resolution notes are evidence from history (source="history"), not verified truth."""
    end = _utc(as_of) if as_of else datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    def q(c):
        if not c.execute("SELECT 1 FROM stores WHERE store_id = %s", (store_id,)).fetchone():
            return RecentTicketsOut(status="not_found", store_id=store_id, asset_id=asset_id,
                                    message="store not in master data")
        rows = c.execute(
            "SELECT ticket_id, date_opened, category, subcategory, subject, status, asset_id, resolution_notes"
            " FROM public.tickets WHERE store_id = %s AND date_opened BETWEEN %s AND %s"
            " AND (%s::text IS NULL OR asset_id = %s) AND (%s::text IS NULL OR ticket_id <> %s)"
            " ORDER BY date_opened DESC LIMIT 50",
            (store_id, start, end, asset_id, asset_id, exclude_ticket_id, exclude_ticket_id),
        ).fetchall()
        tickets = [RecentTicket(ticket_id=r[0], date_opened=r[1], category=r[2], subcategory=r[3], subject=r[4],
                                ticket_status=r[5], asset_id=r[6], resolution_notes=r[7]) for r in rows]
        return RecentTicketsOut(status="ok", store_id=store_id, asset_id=asset_id, window_start=start,
                                window_end=end, tickets=tickets, recurrence_count=len(tickets))
    return _db.run(q, conn)


# --- stores and assets ------------------------------------------------------------------------

def get_store(store_id: str, *, conn: Conn = None) -> StoreOut:
    def q(c):
        r = c.execute("SELECT store_id, store_name, city, neighborhood, store_type, region_manager, support_phone"
                      " FROM stores WHERE store_id = %s", (store_id,)).fetchone()
        if r is None:
            return StoreOut(status="not_found", store_id=store_id, message="store not in master data")
        return StoreOut(status="ok", store_id=r[0], store_name=r[1], city=r[2], neighborhood=r[3],
                        store_type=r[4], region_manager=r[5], support_phone=r[6])
    return _db.run(q, conn)


def split_system_version(make: str | None, system_version: str | None) -> tuple[str | None, str | None]:
    """CMDB 'FoodTech POS v5.1.0' with make 'FoodTech POS' -> ('FoodTech POS', 'v5.1.0')."""
    sv = (system_version or "").strip()
    if make and sv.lower().startswith(make.lower()):
        return make, sv[len(make):].strip() or None
    return parse_claimed(sv, [make] if make else [])


_VERSION_TOKEN = re.compile(r"^v?\d+(\.\d+)*$", re.I)


def parse_claimed(text: str | None, known_systems: list[str]) -> tuple[str | None, str | None]:
    """'FoodTech POS v4.2.1' -> ('FoodTech POS', 'v4.2.1'), preferring the longest known system prefix."""
    t = (text or "").strip()
    if not t:
        return None, None
    for s in sorted((k for k in known_systems if k), key=len, reverse=True):
        if t.lower().startswith(s.lower()):
            return s, t[len(s):].strip() or None
    parts = t.split()
    if len(parts) > 1 and _VERSION_TOKEN.match(parts[-1]):
        return " ".join(parts[:-1]), parts[-1]
    return t, None


def _norm(s: str | None) -> str:
    return re.sub(r"[\s\-]+", "", (s or "").lower())


def get_asset(asset_id: str, claimed_system: str | None = None, *, conn: Conn = None) -> AssetOut:
    """CMDB asset + version-matrix status. claimed_system (what the ticket says) -> mismatch flag."""
    def q(c):
        r = c.execute("SELECT asset_id, store_id, asset_type, make, model, system_version, install_date,"
                      " warranty_expiry FROM assets WHERE asset_id = %s", (asset_id,)).fetchone()
        if r is None:
            return AssetOut(status="not_found", asset_id=asset_id, claimed_system=claimed_system,
                            message="asset not in CMDB")
        system, version = split_system_version(r[3], r[5])
        vm = c.execute("SELECT known_bugs, patch_available, notes FROM version_matrix"
                       " WHERE lower(system) = lower(%s) AND lower(version) = lower(%s)", (system, version)).fetchone()
        if vm is None:
            vstatus = "not_in_matrix"
        else:
            vstatus = "supported" if (vm[0] or "").strip().lower() in ("", "none known") else "known_issues"
        out = AssetOut(
            status="ok", asset_id=r[0], store_id=r[1], asset_type=r[2], vendor=r[3], model=r[4], system=system,
            version=version, install_date=r[6], warranty_expiry=r[7],
            warranty_expired=None if r[7] is None else r[7] < date.today(),
            version_status=vstatus, known_bugs=vm[0] if vm else None, patch_available=vm[1] if vm else None,
            version_notes=vm[2] if vm else None, claimed_system=claimed_system,
        )
        if claimed_system:
            known = [s for (s,) in c.execute("SELECT DISTINCT system FROM version_matrix")] + [system or ""]
            c_sys, c_ver = parse_claimed(claimed_system, known)
            if _norm(c_sys) != _norm(system):
                out.status, out.mismatch, out.mismatch_kind = "mismatch", True, "system"
                out.message = f"ticket names '{claimed_system}' but CMDB has '{system} {version or ''}'".strip()
            elif c_ver and version and _norm(c_ver) != _norm(version):
                out.status, out.mismatch, out.mismatch_kind = "mismatch", True, "version"
                out.message = f"ticket says {c_ver}, CMDB has {version}"
        return out
    return _db.run(q, conn)


def list_store_assets(store_id: str, *, conn: Conn = None) -> StoreAssetsOut:
    def q(c):
        if not c.execute("SELECT 1 FROM stores WHERE store_id = %s", (store_id,)).fetchone():
            return StoreAssetsOut(status="not_found", store_id=store_id, message="store not in master data")
        rows = c.execute("SELECT asset_id, asset_type, make, model, system_version FROM assets"
                         " WHERE store_id = %s ORDER BY asset_id", (store_id,)).fetchall()
        assets = []
        for a in rows:
            system, version = split_system_version(a[2], a[4])
            assets.append(AssetSummary(asset_id=a[0], asset_type=a[1], system=system, model=a[3], version=version))
        return StoreAssetsOut(status="ok", store_id=store_id, assets=assets)
    return _db.run(q, conn)


# --- SLA --------------------------------------------------------------------------------------

def get_sla(priority: str, category: str, created_at: datetime, now: datetime | None = None,
            *, conn: Conn = None) -> SlaOut:
    """Due times from sla_matrix. No peak-hour logic here (that is an L3 rule)."""
    created = _utc(created_at)
    now = _utc(now) if now else datetime.now(timezone.utc)

    def q(c):
        r = c.execute("SELECT first_response_minutes, resolution_target_minutes, escalation_to_l2_minutes"
                      " FROM sla_matrix WHERE priority = %s AND category = %s", (priority, category)).fetchone()
        if r is None:
            return SlaOut(status="not_found", priority=priority, category=category,
                          message="no SLA row for this priority/category")
        resp, res, esc = (created + timedelta(minutes=m) for m in r)
        left = lambda due: int((due - now).total_seconds() // 60)  # noqa: E731
        return SlaOut(status="ok", priority=priority, category=category, first_response_minutes=r[0],
                      resolution_target_minutes=r[1], escalation_to_l2_minutes=r[2], response_due=resp,
                      resolution_due=res, l2_escalation_due=esc, minutes_left_response=left(resp),
                      minutes_left_resolution=left(res), response_breached=now > resp, breached=now > res)
    return _db.run(q, conn)


# --- knowledge base ---------------------------------------------------------------------------

def kb_route(ticket_text: str, audience: str = "store", *, conn: Conn = None, client=None) -> KbRouteOut:
    """route_and_pick: top-3 runbooks (no LLM), then the picker chooses one or none (LLM)."""
    if not ticket_text.strip():
        return KbRouteOut(status="not_found", message="empty ticket text")
    route, pick = _db.run(lambda c: route_and_pick(c, ticket_text, {"audience": audience}, client=client), conn)
    cands = [RouteCandidate(doc=d.doc, score=d.score, top_chunk_breadcrumbs=d.top_chunk_breadcrumbs)
             for d in route.docs]
    usage = {"input_tokens": pick.input_tokens, "output_tokens": pick.output_tokens, "attempts": pick.attempts}
    if not cands:
        return KbRouteOut(status="not_found", message="no runbook matched", reason=pick.reason, usage=usage)
    return KbRouteOut(status="ok", candidates=cands, doc=pick.doc, reason=pick.reason, usage=usage)


_ESCALATE_RE = [re.compile(r"when to escalate", re.I), re.compile(r"escalat", re.I)]


def _l2_stub(breadcrumb: str) -> str:
    return f"L2-only procedure exists: {breadcrumb} — must be performed by L2."


def _block(rows, h2: str | None, audience: str) -> SectionBlock:
    chunks = []
    for cid, _idx, crumb, text, meta in rows:
        if audience == "store" and meta.get("audience") == "L2":
            chunks.append(SectionChunk(chunk_id=cid, breadcrumb=crumb, l2_stub=_l2_stub(crumb)))  # never the text
        else:
            chunks.append(SectionChunk(chunk_id=cid, breadcrumb=crumb, text=text))
    return SectionBlock(h2=h2, chunks=chunks, text="\n\n".join(c.text or c.l2_stub for c in chunks))


def kb_section(doc: str, query: str, audience: str = "store", *, conn: Conn = None) -> KbSectionOut:
    """Hybrid search inside one doc -> the matched chunk's parent H2 section verbatim (original numbering),
    plus the doc's "When to Escalate" section and breadcrumbs for citing. L2-only chunks -> stub only."""
    def q(c):
        if not c.execute("SELECT 1 FROM kb_documents WHERE doc_path = %s", (doc,)).fetchone():
            return KbSectionOut(status="not_found", doc=doc, message="no such KB document")
        hits = search_kb(c, query, k=5, audience=audience, mode="hybrid", doc_path=doc)
        if not hits:
            return KbSectionOut(status="not_found", doc=doc, message="no section matched the query")
        top = hits[0]
        rows = c.execute("SELECT chunk_id, chunk_index, breadcrumb, text, metadata FROM kb_chunks"
                         " WHERE doc_path = %s AND status = 'approved' ORDER BY chunk_index", (doc,)).fetchall()
        h2_of = {r[0]: r[4].get("h2", []) for r in rows}
        m_h2 = h2_of[top.chunk_id]
        group = [r for r in rows if set(h2_of[r[0]]) & set(m_h2)] if m_h2 else [r for r in rows if r[0] == top.chunk_id]
        section = _block(group, m_h2[0] if m_h2 else None, audience)

        all_h2 = list(dict.fromkeys(h for r in rows for h in h2_of[r[0]]))
        esc_h2 = next((h for rx in _ESCALATE_RE for h in all_h2 if rx.search(h)), None)
        escalation = None
        if esc_h2:
            escalation = _block([r for r in rows if esc_h2 in h2_of[r[0]]], esc_h2, audience)
        cites = list(dict.fromkeys(ch.breadcrumb for b in (section, escalation) if b for ch in b.chunks))
        return KbSectionOut(status="ok", doc=doc, matched_chunk_id=top.chunk_id, matched_breadcrumb=top.breadcrumb,
                            section=section, escalation=escalation, citations=cites)
    return _db.run(q, conn)


# --- episodic memory: human-verified past actions ----------------------------------------------

_VERIFIED = ("approved", "rejected", "executed")


def get_past_actions(store_id: str, asset_id: str | None = None, days: int = 90, *, conn: Conn = None
                     ) -> PastActionsOut:
    """Human-verified action_log rows (approved / rejected / executed) for tickets at the store (optionally
    one asset) in the last `days`. Unverified 'proposed' rows are never returned."""
    since = datetime.now(timezone.utc) - timedelta(days=days)

    def q(c):
        if not c.execute("SELECT 1 FROM stores WHERE store_id = %s", (store_id,)).fetchone():
            return PastActionsOut(status="not_found", store_id=store_id, asset_id=asset_id,
                                  message="store not in master data")
        rows = c.execute(
            "SELECT coalesce(a.verified_at, a.created_at), t.ticket_id, a.action_type, a.payload->>'kb_doc',"
            " a.payload->>'routing', a.status, a.verified_by, t.asset_id"
            " FROM action_log a JOIN tickets t USING (ticket_uid)"
            " WHERE t.store_id = %s AND (%s::text IS NULL OR t.asset_id = %s)"
            " AND a.status = ANY(%s) AND a.verified_by_human AND a.created_at >= %s"
            " ORDER BY a.created_at DESC LIMIT 20",
            (store_id, asset_id, asset_id, list(_VERIFIED), since),
        ).fetchall()
        return PastActionsOut(status="ok", store_id=store_id, asset_id=asset_id, actions=[
            PastAction(date=r[0], ticket_id=r[1], action_type=r[2], runbook=r[3], routing=r[4], verdict=r[5],
                       verified_by=r[6], asset_id=r[7]) for r in rows])
    return _db.run(q, conn)


# --- the only write ---------------------------------------------------------------------------

def actor_type(actor: str) -> str:
    """'human:<user>' -> human, 'system:<job>' -> system, anything else (node names) -> agent."""
    prefix = actor.split(":", 1)[0].lower()
    return prefix if prefix in ("human", "system") else "agent"


_ACTION_TYPES = {"update": "update_ticket", "escalate": "escalate_to_l2", "notify": "notify_requester",
                 "work_note": "work_note", "cmdb_correction": "propose_cmdb_correction"}


def log_action(ticket_id: str, action: str, payload: dict | None = None, actor: str = "agent",
               verified_by_human: bool = False, trace_id: str | None = None, batch_id: str | None = None,
               *, conn: Conn = None) -> LogActionOut:
    """Simulated ITSM write (update / escalate / notify / work_note) into action_log, with a trace_id.
    Unverified actions are 'proposed'; human-verified ones are 'executed'."""
    if action not in _ACTION_TYPES:
        return LogActionOut(status="not_found", ticket_id=ticket_id, message=f"unknown action {action!r}")
    trace_id = trace_id or uuid.uuid4().hex
    action_type = _ACTION_TYPES[action]

    def q(c):
        t = _find_ticket(c, ticket_id, batch_id)
        if t is None:
            return LogActionOut(status="not_found", ticket_id=ticket_id, trace_id=trace_id, message="no such ticket")
        state = "executed" if verified_by_human else "proposed"
        r = c.execute(
            "INSERT INTO action_log (ticket_uid, trace_id, actor, actor_type, action_type, payload, status,"
            " verified_by_human, verified_at, executed_at)"
            " VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s, CASE WHEN %s THEN now() END, CASE WHEN %s THEN now() END)"
            " RETURNING action_id",
            (t[0], trace_id, actor, actor_type(actor), action_type, json.dumps(payload or {}, default=str),
             state, verified_by_human, verified_by_human, verified_by_human),
        ).fetchone()
        return LogActionOut(status="ok", ticket_id=ticket_id, action_id=r[0], action_type=action_type,
                            action_status=state, trace_id=trace_id)
    return _db.run(q, conn)
