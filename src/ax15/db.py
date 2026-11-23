"""Database connections. App code uses the pooled DATABASE_URL; migrations prefer DATABASE_URL_DIRECT."""
from __future__ import annotations

import os
import sys

import psycopg

from ax15 import settings  # noqa: F401  (loads .env)

APP_VARS = ("DATABASE_URL",)
MIGRATION_VARS = ("DATABASE_URL_DIRECT", "DATABASE_URL")


def connect(env_vars: tuple[str, ...] = APP_VARS, **kwargs) -> tuple[psycopg.Connection, str]:
    """Connect with the first env var that works. Returns (connection, var name). Never prints URLs."""
    errors = []
    for var in env_vars:
        url = os.getenv(var, "").strip()
        if not url:
            errors.append(f"{var} not set")
            continue
        try:
            # TCP keepalives: a socket Neon dropped silently fails in ~1 min instead of hanging forever.
            ka = {"keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3}
            return psycopg.connect(url, connect_timeout=15, **{**ka, **kwargs}), var
        except psycopg.OperationalError as e:
            errors.append(f"{var}: {type(e).__name__}: {str(e).splitlines()[0][:160]}")
            if var != env_vars[-1]:
                print(f"[db] could not connect with {var}; trying fallback", file=sys.stderr)
    raise SystemExit("[db] no database connection: " + "; ".join(errors))


def configured() -> bool:
    return any(os.getenv(v, "").strip() for v in MIGRATION_VARS)
