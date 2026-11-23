"""Leakage stripping (no label leakage into prompts).

Evaluation-only information must never reach an agent/LLM prompt:
- label / evaluation keys that might appear in an input file (e.g. `chaos_type`),
- dataset-generator text embedded in free text, e.g. INC-00298's history note
  "Automatic escalation triggered due to unknown_system chaos type."

Text is cleaned at sentence level: any sentence that mentions a chaos type or
the word "chaos" is dropped, the rest of the text is kept unchanged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Keys that only exist in labels / the index, never in a real ITSM ticket.
LEAKAGE_KEYS = frozenset({
    "chaos_type", "chaos_correct_behavior", "correct_routing", "relevant_kb_docs",
    "should_escalate", "escalation_reason", "correct_resolution_summary",
    "expected_agent_action", "evaluator_notes", "labels", "label", "expected_behavior",
})

# "chaos" anywhere, or a snake_case chaos-type identifier. Plain-English words that
# are also chaos-type names ("vague", "emotional") are only caught next to "chaos".
_LEAK_RE = re.compile(
    r"\bchaos\b|\b(?:false_asset|red_herring|unknown_system|miscategori[sz]ed_\w+)\b",
    re.I,
)
# Sentence boundary: ., ! or ? followed by whitespace (so "v2.3" is not split), or a newline.
# The capture group keeps the separators so kept text is reassembled unchanged.
_BOUNDARY_RE = re.compile(r"((?<=[.!?])[ \t]+|\s*\n\s*)")


def has_leak(text: str | None) -> bool:
    return bool(text) and bool(_LEAK_RE.search(text))


def clean_text(text: str | None) -> tuple[str | None, list[str]]:
    """Remove leaking sentences. Returns (clean text or None if nothing left, removed sentences)."""
    if not text or not has_leak(text):
        return text, []
    kept, removed = [], []
    parts = _BOUNDARY_RE.split(text)               # [sentence, sep, sentence, sep, ...]
    for i in range(0, len(parts), 2):
        sentence, sep = parts[i], (parts[i + 1] if i + 1 < len(parts) else "")
        if _LEAK_RE.search(sentence):
            removed.append(sentence.strip())
        else:
            kept.append(sentence + sep)
    out = "".join(kept).strip()
    return (out or None), removed


@dataclass
class LeakReport:
    keys_dropped: list[str] = field(default_factory=list)
    sentences_removed: list[tuple[str, str]] = field(default_factory=list)  # (field path, sentence)

    @property
    def any(self) -> bool:
        return bool(self.keys_dropped or self.sentences_removed)

    def add_text(self, path: str, removed: list[str]) -> None:
        self.sentences_removed.extend((path, s) for s in removed)
