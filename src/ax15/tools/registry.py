"""Tool registry: name, one-line description and JSON schema per tool.

Used by the 1.0 agent for model tool calling (schemas() is in Anthropic tool format) and
by MCP later. call() validates arguments with the tool's Pydantic input model and returns
the output model as JSON-ready data.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from ax15.tools import functions as f
from ax15.tools import models as m


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    fn: Callable[..., BaseModel]
    writes: bool = False

    def input_schema(self) -> dict:
        return self.input_model.model_json_schema()


TOOLS: dict[str, ToolSpec] = {t.name: t for t in [
    ToolSpec("get_ticket", "Get a ticket's prompt-safe fields and history notes by ticket id.",
             m.GetTicketInput, m.TicketOut, f.get_ticket),
    ToolSpec("get_store", "Get store details (name, city, type, region manager); status not_found if unknown.",
             m.GetStoreInput, m.StoreOut, f.get_store),
    ToolSpec("get_asset", "Get a CMDB asset with version-matrix status; pass claimed_system to detect a mismatch.",
             m.GetAssetInput, m.AssetOut, f.get_asset),
    ToolSpec("list_store_assets", "List every asset registered at a store (id, type, system, model, version).",
             m.ListStoreAssetsInput, m.StoreAssetsOut, f.list_store_assets),
    ToolSpec("get_recent_tickets", "Recent tickets at a store (optionally one asset) and a recurrence count; "
             "resolution notes are history, not verified truth.",
             m.GetRecentTicketsInput, m.RecentTicketsOut, f.get_recent_tickets),
    ToolSpec("get_sla", "SLA due times, minutes left and breached flags for a priority, category and open time.",
             m.GetSlaInput, m.SlaOut, f.get_sla),
    ToolSpec("kb_route", "Pick the best runbook for a ticket: top-3 candidates and the chosen doc, or null "
             "if none fits (escalate).", m.KbRouteInput, m.KbRouteOut, f.kb_route),
    ToolSpec("kb_section", "Get the relevant section of one KB doc verbatim, plus its When to Escalate section "
             "and citations; L2-only steps come back as a stub.", m.KbSectionInput, m.KbSectionOut, f.kb_section),
    ToolSpec("get_past_actions", "Human-verified past actions (approved / rejected / executed) at a store, "
             "optionally one asset; proposed rows are never returned.", m.GetPastActionsInput, m.PastActionsOut,
             f.get_past_actions),
    ToolSpec("log_action", "Record an ITSM action (update, escalate, notify, work_note) in the action log. "
             "The only write.", m.LogActionInput, m.LogActionOut, f.log_action, writes=True),
]}


def schemas() -> list[dict[str, Any]]:
    """Tool definitions in Anthropic tool-use format: name, description, input_schema."""
    return [{"name": t.name, "description": t.description, "input_schema": t.input_schema()} for t in TOOLS.values()]


def call(name: str, args: dict[str, Any], **kwargs) -> dict[str, Any]:
    """Validate args with the tool's input model, run it, return JSON-ready output."""
    spec = TOOLS[name]
    inp = spec.input_model.model_validate(args)
    out = spec.fn(**inp.model_dump(), **kwargs)
    return spec.output_model.model_validate(out).model_dump(mode="json")
