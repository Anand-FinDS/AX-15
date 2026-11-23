"""Pydantic input/output models for the L2 tools.

Every output carries `status` ("ok" | "not_found" | "mismatch") instead of raising, so the
agent can reason about missing or inconsistent records.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Status = Literal["ok", "not_found", "mismatch"]
Audience = Literal["store", "agent"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Out(BaseModel):
    status: Status
    message: str | None = None


# --- get_ticket -----------------------------------------------------------------------------

class GetTicketInput(_In):
    ticket_id: str = Field(description="Business ticket id, e.g. INC-00162")
    batch_id: str | None = Field(None, description="Batch to read from; default = most recently loaded")


class HistoryNote(BaseModel):
    seq: int
    ts: datetime | None
    actor: str | None
    note: str | None


class TicketOut(_Out):
    """Prompt-safe ticket fields only: never labels, chaos_type or eval.*"""
    ticket_id: str
    batch_id: str | None = None
    source: str | None = None
    store_id: str | None = None
    store_name: str | None = None
    submitted_by: str | None = None
    date_opened: datetime | None = None
    priority: str | None = None
    category: str | None = None
    subcategory: str | None = None
    subject: str | None = None
    description: str | None = None
    asset_id: str | None = None
    system_version: str | None = None
    tags: list[str] = []
    escalation_flag: bool | None = None
    related_tickets: list[str] = []
    ticket_status: str | None = None
    resolution_notes: str | None = None
    history: list[HistoryNote] = []


# --- get_store / list_store_assets ------------------------------------------------------------

class GetStoreInput(_In):
    store_id: str = Field(description="Store id, e.g. SW-0111")


class StoreOut(_Out):
    store_id: str
    store_name: str | None = None
    city: str | None = None
    neighborhood: str | None = None
    store_type: str | None = None
    region_manager: str | None = None
    support_phone: str | None = None


class ListStoreAssetsInput(_In):
    store_id: str = Field(description="Store id, e.g. SW-0111")


class AssetSummary(BaseModel):
    asset_id: str
    asset_type: str
    system: str | None
    model: str | None
    version: str | None


class StoreAssetsOut(_Out):
    store_id: str
    assets: list[AssetSummary] = []


# --- get_asset ------------------------------------------------------------------------------

class GetAssetInput(_In):
    asset_id: str = Field(description="CMDB asset id, e.g. POS-0111-T1")
    claimed_system: str | None = Field(
        None, description="System/version the ticket names, e.g. 'FoodTech POS v4.2.1'; compared with the CMDB")


class AssetOut(_Out):
    asset_id: str
    store_id: str | None = None
    asset_type: str | None = None
    vendor: str | None = None
    model: str | None = None
    system: str | None = None
    version: str | None = None
    install_date: date | None = None
    warranty_expiry: date | None = None
    warranty_expired: bool | None = None
    version_status: Literal["supported", "known_issues", "not_in_matrix"] | None = None
    known_bugs: str | None = None
    patch_available: str | None = None
    version_notes: str | None = None
    claimed_system: str | None = None
    mismatch: bool = False
    mismatch_kind: Literal["system", "version"] | None = None


# --- get_recent_tickets ---------------------------------------------------------------------

class GetRecentTicketsInput(_In):
    store_id: str
    asset_id: str | None = None
    days: int = Field(30, ge=1, le=365)
    as_of: datetime | None = Field(None, description="Window end; default now. Use the ticket's date_opened.")
    exclude_ticket_id: str | None = Field(None, description="Leave the current ticket out of the list")


class RecentTicket(BaseModel):
    ticket_id: str
    date_opened: datetime | None
    category: str | None
    subcategory: str | None
    subject: str | None
    ticket_status: str | None
    asset_id: str | None
    resolution_notes: str | None
    source: Literal["history"] = "history"     # evidence from past tickets, not verified truth


class RecentTicketsOut(_Out):
    store_id: str
    asset_id: str | None = None
    window_start: datetime | None = None
    window_end: datetime | None = None
    tickets: list[RecentTicket] = []
    recurrence_count: int = 0                  # tickets in the window (same asset if given)


# --- get_sla --------------------------------------------------------------------------------

class GetSlaInput(_In):
    priority: str = Field(description="P1..P4")
    category: str
    created_at: datetime
    now: datetime | None = None


class SlaOut(_Out):
    priority: str
    category: str
    first_response_minutes: int | None = None
    resolution_target_minutes: int | None = None
    escalation_to_l2_minutes: int | None = None
    response_due: datetime | None = None
    resolution_due: datetime | None = None
    l2_escalation_due: datetime | None = None
    minutes_left_response: int | None = None
    minutes_left_resolution: int | None = None
    response_breached: bool | None = None
    breached: bool | None = None               # resolution target passed


# --- kb_route / kb_section ------------------------------------------------------------------

class KbRouteInput(_In):
    ticket_text: str = Field(description="Ticket subject + description only")
    audience: Audience = "store"


class RouteCandidate(BaseModel):
    doc: str
    score: float
    top_chunk_breadcrumbs: list[str]


class KbRouteOut(_Out):
    candidates: list[RouteCandidate] = []
    doc: str | None = None                     # chosen runbook, or None = no candidate fits (escalate)
    reason: str | None = None
    usage: dict | None = None                  # picker LLM usage, for tracing/cost (not shown to the model)


class KbSectionInput(_In):
    doc: str = Field(description="KB doc path, e.g. runbooks/pos-printer-integration.md")
    query: str
    audience: Audience = "store"


class SectionChunk(BaseModel):
    chunk_id: int
    breadcrumb: str
    text: str | None = None                    # verbatim kb_chunks.text; None when withheld
    l2_stub: str | None = None                 # set instead of text for L2-only chunks (store audience)


class SectionBlock(BaseModel):
    h2: str | None
    chunks: list[SectionChunk]
    text: str                                  # chunk texts (or stubs) joined, original numbering


class KbSectionOut(_Out):
    doc: str
    matched_chunk_id: int | None = None
    matched_breadcrumb: str | None = None
    section: SectionBlock | None = None
    escalation: SectionBlock | None = None     # the doc's "When to Escalate" section
    citations: list[str] = []


# --- get_past_actions (episodic memory: human-verified action_log rows) ------------------------

class GetPastActionsInput(_In):
    store_id: str
    asset_id: str | None = None
    days: int = Field(90, ge=1, le=365)


class PastAction(BaseModel):
    date: datetime
    ticket_id: str
    action_type: str
    runbook: str | None = None                 # payload kb_doc
    routing: str | None = None                 # payload routing
    verdict: Literal["approved", "rejected", "executed"]
    verified_by: str | None = None
    asset_id: str | None = None


class PastActionsOut(_Out):
    store_id: str
    asset_id: str | None = None
    actions: list[PastAction] = []             # human-verified only; "proposed" rows are never returned


# --- log_action -----------------------------------------------------------------------------

class LogActionInput(_In):
    ticket_id: str
    action: Literal["update", "escalate", "notify", "work_note", "cmdb_correction"]
    payload: dict[str, Any] = {}
    actor: str = Field(description="Agent node name or human user id")
    verified_by_human: bool = False
    trace_id: str | None = None
    batch_id: str | None = None


class LogActionOut(_Out):
    ticket_id: str
    action_id: int | None = None
    action_type: str | None = None
    action_status: str | None = None
    trace_id: str | None = None
