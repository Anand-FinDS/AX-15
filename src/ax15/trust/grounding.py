"""Step grounding: is a returned step found verbatim (normalised whitespace) in the KB?

Shared by the eval (step fidelity) and the console (per-step badge). A leading list marker
("1.", "-", "*") on the step is ignored; everything else must match a kb_chunks.text substring.
"""
from __future__ import annotations

import re
import threading

import psycopg

_WS = re.compile(r"\s+")
_MARKER = re.compile(r"^\s*(?:\d+[.)]|[-*•])\s+")
_lock = threading.Lock()
_corpus: str | None = None


def normalise(text: str) -> str:
    return _WS.sub(" ", text).strip()


def load_corpus(conn: psycopg.Connection, refresh: bool = False) -> str:
    """All approved chunk texts, whitespace-normalised, joined by a separator no step can span."""
    global _corpus
    with _lock:
        if _corpus is None or refresh:
            rows = conn.execute("SELECT text FROM kb_chunks WHERE status = 'approved'").fetchall()
            _corpus = "\n\u0000\n".join(normalise(r[0]) for r in rows)
        return _corpus


def step_text(step: str) -> str:
    return normalise(_MARKER.sub("", step))


def is_grounded(step: str, corpus: str) -> bool:
    s = step_text(step)
    return len(s) >= 8 and s in corpus


def fidelity(steps: list[str], corpus: str) -> float | None:
    return sum(is_grounded(s, corpus) for s in steps) / len(steps) if steps else None
