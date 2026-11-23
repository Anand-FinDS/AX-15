"""Stage 1b of routing: the top-3 runbook picker (L1).

route_doc (search.py, no LLM) returns the top 3 runbooks; pick_doc is always called after it
(route_and_pick) and asks the base model (config/models.yaml) to choose one of them, or none. The prompt shows each candidate's title, Overview section
and H2/H3 heading list only, never full text. Output must be JSON
{"doc": <candidate id | "none">, "reason": "<one sentence>"}; invalid output is retried
once, then the pick is none. A none pick has doc = None ({"doc": null, ...}), meaning escalate:
no candidate runbook fits. An optional cache (by prompt hash) lets evals rerun for free.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

import anthropic

from ax15.config import base_llm
from ax15.l1_knowledge.search import RouteResult, route_doc
from ax15.l1_knowledge.chunker import _FENCE_RE, _HEADING_RE, _clean_heading, _first_paragraph
from ax15.settings import dataset_dir

NONE = "none"
MAX_OVERVIEW_CHARS = 700

SYSTEM = """You route IT support tickets for ServeWell restaurants to the single best knowledge-base document.
You get a ticket and up to 3 candidate documents, each with its id, title, overview and section headings.
Pick the candidate whose procedure addresses the ticket's actual underlying problem, not just shared keywords.
If no candidate fits, pick "none".
Reply with JSON only, no other text: {"doc": "<candidate id or none>", "reason": "<one sentence>"}"""

# 2.0 dual-query routing merges up to 5 candidates; with <= 3 the original SYSTEM is used unchanged
# (same prompt, same cache keys as the locked 1.0 routing).
SYSTEM_N = SYSTEM.replace("up to 3 candidate documents", "up to {n} candidate documents")


def system_for(n: int) -> str:
    return SYSTEM if n <= 3 else SYSTEM_N.replace("{n}", str(n))


RETRY_NOTE = "\n\nYour previous reply was not valid. Reply with the JSON object only."


@dataclass(frozen=True)
class DocCard:
    doc: str
    title: str
    overview: str
    headings: list[str]


@lru_cache(maxsize=None)
def doc_card(doc_path: str, kb_root: Path | None = None) -> DocCard:
    """Title, Overview section (or first paragraph) and H2/H3 headings of a KB doc (read-only)."""
    lines = ((kb_root or dataset_dir() / "kb") / doc_path).read_text(encoding="utf-8").splitlines()
    title, headings, overview, in_fence, in_overview = "", [], [], False, False
    for line in lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        m = None if in_fence else _HEADING_RE.match(line)
        if m:
            level, text = len(m.group(1)), _clean_heading(m.group(2))
            if level == 1 and not title:
                title = text
            elif level in (2, 3):
                headings.append(("" if level == 2 else "  - ") + text)
                in_overview = level == 2 and text.lower().startswith("overview")
            continue
        if in_overview:
            overview.append(line)
    text = re.sub(r"\s+", " ", "\n".join(overview)).strip() or _first_paragraph(lines)
    if len(text) > MAX_OVERVIEW_CHARS:
        text = text[:MAX_OVERVIEW_CHARS].rsplit(" ", 1)[0] + "…"
    return DocCard(doc_path, title or doc_path, text, headings)


def build_prompt(ticket_text: str, cards: list[DocCard]) -> str:
    blocks = []
    for c in cards:
        heads = "\n".join(h if h.startswith("  - ") else f"- {h}" for h in c.headings)
        blocks.append(f'<candidate id="{c.doc}">\nTitle: {c.title}\nOverview: {c.overview}\n'
                      f"Sections:\n{heads}\n</candidate>")
    return f"<ticket>\n{ticket_text}\n</ticket>\n\n<candidates>\n" + "\n\n".join(blocks) + "\n</candidates>"


def parse_pick(text: str, ids: list[str]) -> tuple[str, str] | None:
    """Validated (doc, reason), or None if the reply is not the required JSON."""
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s)          # tolerate a fenced reply
    try:
        obj = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict) or set(obj) != {"doc", "reason"}:
        return None
    doc, reason = obj["doc"], obj["reason"]
    if not isinstance(doc, str) or not isinstance(reason, str) or not reason.strip():
        return None
    if doc != NONE and doc not in ids:
        return None
    return doc, reason.strip()


@dataclass
class Pick:
    doc: str | None            # None = no candidate fits (escalate)
    reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    attempts: int = 0
    valid: bool = True
    cached: bool = False

    def as_dict(self) -> dict:
        return {"doc": self.doc, "reason": self.reason}


class PickCache:
    """JSON file cache keyed by sha256(model + system + prompt)."""

    def __init__(self, path: Path | None):
        self.path = path
        self._lock = threading.Lock()
        self.data: dict = json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else {}

    @staticmethod
    def key(model: str, prompt: str, system: str = SYSTEM) -> str:
        return hashlib.sha256(f"{model}\n{system}\n{prompt}".encode("utf-8")).hexdigest()

    def get(self, key: str) -> Pick | None:
        v = self.data.get(key)
        if not v:
            return None
        doc = None if v["doc"] in (None, NONE) else v["doc"]
        return Pick(**{**v, "doc": doc, "cached": True})

    def put(self, key: str, pick: Pick) -> None:
        with self._lock:
            self.data[key] = {k: v for k, v in asdict(pick).items() if k != "cached"}

    def save(self) -> None:
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.data, indent=1), encoding="utf-8")


def pick_doc(ticket_text: str, candidates: list[str], client: anthropic.Anthropic | None = None,
             cache: PickCache | None = None, cards: list[DocCard] | None = None) -> Pick:
    """Choose one of `candidates` (doc paths) for the ticket; doc=None if none fits."""
    if not candidates:
        return Pick(None, "no candidates", valid=False)
    cfg = base_llm()
    cards = cards or [doc_card(d) for d in candidates]
    prompt = build_prompt(ticket_text, cards)
    system = system_for(len(cards))
    key = PickCache.key(cfg["model"], prompt, system)
    if cache and (hit := cache.get(key)):
        return hit
    client = client or anthropic.Anthropic()
    pick = Pick(None, "invalid model output after retry", valid=False)
    for attempt in (1, 2):
        resp = client.messages.create(
            model=cfg["model"], max_tokens=1024, system=system,
            output_config={"effort": cfg.get("effort", "low")},
            messages=[{"role": "user", "content": prompt + (RETRY_NOTE if attempt == 2 else "")}],
        )
        pick.input_tokens += resp.usage.input_tokens
        pick.output_tokens += resp.usage.output_tokens
        pick.attempts = attempt
        text = "".join(b.text for b in resp.content if b.type == "text")
        parsed = parse_pick(text, candidates) if resp.stop_reason != "refusal" else None
        if parsed:
            pick.doc = None if parsed[0] == NONE else parsed[0]
            pick.reason, pick.valid = parsed[1], True
            break
    if cache:
        cache.put(key, pick)
    return pick


def route_and_pick(conn, ticket_text: str, filters: dict | None = None, client: anthropic.Anthropic | None = None,
                   cache: PickCache | None = None, query_embedding: list[float] | None = None
                   ) -> tuple[RouteResult, Pick]:
    """Routing pipeline: route_doc (top 3 runbooks, no LLM), then pick_doc over them."""
    route = route_doc(conn, ticket_text, filters, query_embedding=query_embedding)
    return route, pick_doc(ticket_text, route.top(3), client=client, cache=cache)
