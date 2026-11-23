"""Canonical Ticket model + intake for JSON tickets and free-text messages (L0-3).

Both paths produce the same `Ticket`, with leakage stripped (no label leakage into prompts).
Free-text intake is deterministic (no LLM): it reads optional "Key: value"
header lines, then falls back to pattern matching for ids, priority and version.
Anything it cannot find is left empty for the agent's triage step to fill.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from ax15.l0_data.leakage import LEAKAGE_KEYS, LeakReport, clean_text, has_leak

Source = Literal["train", "panel_test", "manual", "ui"]

CATEGORIES = ("POS", "Printers", "Wi-Fi / Network", "Soft Serve", "Online Orders", "Kiosks", "General")
_CATEGORY_ALIASES = {
    "pos": "POS", "point of sale": "POS",
    "printer": "Printers", "printers": "Printers",
    "network": "Wi-Fi / Network", "wifi": "Wi-Fi / Network", "wi-fi": "Wi-Fi / Network",
    "wi-fi / network": "Wi-Fi / Network", "wi-fi/network": "Wi-Fi / Network",
    "soft serve": "Soft Serve", "soft-serve": "Soft Serve", "softserve": "Soft Serve",
    "online orders": "Online Orders", "online order": "Online Orders", "online ordering": "Online Orders",
    "kiosk": "Kiosks", "kiosks": "Kiosks", "general": "General",
}
PREFIX_CATEGORY = {"POS": "POS", "PRN": "Printers", "RTR": "Wi-Fi / Network", "SSM": "Soft Serve", "KSK": "Kiosks"}
# Keyword fallback for free text with neither a category header nor an asset id. Order matters.
_CATEGORY_KEYWORDS = [
    ("Soft Serve", r"soft[- ]?serve|ice cream|cone|mix level|freezer"),
    ("Online Orders", r"zomato|swiggy|online order|aggregator|delivery app"),
    ("Kiosks", r"\bkiosk"),
    ("Printers", r"\bprinter|receipt|\bprint(?:ing)?\b"),
    ("Wi-Fi / Network", r"wi-?fi|router|network|internet|vpn"),
    ("POS", r"\bpos\b|till|terminal|cash drawer|card machine|pin pad|payment"),
]

STORE_RE = re.compile(r"\bSW-\d{4}\b")
ASSET_RE = re.compile(r"\b(?:POS|PRN|RTR|SSM|KSK)-\d{4}-[A-Z0-9]+\b")
TICKET_RE = re.compile(r"\bINC-\d{5}\b")
PRIORITY_RE = re.compile(r"\bP([1-4])\b", re.I)


class TicketValidationError(ValueError):
    pass


class HistoryEntry(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)
    seq: int
    ts: datetime | None = None
    actor: str | None = None
    note: str | None = None


class Ticket(BaseModel):
    """Canonical ticket. Contains only fields safe to show an agent."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ticket_id: str = Field(min_length=1)
    store_id: str | None = None
    store_name: str | None = None
    submitted_by: str | None = None
    contact_phone: str | None = None
    date_opened: datetime | None = None
    priority: Literal["P1", "P2", "P3", "P4"] | None = None
    category: str | None = None
    subcategory: str | None = None
    subject: str | None = None
    description: str | None = None
    asset_id: str | None = None
    system_version: str | None = None
    tags: list[str] = Field(default_factory=list)
    escalation_flag: bool = False
    related_tickets: list[str] = Field(default_factory=list)
    resolution_notes: str | None = None
    status: str | None = None
    history: list[HistoryEntry] = Field(default_factory=list)
    source: Source
    batch_id: str = Field(min_length=1)
    input_format: Literal["json", "free_text"] = "json"
    raw_input: str | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _blank_to_none(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("priority", mode="before")
    @classmethod
    def _priority(cls, v):
        return v.strip().upper() if isinstance(v, str) else v

    @field_validator("category", mode="before")
    @classmethod
    def _category(cls, v):
        return normalise_category(v) if isinstance(v, str) else v

    @field_validator("tags", "related_tickets", mode="before")
    @classmethod
    def _none_list(cls, v):
        return [] if v is None else v

    @model_validator(mode="after")
    def _has_content(self):
        if not (self.subject or self.description):
            raise ValueError("ticket has neither subject nor description")
        return self

    def prompt_fields(self) -> dict[str, Any]:
        """Text an agent may see (used by leakage checks)."""
        d = self.model_dump(exclude={"source", "batch_id", "input_format"})
        d["history"] = [h.model_dump() for h in self.history]
        return d


@dataclass
class Normalised:
    ticket: Ticket
    leaks: LeakReport = field(default_factory=LeakReport)
    warnings: list[str] = field(default_factory=list)


def normalise_category(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip()
    return _CATEGORY_ALIASES.get(v.lower(), v)


# --------------------------------------------------------------------------- JSON

_TICKET_KEYS = set(Ticket.model_fields) - {"history", "source", "batch_id", "input_format", "raw_input"}
_TEXT_KEYS = ("store_name", "submitted_by", "subject", "description", "subcategory", "resolution_notes", "status")


def from_json(data: dict[str, Any], *, source: Source, batch_id: str) -> Normalised:
    """Normalise one ITSM-style JSON ticket. Raises TicketValidationError if unusable."""
    if not isinstance(data, dict):
        raise TicketValidationError("ticket JSON must be an object")
    leaks, warnings = LeakReport(), []
    d = dict(data)

    for k in sorted(set(d) & LEAKAGE_KEYS):
        d.pop(k)
        leaks.keys_dropped.append(k)
    history_raw = d.pop("ticket_history", None) or []
    unknown = sorted(set(d) - _TICKET_KEYS)
    for k in unknown:
        d.pop(k)
    if unknown:
        warnings.append(f"ignored unknown fields: {', '.join(unknown)}")

    for k in _TEXT_KEYS:
        if isinstance(d.get(k), str):
            d[k], removed = clean_text(d[k])
            leaks.add_text(k, removed)
    if isinstance(d.get("tags"), list):
        kept = [t for t in d["tags"] if not (isinstance(t, str) and has_leak(t))]
        leaks.sentences_removed.extend(("tags", t) for t in d["tags"] if t not in kept)
        d["tags"] = kept

    if not isinstance(history_raw, list):
        raise TicketValidationError("ticket_history must be a list")
    history = []
    for i, h in enumerate(history_raw):
        if not isinstance(h, dict):
            raise TicketValidationError(f"ticket_history[{i}] must be an object")
        note, removed = clean_text(h.get("note"))
        leaks.add_text(f"ticket_history[{i}].note", removed)
        actor, removed_a = clean_text(h.get("actor"))
        leaks.add_text(f"ticket_history[{i}].actor", removed_a)
        history.append({"seq": i, "ts": h.get("timestamp") or h.get("ts"), "actor": actor, "note": note})

    priority = d.get("priority")
    if isinstance(priority, str) and priority.strip().upper() not in {"P1", "P2", "P3", "P4"}:
        warnings.append(f"unrecognised priority {priority!r}; left empty")
        d["priority"] = None
    if d.get("category") and normalise_category(d["category"]) not in CATEGORIES:
        warnings.append(f"unrecognised category {d['category']!r}; kept as given")

    try:
        ticket = Ticket(**d, history=history, source=source, batch_id=batch_id, input_format="json")
    except ValidationError as e:
        errs = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:5])
        raise TicketValidationError(errs) from None
    return Normalised(ticket, leaks, warnings)


