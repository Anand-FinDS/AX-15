"""Evaluator-only helpers for the retrieval bot.

Reads expected documents from eval.eval_labels for display and scoring.
Nothing here may be passed to search or to the answer model (no label leakage into prompts):
the bot calls these only after the model answer has been produced.
"""
from __future__ import annotations

from dataclasses import dataclass

import psycopg


def expected_docs(conn: psycopg.Connection, ticket_uid: int) -> list[str] | None:
    """relevant_kb_docs for a labelled ticket, or None if the ticket has no label."""
    row = conn.execute(
        "SELECT relevant_kb_docs FROM eval.eval_labels WHERE ticket_uid = %s", (ticket_uid,)
    ).fetchone()
    return list(row[0]) if row else None


@dataclass(frozen=True)
class DocScore:
    expected: list[tuple[str, int | None]]   # (doc_path, first rank it was retrieved at, or None)
    main_runbook: str | None
    found: int

    @property
    def recall(self) -> float:
        return self.found / len(self.expected) if self.expected else 0.0

    @property
    def main_hit(self) -> bool | None:
        if self.main_runbook is None:
            return None
        return dict(self.expected)[self.main_runbook] is not None


def score_docs(expected: list[str], retrieved_doc_paths: list[str]) -> DocScore:
    """Mark each expected doc with the first chunk rank (1-based) where it was retrieved."""
    first_rank: dict[str, int] = {}
    for i, d in enumerate(retrieved_doc_paths, 1):
        first_rank.setdefault(d, i)
    marked = [(d, first_rank.get(d)) for d in expected]
    main = next((d for d in expected if d.startswith("runbooks/")), None)
    return DocScore(marked, main, sum(1 for _, r in marked if r is not None))
