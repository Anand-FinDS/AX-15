"""Shared connection for the tools: health-checked, reconnects once if Neon closed it."""
from __future__ import annotations

import threading
from collections.abc import Callable
from typing import TypeVar

import psycopg

from ax15 import db

T = TypeVar("T")
_lock = threading.Lock()
_conn: psycopg.Connection | None = None


def _fresh() -> psycopg.Connection:
    global _conn
    with _lock:
        if _conn is not None and not _conn.closed and not _conn.broken:
            try:
                _conn.execute("SELECT 1")
                return _conn
            except psycopg.OperationalError:
                pass
        if _conn is not None:
            _conn.close()
        _conn, _ = db.connect(autocommit=True)
        return _conn


def run(fn: Callable[[psycopg.Connection], T], conn: psycopg.Connection | None = None) -> T:
    """Run fn on the given connection, or on the shared one (retrying once after a reconnect).
    If a caller's connection was dropped by the server, retry once on a fresh shared connection."""
    if conn is not None:
        try:
            return fn(conn)
        except psycopg.OperationalError:
            if not (conn.closed or conn.broken):
                raise
            return fn(_fresh())
    try:
        return fn(_fresh())
    except psycopg.OperationalError:
        global _conn
        with _lock:
            if _conn is not None:
                _conn.close()
            _conn = None
        return fn(_fresh())