# --------------------------------------------------------------------------- free text

_HEADER_ALIASES = {
    "ticket": "ticket_id", "ticket id": "ticket_id", "ticket_id": "ticket_id", "id": "ticket_id",
    "store": "store_id", "store id": "store_id", "store_id": "store_id",
    "store name": "store_name", "outlet": "store_name", "location": "store_name",
    "from": "submitted_by", "name": "submitted_by", "submitted by": "submitted_by", "reported by": "submitted_by",
    "phone": "contact_phone", "contact": "contact_phone", "contact phone": "contact_phone",
    "date": "date_opened", "opened": "date_opened", "date opened": "date_opened",
    "priority": "priority", "category": "category", "subcategory": "subcategory",
    "subject": "subject", "title": "subject", "summary": "subject",
    "asset": "asset_id", "asset id": "asset_id", "asset_id": "asset_id",
    "version": "system_version", "system version": "system_version", "system": "system_version",
    "status": "status",
}
_HEADER_RE = re.compile(r"^\s*([A-Za-z][A-Za-z _]{0,20}?)\s*:\s*(.+?)\s*$")


def _parse_headers(text: str) -> tuple[dict[str, str], str]:
    """Leading 'Key: value' lines with known keys → (fields, remaining body)."""
    fields: dict[str, str] = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            if fields:      # blank line after headers ends the header block
                break
            continue
        m = _HEADER_RE.match(line)
        key = _HEADER_ALIASES.get(m.group(1).strip().lower()) if m else None
        if not key:
            break
        fields.setdefault(key, m.group(2))
        i += 1
    return fields, "\n".join(lines[i:]).strip()


