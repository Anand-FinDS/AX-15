"""Heading-aware KB chunker + metadata tagger (L1).

Rules (kept deliberately simple and explainable):
- Split each markdown doc at H3 headings; an H2 section with no H3 is one chunk.
  Text between an H2 heading and its first H3 is its own chunk. H4+ stays inside.
- Headings inside ``` code fences are ignored (spec sheets have `# comment` lines).
- Because splits only happen at headings, a numbered list is never split.
- Chunks under ~40 words are merged into the next chunk (or the previous one if last).
- FAQ docs: one chunk per **Q:** / A: pair; never merged.
- Each chunk carries a breadcrumb "Doc title > H2 > H3", prepended when embedding.
- Contextual prefix: "<doc title> — <one-line doc summary>" (summary = first sentence of the
  Overview section, else of the first paragraph; FAQs have neither, so title only). Embedded and full-text indexed with the
  chunk, but stored separately, so the original chunk text is what gets displayed.
- version-matrix.csv is structured data (loaded in L0-2), not chunked.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from ax15.settings import dataset_dir

MIN_WORDS = 40
DOC_TYPES = {"runbooks": "runbook", "faq": "faq", "sop": "sop", "system-specs": "system_spec"}

_CATEGORY_RULES = [  # (regex on file stem, category) — first match wins
    (r"^pos-|^foodtech|^orbitpos", "POS"),
    (r"^printers?-|^epson|^star-", "Printers"),
    (r"^network-|^netlink", "Wi-Fi / Network"),
    (r"^online-orders", "Online Orders"),
    (r"^soft-serve|^creamtech|^frostypro", "Soft Serve"),
    (r"^kiosks?-", "Kiosks"),
]
_SECTION_RULES = [  # (regex on H2/H3 heading text, section_type) — first match wins
    (r"immediate", "immediate"),
    (r"escalat", "escalate"),
    (r"diagnos", "diagnosis"),
    (r"resolution|resolve", "resolution"),
]
# L2-only: explicit markers in heading or text, or headings naming admin secrets.
_L2_TEXT_RE = re.compile(r"\bL2[- ]only\b|\bL2 procedure\b|\bL2 approval\b", re.I)
# Rollback is the second half of the L2-only upgrade procedure.
# ("password" is deliberately absent: the guest Wi-Fi password fix is store-facing.)
_L2_HEADING_RE = re.compile(r"credential|registry|rollback procedure", re.I)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_FAQ_Q_RE = re.compile(r"^\*\*Q:", re.M)


@dataclass
class Chunk:
    doc_path: str
    chunk_index: int
    breadcrumb: str
    text: str
    metadata: dict = field(default_factory=dict)
    context_prefix: str = ""

    @property
    def embed_text(self) -> str:
        return embed_input(self.context_prefix, self.breadcrumb, self.text)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.embed_text.encode("utf-8")).hexdigest()

    @property
    def words(self) -> int:
        return len(self.text.split())


@dataclass
class Document:
    doc_path: str            # relative to kb/, e.g. "runbooks/pos-scanner.md" (matches labels)
    doc_type: str
    title: str
    category: str
    content_hash: str
    chunks: list[Chunk]
    summary: str = ""


def embed_input(context_prefix: str, breadcrumb: str, text: str) -> str:
    """The exact text that is embedded (and hashed) for a chunk."""
    return "\n\n".join(x for x in (context_prefix, breadcrumb, text) if x)


_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_LABEL_RE = re.compile(r"\*\*[^*]{1,30}:\*\*\s*")          # "**Purpose:** " style labels
_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MAX_SUMMARY = 240


def _plain(text: str) -> str:
    text = _LINK_RE.sub(r"\1", _LABEL_RE.sub("", text))
    return re.sub(r"\s+", " ", re.sub(r"[*_`>]", "", text)).strip()


def _first_paragraph(lines: list[str]) -> str:
    para: list[str] = []
    in_fence = False
    for line in lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line.strip() or _HEADING_RE.match(line) or re.match(r"^\s*(-{3,}|\|)", line):
            if para:
                break
            continue
        para.append(line)
    return " ".join(para)


def doc_summary(md: str) -> str:
    """One line: first sentence of the Overview section, else of the doc's first paragraph."""
    lines = md.splitlines()
    start = None
    for i, line in enumerate(lines):
        m = _HEADING_RE.match(line)
        if m and len(m.group(1)) == 2 and _clean_heading(m.group(2)).lower().startswith("overview"):
            start = i + 1
            break
    para = _first_paragraph(lines[start:]) if start is not None else ""
    if not para:
        para = _first_paragraph(lines)
    sentence = _SENTENCE_END_RE.split(_plain(para), maxsplit=1)[0]
    if sentence.startswith("Q:"):      # FAQs have no intro paragraph; a question is not a summary
        return ""
    if len(sentence) > _MAX_SUMMARY:
        sentence = sentence[:_MAX_SUMMARY].rsplit(" ", 1)[0] + "…"
    return sentence


@dataclass
class _Part:
    path: list[str]          # heading path below the H1: [H2] or [H2, H3]
    lines: list[str]
    section_type: str = "other"
    is_faq: bool = False

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()


def _clean_heading(h: str) -> str:
    return re.sub(r"[*_`]", "", h).strip()


def doc_category(stem: str, doc_type: str) -> str:
    if doc_type == "sop":
        return "General"
    for pattern, cat in _CATEGORY_RULES:
        if re.search(pattern, stem):
            return cat
    return "General"


def section_type(path: list[str]) -> str:
    for heading in reversed(path):            # most specific heading first
        for pattern, st in _SECTION_RULES:
            if re.search(pattern, heading, re.I):
                return st
    return "other"


