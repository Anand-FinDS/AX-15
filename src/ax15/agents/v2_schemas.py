"""2.0 schemas: the three LLM node outputs (facts / ids / text only) and the final answer.

V2Answer is a superset of 1.0's V1Answer (same fields, same validation) so the eval compares like for
like; it adds fired_rules, priority_rules, node_path, routing_query, model_used and a richer handoff.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ax15.agents.v1_single import L2Handoff, V1Answer

DeviceType = Literal["pos_terminal", "receipt_printer", "network_router", "kiosk", "soft_serve_machine",
                     "payment_terminal", "online_ordering", "other", "unknown"]

# classify device type -> CMDB asset_type
ASSET_TYPE = {"pos_terminal": "POS Terminal", "payment_terminal": "POS Terminal", "receipt_printer": "Receipt Printer",
              "network_router": "Network Router", "kiosk": "Self-Order Kiosk", "soft_serve_machine": "Soft Serve Machine"}


class Claim(BaseModel):
    claim: str
    ticket_ref: str | None


class Intent(BaseModel):
    kind: Literal["it", "non_it"]
    summary: str


class ClassifyOut(BaseModel):
    """Facts only (prompts/v2_classify.md). Every field is required: no silent defaults.
    intents: what the requester asks for, judged from the description text (not category / asset fields);
    all other facts describe the IT intent(s) only."""
    intents: list[Intent]
    clarity: Literal["clear", "vague"]
    clarifying_questions: list[str]
    clean_symptom: str
    affected_device_type: DeviceType
    payment_failure: bool
    payments_unavailable: bool
    store_wide_outage: bool
    pos_outage_all: bool
    partial_outage: bool
    multiple_locations: bool
    devices_affected: int | None
    device_unusable: bool
    function_degraded: bool
    cosmetic_only: bool
    customer_facing: bool
    data_loss: bool
    security_incident: bool
    safety_hazard: bool
    power_instability: bool
    network_infrastructure_down: bool
    integration_failure: bool
    waited_over_30_min: bool
    compliance_impact: bool
    customer_requested_escalation: bool
    expresses_dissatisfaction: bool
    dne_how_to: bool
    dne_user_error: bool
    dne_billing: bool
    dne_maintenance: bool
    dne_feature_request: bool
    dne_third_party_only: bool
    dne_resolved_after_reset: bool
    error_codes: list[str]
    systems_mentioned: list[str]
    steps_already_tried: list[str]
    claims_to_verify: list[Claim]
    matches_known_bug: bool
    requester_tone: Literal["calm", "concerned", "frustrated", "angry", "urgent"]


class ResolveOut(BaseModel):
    """Ids only (prompts/v2_resolve.md); code renders the step text."""
    section: str
    step_ids: list[str]
    skipped_already_tried: list[str]
    escalation_criteria_met: list[str]
    needs_l2_only: bool
    reason: str


class ComposeHandoff(BaseModel):
    summary: str
    evidence: list[str]
    requested_action: str


class ComposeOut(BaseModel):
    """Text only (prompts/v2_compose.md): no steps."""
    reply_to_store: str
    escalation_reason: str | None
    l2_handoff: ComposeHandoff | None


class V2Handoff(L2Handoff):
    """SOP Step 3 mandatory fields + Step 5 documentation, filled by code; summary text by compose."""
    asset_id: str | None = None
    error_codes: list[str] = []
    store_impact: str | None = None
    priority: str | None = None
    store: str | None = None
    sla_first_response_due: str | None = None
    missing_fields: list[str] = []
    notify: list[str] = []


class FiredRule(BaseModel):
    id: str
    reason: str
    source: str
    applied: bool = True


class V2Answer(V1Answer):
    l2_handoff: V2Handoff | None = None
    fired_rules: list[FiredRule] = []
    priority_rules: list[FiredRule] = []
    node_path: list[str] = []
    routing_query: Literal["raw", "symptom", "both"] | None = None
    model_used: list[str] = Field(default_factory=list, description="models that answered, per LLM node")
    step_ids: list[str] = []
    interim_steps: list[str] = Field(default_factory=list, description="escalations: runbook first-response items "
                                     "shown verbatim 'while L2 picks this up' (max 3)")
    verify_rules_routing: str | None = Field(None, description="verify mode: routing class the remaining SOP rules "
                                             "give (information only; no escalation or action)")
    device_candidates: list[dict] = []
    non_it_redirects: list[str] = []