def _find_version(text: str, known_versions: list[str]) -> str | None:
    hits = [v for v in known_versions if re.search(rf"(?<![\w.]){re.escape(v)}(?![\w.])", text, re.I)]
    return max(hits, key=len) if hits else None


def _guess_category(text: str, asset_id: str | None) -> str | None:
    if asset_id:
        return PREFIX_CATEGORY.get(asset_id.split("-", 1)[0])
    for cat, pattern in _CATEGORY_KEYWORDS:
        if re.search(pattern, text, re.I):
            return cat
    return None


def from_text(
    text: str,
    *,
    source: Source,
    batch_id: str,
    known_versions: list[str] | None = None,
    received_at: datetime | None = None,
) -> Normalised:
    """Normalise a free-text store message (e.g. a pasted email / chat message)."""
    if not text or not text.strip():
        raise TicketValidationError("empty message")
    leaks, warnings = LeakReport(), []
    cleaned, removed = clean_text(text.strip())
    leaks.add_text("raw_input", removed)
    if not cleaned:
        raise TicketValidationError("message is empty after leakage removal")

    hdr, body = _parse_headers(cleaned)
    scan = cleaned

    ticket_id = hdr.get("ticket_id")
    if not ticket_id:
        ticket_id = "TXT-" + hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:10].upper()
        warnings.append(f"no ticket id in message; assigned {ticket_id}")

    store_id = hdr.get("store_id")
    if not store_id and (m := STORE_RE.search(scan)):
        store_id = m.group(0)
    asset_id = hdr.get("asset_id")
    if not asset_id and (m := ASSET_RE.search(scan)):
        asset_id = m.group(0)

    priority = hdr.get("priority")
    if priority and not re.fullmatch(r"P[1-4]", priority.strip(), re.I):
        m = PRIORITY_RE.search(priority)
        priority = f"P{m.group(1)}" if m else None
        if not m:
            warnings.append(f"unrecognised priority {hdr['priority']!r}; left empty")
    if not priority and (m := PRIORITY_RE.search(scan)):
        priority = f"P{m.group(1)}"

    category = normalise_category(hdr.get("category")) or _guess_category(scan, asset_id)
    if category is None:
        warnings.append("category not determined; left for triage")

    system_version = hdr.get("system_version") or _find_version(scan, known_versions or [])

    date_opened: datetime | None = None
    if hdr.get("date_opened"):
        try:
            date_opened = datetime.fromisoformat(hdr["date_opened"])
        except ValueError:
            warnings.append(f"unparseable date {hdr['date_opened']!r}; used received time")
    date_opened = date_opened or received_at or datetime.now(timezone.utc)

    description = body or None
    subject = hdr.get("subject")
    if not subject and description:
        first = next((ln.strip() for ln in description.splitlines() if ln.strip()), "")
        subject = first if len(first) <= 120 else first[:117].rstrip() + "..."

    related = sorted({t for t in TICKET_RE.findall(scan) if t != ticket_id})

    try:
        ticket = Ticket(
            ticket_id=ticket_id, store_id=store_id, store_name=hdr.get("store_name"),
            submitted_by=hdr.get("submitted_by"), contact_phone=hdr.get("contact_phone"),
            date_opened=date_opened, priority=priority, category=category,
            subcategory=hdr.get("subcategory"), subject=subject, description=description,
            asset_id=asset_id, system_version=system_version, related_tickets=related,
            status=hdr.get("status") or "Open", source=source, batch_id=batch_id,
            input_format="free_text", raw_input=cleaned,
        )
    except ValidationError as e:
        errs = "; ".join(f"{'.'.join(map(str, x['loc']))}: {x['msg']}" for x in e.errors()[:5])
        raise TicketValidationError(errs) from None
    return Normalised(ticket, leaks, warnings)
