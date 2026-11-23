"""Write canonical tickets (+ history) to Postgres. Idempotent per (source, batch_id, ticket_id)."""
from __future__ import annotations

import json

import psycopg

from ax15.l0_data.normalise import Ticket

TICKET_COLS = (
    "ticket_id", "store_id", "store_name", "submitted_by", "contact_phone", "date_opened",
    "priority", "category", "subcategory", "subject", "description", "asset_id",
    "system_version", "tags", "escalation_flag", "related_tickets", "resolution_notes",
    "status", "source", "batch_id", "input_format", "raw_input",
)
_KEY = ("source", "batch_id", "ticket_id")

UPSERT_TICKET = (
    f"INSERT INTO tickets ({', '.join(TICKET_COLS)}) VALUES ({', '.join(['%s'] * len(TICKET_COLS))})"
    f" ON CONFLICT ({', '.join(_KEY)}) DO UPDATE SET "
    + ", ".join(f"{c} = EXCLUDED.{c}" for c in TICKET_COLS if c not in _KEY)
    + ", loaded_at = now() RETURNING ticket_uid"
)


def upsert_batch_run(conn: psycopg.Connection, batch_id: str, batch_name: str, source: str) -> None:
    conn.execute(
        "INSERT INTO batch_runs (batch_id, batch_name, source) VALUES (%s, %s, %s)"
        " ON CONFLICT (batch_id) DO UPDATE SET status = 'running', started_at = now(), finished_at = NULL",
        (batch_id, batch_name, source),
    )


def finish_batch_run(
    conn: psycopg.Connection, batch_id: str, *, total: int, loaded: int, rejected: int,
    errors: list[dict], status: str = "completed", report_path: str | None = None,
) -> None:
    conn.execute(
        "UPDATE batch_runs SET status = %s, files_total = %s, files_loaded = %s, files_rejected = %s,"
        " errors = %s::jsonb, report_path = %s, finished_at = now() WHERE batch_id = %s",
        (status, total, loaded, rejected, json.dumps(errors), report_path, batch_id),
    )


def load_tickets(conn: psycopg.Connection, tickets: list[Ticket]) -> dict[str, int]:
    """Upsert tickets and replace their source-loaded history. Returns {ticket_id: ticket_uid}.

    History rows written by the agent later carry a trace_id; only rows with
    trace_id IS NULL (loaded from source) are replaced, so agent notes survive a reload.
    """
    if not tickets:
        return {}
    with conn.cursor() as cur:
        cur.executemany(
            UPSERT_TICKET,
            [tuple(getattr(t, c) for c in TICKET_COLS) for t in tickets],
            returning=True,
        )
        uids = []
        while True:
            uids.append(cur.fetchone()[0])
            if not cur.nextset():
                break
        uid_by_id = {t.ticket_id: uid for t, uid in zip(tickets, uids, strict=True)}

        cur.execute(
            "DELETE FROM ticket_history WHERE ticket_uid = ANY(%s) AND trace_id IS NULL",
            (list(uid_by_id.values()),),
        )
        rows = [
            (uid_by_id[t.ticket_id], h.seq, h.ts, h.actor, h.note)
            for t in tickets for h in t.history
        ]
        if rows:
            cur.executemany(
                "INSERT INTO ticket_history (ticket_uid, seq, ts, actor, note) VALUES (%s, %s, %s, %s, %s)",
                rows,
            )
    return uid_by_id
