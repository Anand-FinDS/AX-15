"""Human-in-the-loop review of proposed actions in action_log.

The agent only writes status 'proposed'. A reviewer approves or rejects each one; the row records
verified_by_human, verified_by and verified_at. Only 'proposed' rows can be reviewed (no flip-flopping).
"""
from __future__ import annotations

from typing import Literal

import psycopg

Decision = Literal["approve", "reject"]
_STATUS = {"approve": "approved", "reject": "rejected"}


def proposed_actions(conn: psycopg.Connection, trace_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT action_id, action_type, status, actor, payload, verified_by_human, verified_by, verified_at, created_at"
        " FROM action_log WHERE trace_id = %s ORDER BY action_id", (trace_id,)).fetchall()
    keys = ["action_id", "action_type", "status", "actor", "payload", "verified_by_human", "verified_by",
            "verified_at", "created_at"]
    return [dict(zip(keys, r)) for r in rows]


def review_action(conn: psycopg.Connection, action_id: int, decision: Decision, reviewer: str = "console-user") -> bool:
    """Approve or reject one proposed action. Returns False if it was not in 'proposed' state."""
    if decision not in _STATUS:
        raise ValueError(f"decision must be approve or reject, not {decision!r}")
    r = conn.execute(
        "UPDATE action_log SET status = %s, verified_by_human = true, verified_by = %s, verified_at = now()"
        " WHERE action_id = %s AND status = 'proposed' RETURNING action_id",
        (_STATUS[decision], reviewer, action_id)).fetchone()
    return r is not None
