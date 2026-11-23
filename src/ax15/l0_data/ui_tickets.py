"""Tickets created in the Streamlit console (L0 live intake).

create_ui_ticket() builds an ITSM-style ticket dict, runs it through the same normaliser and
Ticket model as loaded tickets (leakage strip included), and stores it with source='ui' and
batch_id='ui-<YYYYMMDD>'. Train/eval code filters on batch_id='train', so UI tickets never mix in.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import psycopg

from ax15.l0_data.normalise import Ticket, TicketValidationError, from_json
from ax15.l0_data.tickets_db import load_tickets, upsert_batch_run

UI_SOURCE = "ui"


def ui_batch_id(now: datetime | None = None) -> str:
    return f"ui-{(now or datetime.now(timezone.utc)):%Y%m%d}"


def new_ticket_id(now: datetime | None = None) -> str:
    return f"UI-{(now or datetime.now(timezone.utc)):%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"


def create_ui_ticket(conn: psycopg.Connection, *, store_id: str, subject: str, description: str,
                     category: str, priority: str, asset_id: str | None = None, submitted_by: str | None = None,
                     now: datetime | None = None) -> tuple[Ticket, int]:
    """Validate and store one console ticket. Returns (ticket, ticket_uid).

    store_name and system_version come from master data (stores / CMDB asset), not from the form.
    Raises TicketValidationError for unusable input (e.g. unknown store, empty subject).
    """
    now = now or datetime.now(timezone.utc)
    store = conn.execute("SELECT store_name FROM stores WHERE store_id = %s", (store_id,)).fetchone()
    if store is None:
        raise TicketValidationError(f"unknown store {store_id!r}")
    if not (subject or "").strip() or not (description or "").strip():
        raise TicketValidationError("subject and description are required")
    system_version = None
    if asset_id:
        a = conn.execute("SELECT system_version FROM assets WHERE asset_id = %s AND store_id = %s",
                         (asset_id, store_id)).fetchone()
        if a is None:
            raise TicketValidationError(f"asset {asset_id!r} is not registered at {store_id}")
        system_version = a[0]
    batch = ui_batch_id(now)
    data = {"ticket_id": new_ticket_id(now), "store_id": store_id, "store_name": store[0],
            "submitted_by": submitted_by or "console-user", "date_opened": now.isoformat(), "priority": priority,
            "category": category, "subject": subject, "description": description, "asset_id": asset_id,
            "system_version": system_version, "status": "Open"}
    ticket = from_json(data, source=UI_SOURCE, batch_id=batch).ticket       # same model + leakage strip
    with conn.transaction():
        upsert_batch_run(conn, batch, f"Console tickets {now:%Y-%m-%d}", UI_SOURCE)
        uid = load_tickets(conn, [ticket])[ticket.ticket_id]
    return ticket, uid


def ui_ticket_counts(conn: psycopg.Connection) -> dict:
    """How many console tickets / their action_log rows exist (for the confirm step)."""
    t, a = conn.execute(
        "SELECT count(DISTINCT t.ticket_uid), count(a.action_id) FROM tickets t"
        " LEFT JOIN action_log a USING (ticket_uid) WHERE t.source = 'ui'").fetchone()
    return {"tickets": t, "actions": a}


def clear_ui_tickets(conn: psycopg.Connection) -> dict:
    """Delete every console ticket (source='ui') with its action_log rows, history and 2.0 checkpoints.
    Train / panel / eval data is never touched (filter is source = 'ui' only). One transaction."""
    with conn.transaction():
        traces = [r[0] for r in conn.execute(
            "SELECT DISTINCT a.trace_id FROM action_log a JOIN tickets t USING (ticket_uid)"
            " WHERE t.source = 'ui' AND a.trace_id IS NOT NULL").fetchall()]
        actions = conn.execute("DELETE FROM action_log WHERE ticket_uid IN"
                               " (SELECT ticket_uid FROM tickets WHERE source = 'ui')").rowcount
        tickets = conn.execute("DELETE FROM tickets WHERE source = 'ui'").rowcount   # history cascades
        cps = 0
        if traces and conn.execute("SELECT to_regclass('public.checkpoints')").fetchone()[0]:
            for tbl in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                cps += conn.execute(f"DELETE FROM {tbl} WHERE thread_id = ANY(%s)", (traces,)).rowcount
    return {"tickets": tickets, "actions": actions, "checkpoint_rows": cps}
