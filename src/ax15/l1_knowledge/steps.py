"""Runbook sections as numbered step items ("select, don't write").

The 2.0 resolver sees a runbook's step-bearing sections as items with ids (S1, S2, ...) and its
"When to Escalate" criteria as items (E1, E2, ...). It returns ids only; the answer's steps are the
items' text rendered verbatim from kb_chunks.text (contiguous original lines, original numbering), so
every step passes the grounding check by construction. L2-only chunks never become items (store
audience): they appear as one-line stubs.

Item boundaries inside a chunk:
- if the chunk has bold paragraph lines ("**Step 1: Verify Device Recognition**"), each bold line starts
  an item that runs to the next bold line (its numbered sub-steps stay inside it);
- otherwise each top-level list line ("1. ...", "- ...") starts an item, with its indented children.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

import psycopg

STEP_SECTION_TYPES = ("immediate", "diagnosis", "resolution")
MAX_ITEMS = 80

_LIST = re.compile(r"^(\d+[.)]|[-*•])\s+")
_ANY_LIST = re.compile(r"^\s*(\d+[.)]|[-*•])\s+")
_BOLD_PARA = re.compile(r"^\*\*[^*].*\*\*:?\s*$")
_HEADING = re.compile(r"^#{1,6}\s")
_RULE = re.compile(r"^\s*(-{3,}|\*{3,})\s*$")
_COLLECT = re.compile(r"information|collect|before escalating|contact", re.I)


@dataclass
class StepItem:
    id: str
    chunk_id: int
    breadcrumb: str
    text: str                  # verbatim lines from kb_chunks.text
    title: str                 # first line without list marker / bold, for display and the prompt
    section_type: str = ""     # immediate / diagnosis / resolution / escalate

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class DocSteps:
    doc: str
    sections: list[str] = field(default_factory=list)            # breadcrumbs in order
    items: list[StepItem] = field(default_factory=list)
    criteria: list[StepItem] = field(default_factory=list)       # When to Escalate items (E ids)
    l2_stubs: list[str] = field(default_factory=list)
    truncated: bool = False

    def item(self, iid: str) -> StepItem | None:
        return next((i for i in self.items + self.criteria if i.id == iid), None)

    def as_dict(self) -> dict:
        return {"doc": self.doc, "sections": self.sections, "items": [i.as_dict() for i in self.items],
                "criteria": [i.as_dict() for i in self.criteria], "l2_stubs": self.l2_stubs,
                "truncated": self.truncated}


def _title(line: str) -> str:
    t = _LIST.sub("", line.strip())
    return re.sub(r"\*\*", "", t).strip().rstrip(":")


def _body_lines(text: str) -> list[str]:
    return [ln for ln in text.splitlines() if not _HEADING.match(ln) and not _RULE.match(ln)]


def split_items(text: str) -> list[str]:
    """Chunk text -> verbatim item texts (see module doc). Each item is a run of contiguous original
    lines (headings and horizontal rules end an item and are never part of one)."""
    lines = text.splitlines()
    bold_mode = any(_BOLD_PARA.match(ln) for ln in lines)
    starts_item = _BOLD_PARA.match if bold_mode else _LIST.match
    out: list[str] = []
    cur: list[str] = []

    def close():
        while cur and not cur[-1].strip():
            cur.pop()
        if cur:
            out.append("\n".join(cur))
        cur.clear()

    for ln in lines:
        if _HEADING.match(ln) or _RULE.match(ln):
            close()
            continue
        if starts_item(ln) and cur:
            close()
        if not cur and not ln.strip():
            continue
        cur.append(ln)
    close()
    return out


def split_criteria(text: str) -> list[str]:
    """'When to Escalate' chunk -> one item per leaf bullet, stopping at 'Information to Collect'."""
    lines = _body_lines(text)
    out = []
    for i, ln in enumerate(lines):
        if _BOLD_PARA.match(ln) and _COLLECT.search(ln) and out:
            break
        if not _ANY_LIST.match(ln):
            continue
        indent = len(ln) - len(ln.lstrip())
        nxt = next((x for x in lines[i + 1:] if x.strip()), "")
        if _ANY_LIST.match(nxt) and len(nxt) - len(nxt.lstrip()) > indent:
            continue                                # a group header ("1. **USB Printer Issues:**")
        out.append(ln)
    return out


def doc_steps(conn: psycopg.Connection, doc: str, audience: str = "store", prefer_h2: str | None = None) -> DocSteps:
    """Step items (S ids) from a doc's immediate / diagnosis / resolution sections and criteria (E ids)
    from its escalation section. prefer_h2 keeps that H2 group first when the item cap applies."""
    rows = conn.execute(
        "SELECT chunk_id, breadcrumb, text, metadata FROM kb_chunks WHERE doc_path = %s AND status = 'approved'"
        " ORDER BY chunk_index", (doc,)).fetchall()
    ds = DocSteps(doc)
    step_rows, esc_rows = [], []
    for cid, crumb, text, meta in rows:
        if audience == "store" and meta.get("audience") == "L2":
            ds.l2_stubs.append(f"L2-only procedure exists: {crumb} — must be performed by L2.")
            continue
        st = meta.get("section_type")
        if st in STEP_SECTION_TYPES:
            step_rows.append((cid, crumb, text, meta))
        elif st == "escalate":
            esc_rows.append((cid, crumb, text))
    if prefer_h2:
        step_rows.sort(key=lambda r: (prefer_h2 not in (r[3].get("h2") or []), 0))
    n = 0
    for cid, crumb, text, _meta in step_rows:
        for t in split_items(text):
            if n >= MAX_ITEMS:
                ds.truncated = True
                break
            n += 1
            ds.items.append(StepItem(f"S{n}", cid, crumb, t, _title(t.splitlines()[0]),
                                     _meta.get("section_type") or ""))
            if crumb not in ds.sections:
                ds.sections.append(crumb)
    m = 0
    for cid, crumb, text in esc_rows:
        for t in split_criteria(text):
            m += 1
            ds.criteria.append(StepItem(f"E{m}", cid, crumb, t.strip(), _title(t)))
    return ds


def render_for_prompt(ds: DocSteps) -> str:
    """Items with ids, grouped by breadcrumb, for the resolver prompt."""
    L, last = [f'<runbook doc="{ds.doc}">'], None
    for it in ds.items:
        if it.breadcrumb != last:
            L.append(f"\n## {it.breadcrumb}")
            last = it.breadcrumb
        L.append(f"[{it.id}] {it.text}")
    if ds.l2_stubs:
        L.append("\n## L2-only (not shown to stores)")
        L += [f"- {s}" for s in ds.l2_stubs]
    L.append("</runbook>\n\n<when_to_escalate>")
    L += [f"[{c.id}] {c.title}" for c in ds.criteria]
    L.append("</when_to_escalate>")
    return "\n".join(L)
