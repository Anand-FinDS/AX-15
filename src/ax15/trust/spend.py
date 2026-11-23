"""Session spend guard: every LLM call's cost goes into outputs/spend_ledger.jsonl.

check() raises SpendCapExceeded once the session total passes config spend.session_cap_usd,
so batch runs stop instead of overspending. Session = AX15_SESSION env var, default today's date.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, timezone

from ax15.config import base_llm, models
from ax15.settings import OUTPUTS_DIR

LEDGER = OUTPUTS_DIR / "spend_ledger.jsonl"
_lock = threading.Lock()


class SpendCapExceeded(RuntimeError):
    pass


def session() -> str:
    return os.getenv("AX15_SESSION") or date.today().isoformat()


def cap() -> float:
    """Session cap: AX15_SPEND_CAP (per-session override) else config spend.session_cap_usd."""
    env = os.getenv("AX15_SPEND_CAP")
    return float(env) if env else float(models().get("spend", {}).get("session_cap_usd", 3.0))


def cost_of(input_tokens: int = 0, output_tokens: int = 0, cache_read: int = 0, cache_write: int = 0,
            price: dict | None = None) -> float:
    p = price or base_llm().get("price_per_mtok", {})
    return (input_tokens * p.get("input", 0) + output_tokens * p.get("output", 0)
            + cache_read * p.get("cache_read", p.get("input", 0) * 0.1)
            + cache_write * p.get("cache_write", p.get("input", 0) * 1.25)) / 1e6


def record(cost: float, source: str) -> None:
    if cost <= 0:
        return
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "session": session(), "source": source,
                       "cost_usd": round(cost, 6)})
    with _lock, LEDGER.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def total(sess: str | None = None) -> float:
    sess = sess or session()
    if not LEDGER.exists():
        return 0.0
    with _lock:
        lines = LEDGER.read_text(encoding="utf-8").splitlines()
    return sum(json.loads(l)["cost_usd"] for l in lines if l.strip() and json.loads(l)["session"] == sess)


def check() -> None:
    t = total()
    if t >= cap():
        raise SpendCapExceeded(f"session spend ${t:.2f} reached the ${cap():.2f} cap")