def audience(path: list[str], text: str) -> str:
    if any(_L2_HEADING_RE.search(h) or _L2_TEXT_RE.search(h) for h in path) or _L2_TEXT_RE.search(text):
        return "L2"
    return "L1"


def _split_sections(md: str) -> tuple[str, list[_Part]]:
    """Return (H1 title, parts split at H2/H3)."""
    title, parts = "", []
    h2, cur = None, _Part(path=[], lines=[])
    in_fence = False
    for line in md.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
        m = None if in_fence else _HEADING_RE.match(line)
        level = len(m.group(1)) if m else 0
        if level == 1 and not title:
            title = _clean_heading(m.group(2))
            continue
        if level in (2, 3):
            parts.append(cur)
            heading = _clean_heading(m.group(2))
            if level == 2:
                h2 = heading
                cur = _Part(path=[h2], lines=[line])
            else:
                cur = _Part(path=[h2, heading] if h2 else [heading], lines=[line])
            continue
        cur.lines.append(line)
    parts.append(cur)
    return title, [p for p in parts if re.sub(r"[#\s\-*]", "", p.text)]   # drop empty / rule-only parts


def _split_faq(part: _Part) -> list[_Part]:
    """Split an FAQ section into one part per Q&A; text before the first Q stays separate."""
    text = part.text
    starts = [m.start() for m in _FAQ_Q_RE.finditer(text)]
    if not starts:
        return [part]
    out = []
    head = text[: starts[0]].strip()
    # keep the H2 heading line out of Q&A chunks; it is in the breadcrumb
    head_lines = [ln for ln in head.splitlines() if not _HEADING_RE.match(ln)]
    if re.sub(r"[\s\-*]", "", "\n".join(head_lines)):
        out.append(_Part(path=part.path, lines=head.splitlines()))
    for i, s in enumerate(starts):
        qa = text[s: starts[i + 1] if i + 1 < len(starts) else len(text)].strip()
        qa = re.sub(r"\n-{3,}\s*$", "", qa).strip()
        question = _clean_heading(qa.splitlines()[0].replace("Q:", "", 1))
        out.append(_Part(path=part.path + [question], lines=qa.splitlines(), is_faq=True))
    return out


def _continues_list(p: _Part) -> bool:
    """True if the section's body starts at list item n > 1 (the author numbered across headings)."""
    body = "\n".join(ln for ln in p.lines if not _HEADING_RE.match(ln)).lstrip()
    m = re.match(r"(\d+)\.\s", body)
    return bool(m) and int(m.group(1)) > 1


def _merge_small(parts: list[_Part]) -> list[list[_Part]]:
    """Group parts so that every group (except FAQ Q&As and lone docs) has >= MIN_WORDS words.

    A section that continues the previous section's numbered list is always attached
    to it, so a numbered procedure is never split even when headings interrupt it.
    """
    groups: list[list[_Part]] = []
    pending: list[_Part] = []
    for p in parts:
        if not p.is_faq and _continues_list(p) and (pending or (groups and not groups[-1][0].is_faq)):
            (pending or groups[-1]).append(p)
            continue
        if p.is_faq:
            if pending:
                groups.append(pending)
                pending = []
            groups.append([p])
            continue
        pending.append(p)
        if sum(len(x.text.split()) for x in pending) >= MIN_WORDS:
            groups.append(pending)
            pending = []
    if pending:
        if groups and not groups[-1][0].is_faq:
            groups[-1].extend(pending)        # small tail → merge into previous
        else:
            groups.append(pending)
    return groups


def _breadcrumb(title: str, paths: list[list[str]]) -> str:
    """Common heading prefix, then the distinct last-level headings joined with ' / '."""
    common: list[str] = []
    for level in zip(*paths):
        if len(set(level)) == 1:
            common.append(level[0])
        else:
            break
    tails = []
    for p in paths:
        rest = p[len(common):]
        if rest and rest[-1] not in tails:
            tails.append(rest[-1])
    crumbs = [title] + common + ([" / ".join(tails)] if tails else [])
    return " > ".join(c for c in crumbs if c)


def chunk_document(path: Path, kb_root: Path) -> Document:
    rel = path.relative_to(kb_root).as_posix()
    doc_type = DOC_TYPES[rel.split("/", 1)[0]]
    md = path.read_text(encoding="utf-8")
    title, parts = _split_sections(md)
    title = title or path.stem
    if doc_type == "faq":
        parts = [q for p in parts for q in _split_faq(p)]
    category = doc_category(path.stem, doc_type)
    summary = doc_summary(md)
    prefix = f"{title} — {summary}" if summary else title

    chunks = []
    for i, group in enumerate(_merge_small(parts)):
        text = "\n\n".join(p.text for p in group)
        paths = [p.path for p in group]
        types = [section_type(p.path) for p in group]
        meta = {
            "doc_type": doc_type,
            "category": category,
            "section_type": next((t for t in types if t != "other"), "other"),
            "audience": "L2" if any(audience(p.path, p.text) == "L2" for p in group) else "L1",
            "headings": [h for p in paths for h in p[-1:]],
            # parent H2 section(s) of this chunk, for small-to-big expansion (tools.kb_section)
            "h2": list(dict.fromkeys(p[0] for p in paths if p)),
        }
        chunks.append(Chunk(rel, i, _breadcrumb(title, paths), text, meta, prefix))
    return Document(rel, doc_type, title, category, hashlib.sha256(md.encode("utf-8")).hexdigest(), chunks,
                    summary)


def chunk_kb(root: Path | None = None) -> list[Document]:
    kb_root = (root or dataset_dir()) / "kb"
    files = sorted(p for p in kb_root.rglob("*.md") if p.relative_to(kb_root).parts[0] in DOC_TYPES)
    return [chunk_document(p, kb_root) for p in files]
