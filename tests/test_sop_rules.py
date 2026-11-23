"""SOP rules (config/sop_rules.yaml + trust/sop_rules.py): one test per rule, priority maths, precedence."""
from __future__ import annotations

import pytest

from ax15.trust import sop_rules as sr

BASE = {"is_it": True, "ticket_priority": "P3", "ticket_status": "Open", "opened_hour_ist": 9.0,
        "store_type": "franchise"}


def ev(**facts):
    return sr.evaluate({**BASE, **facts})


def ids(res, applied_only=False):
    return [f["id"] for f in res["fired_rules"] if f["applied"] or not applied_only]


# rule id -> (facts that fire it, expected decision)
RULE_CASES = {
    "AX-VERIFY": ({"ticket_closed": True}, "verify"),
    "AX-NON-IT": ({"is_it": False}, "non_it"),
    "AX-SAFETY": ({"safety_hazard": True}, "escalate"),
    "AX-POWER": ({"power_instability": True}, "escalate"),
    "SOP-DNE-1": ({"dne_how_to": True}, "l1"),
    "SOP-DNE-2": ({"dne_user_error": True}, "l1"),
    "SOP-DNE-3": ({"dne_billing": True}, "non_it"),
    "SOP-DNE-4": ({"dne_maintenance": True}, "l1"),
    "SOP-DNE-5": ({"dne_feature_request": True}, "l1"),
    "SOP-DNE-6": ({"dne_third_party_only": True}, "l1"),
    "SOP-DNE-7": ({"dne_resolved_after_reset": True}, "l1"),
    "SOP-TRIG-PAYMENT": ({"payment_failure": True}, "escalate"),
    "SOP-TRIG-POS-OUTAGE": ({"pos_outage_all": True}, "escalate"),
    "SOP-TRIG-DATA": ({"data_loss": True}, "escalate"),
    "SOP-TRIG-SECURITY": ({"security_incident": True}, "escalate"),
    "SOP-TRIG-NETWORK": ({"network_infrastructure_down": True}, "escalate"),
    "SOP-TRIG-INTEGRATION": ({"integration_failure": True}, "escalate"),
    "SOP-TRIG-ERROR-CODE": ({"unknown_error_codes": ["0x00000050"]}, "escalate"),
    "SOP-TRIG-WAITED": ({"waited_over_30_min": True}, "escalate"),
    "SOP-TRIG-COMPLIANCE": ({"compliance_impact": True}, "escalate"),
    "SOP-TRIG-REQUESTED": ({"customer_requested_escalation": True}, "escalate"),
    "SOP-STEP2-EXHAUSTED": ({"all_steps_tried": True}, "escalate"),
    "RB-WHEN-TO-ESCALATE": ({"runbook_escalation_met": True, "runbook_escalation_text": "E2"}, "escalate"),
    "AX-KB-GAP-SYSTEM": ({"unknown_system": True, "unknown_systems": ["KitchenBridge"]}, "escalate"),
    "AX-KB-GAP-NO-DOC": ({"kb_no_doc": True}, "escalate"),
    "AX-L2-ONLY": ({"l2_only_needed": True, "l2_only": "Runbook > L2 Resolution"}, "escalate"),
    "AX-LLM-FAILED": ({"llm_failed": "classify"}, "escalate"),
    "AX-ASSET-UNVERIFIED": ({"asset_unverified": True, "asset_id": "POS-9072-T5"}, "clarify"),
    "AX-SYSTEM-MISMATCH": ({"system_mismatch": True, "claimed_system": "OrbitPOS v2.1",
                                  "cmdb_system": "FoodTech POS v4.2.1"}, "clarify"),
    "AX-VAGUE": ({"clarity_vague": True}, "clarify"),
}


def test_every_yaml_rule_has_a_case():
    assert {r["id"] for r in sr.load_rules()["rules"] if r.get("active", True)} == set(RULE_CASES)


@pytest.mark.parametrize("rule_id", sorted(RULE_CASES))
def test_rule_fires_and_decides(rule_id):
    facts, decision = RULE_CASES[rule_id]
    res = ev(**facts)
    assert rule_id in ids(res, applied_only=True)
    assert res["decision"] == decision
    fired = next(f for f in res["fired_rules"] if f["id"] == rule_id)
    assert fired["source"] and fired["reason"] and "?" not in fired["reason"]


def test_every_rule_cites_a_source():
    cfg = sr.load_rules()
    for r in cfg["rules"] + cfg["priority_base"] + cfg["priority_overrides"]:
        assert r["source"]["doc"] and r["source"]["section"], r["id"]


def test_nothing_fired_is_l1():
    res = ev()
    assert res["decision"] == "l1" and res["fired_rules"] == [] and res["mode"] == "handle"


def test_routing_hints_and_flags():
    assert ev(dne_how_to=True)["routing_hint"] == "l1_self_service"
    assert ev(dne_billing=True)["redirect"] == "Billing@servewell.in"
    r = ev(unknown_system=True)
    assert r["flags"] == ["kb_gap"] and r["no_steps"]


# --- precedence --------------------------------------------------------------------------------

def test_verify_beats_everything_and_keeps_superseded_rules():
    r = ev(ticket_closed=True, payment_failure=True, clarity_vague=True)
    assert r["decision"] == "verify" and r["mode"] == "verify"
    by = {f["id"]: f["applied"] for f in r["fired_rules"]}
    assert by == {"AX-VERIFY": True, "SOP-TRIG-PAYMENT": False, "AX-VAGUE": False}


def test_escalate_beats_clarify():
    r = ev(asset_unverified=True, payment_failure=True)
    assert r["decision"] == "escalate" and r["no_steps"]          # still no steps on an unverified asset


def test_do_not_escalate_checked_before_triggers():
    assert ev(dne_how_to=True, expresses_dissatisfaction=True)["decision"] == "l1"


def test_non_it_beats_do_not_escalate():
    assert ev(is_it=False, dne_how_to=True)["decision"] == "non_it"


# --- priority ----------------------------------------------------------------------------------

def test_peak_hour_bump():
    assert ev(opened_hour_ist=12.4)["priority"] == "P2"            # P3 -> P2 at lunch
    assert ev(opened_hour_ist=19.0)["priority"] == "P2"            # dinner
    assert ev(opened_hour_ist=10.99)["priority"] == "P3"
    assert ev(opened_hour_ist=14.0)["priority"] == "P3"            # window end is exclusive
    assert ev(opened_hour_ist=21.0)["priority"] == "P3"
    assert "SOP-PRI-PEAK" in [p["id"] for p in ev(opened_hour_ist=11.0)["priority_rules"]]


def test_p1_cap():
    r = ev(ticket_priority="P1", opened_hour_ist=12.0)
    assert r["priority"] == "P1"
    r = ev(store_wide_outage=True, opened_hour_ist=12.0, store_type="corporate", recurrence_count=3)
    assert r["base_priority"] == "P1" and r["priority"] == "P1"


def test_increase_overrides_raise_one_level_at_most():
    r = ev(ticket_priority="P4", opened_hour_ist=12.0, store_type="corporate", recurrence_count=3)
    assert r["priority"] == "P3"                                   # P4 -> P3 (peak); corporate / recurrence no change
    assert [p["id"] for p in r["priority_rules"]] == ["AX-PRI-KEEP", "SOP-PRI-PEAK", "SOP-PRI-RECUR", "SOP-PRI-VIP"]
    cfg = {**sr.load_rules(), "increases_stack": True}             # literal reading still available
    assert sr.evaluate({**BASE, "ticket_priority": "P4", "opened_hour_ist": 12.0, "store_type": "corporate"},
                       cfg)["priority"] == "P2"


def test_base_priority_from_impact_replaces_ticket_priority():
    assert ev(ticket_priority="P1", cosmetic_only=True)["priority"] == "P4"
    assert ev(ticket_priority="P4", payments_unavailable=True)["priority"] == "P1"
    assert ev(ticket_priority="P4", devices_affected=3)["priority"] == "P2"
    assert ev(ticket_priority="P1", device_unusable=True)["priority"] == "P3"
    assert ev(ticket_priority="P2")["base_priority"] == "P2"       # no impact signal: keep


def test_recurrence_threshold_more_than_twice():
    assert ev(recurrence_count=1)["priority"] == "P3"
    assert ev(recurrence_count=2)["priority"] == "P2"


def test_customer_escalation_at_least_p2_and_known_issue_floor():
    assert ev(ticket_priority="P4", customer_requested_escalation=True)["priority"] == "P2"
    assert ev(ticket_priority="P1", customer_requested_escalation=True)["priority"] == "P1"
    assert ev(cosmetic_only=True, known_issue_workaround=True)["priority"] == "P4"   # min P4
    assert ev(device_unusable=True, known_issue_workaround=True)["priority"] == "P4"


def test_shift_bounds():
    assert sr.shift("P1", "+1") == "P1" and sr.shift("P4", "-1") == "P4" and sr.shift("P3", "+1") == "P2"


def test_condition_language():
    f = {"a": True, "n": 2, "l": ["x"], "s": "corporate", "h": 12.5}
    assert sr.check({"all": [{"fact": "a"}, {"fact": "n", "op": ">=", "value": 2}]}, f)
    assert sr.check({"any": [{"fact": "missing"}, {"fact": "l"}]}, f)
    assert not sr.check({"not": {"fact": "a"}}, f)
    assert sr.check({"fact": "s", "op": "in", "value": ["corporate"]}, f)
    assert sr.check({"fact": "h", "op": "hour_in", "value": [[12, 13]]}, f)
    assert not sr.check({"fact": "missing", "op": "hour_in", "value": [[0, 24]]}, f)


def test_render_table_lists_every_rule():
    md = sr.render_table()
    for r in sr.load_rules()["rules"]:
        assert r["id"] in md
    assert "SOP-PRI-PEAK" in md and "escalation-procedure.md" in md


def test_verify_mode_keeps_the_routing_class_of_the_remaining_rules():
    r = ev(ticket_closed=True, integration_failure=True)
    assert r["decision"] == "verify" and r["verify_decision"] == "escalate" and r["mode"] == "verify"
    assert [f["id"] for f in r["fired_rules"] if f["applied"]] == ["AX-VERIFY"]
    assert ev(ticket_closed=True)["verify_decision"] == "l1"
    assert ev(ticket_closed=True, dne_resolved_after_reset=True, integration_failure=True)["verify_decision"] == "l1"
    assert ev(ticket_closed=True, dne_how_to=True)["routing_hint"] == "l1_self_service"
    assert ev(payment_failure=True)["verify_decision"] is None


def test_proposed_network_rule_is_inactive_unless_what_if():
    f = {**BASE, "network_issue": True, "pos_affected": True, "during_service": True}
    assert sr.evaluate(f)["decision"] == "l1"
    r = sr.evaluate(f, include_proposed=True)
    assert r["decision"] == "escalate" and [x["id"] for x in r["fired_rules"]] == ["SOP-PROPOSED-NET"]
    assert "SOP-PROPOSED-NET (proposed, inactive)" in sr.render_table()


def test_table_header_matches_increases_stack():
    md = sr.render_table()
    head = md.split("## Priority overrides")[1].splitlines()[0]
    assert "stacking" not in head and "one level at most" in head


SAFETY_TEXT = ("For your safety: keep everyone away from the device. Switch off power at the breaker only if it is "
               "safe to do so. Follow the store's emergency procedure and call emergency services if there is fire "
               "or an injury. Do not try to fix or restart the device.")


def test_ax_safety_escalates_p1_no_steps_facilities_and_fixed_reply():
    """v2.0.1: decided before the do-not-escalate list; P1 whatever the base; fixed safety text opens the reply."""
    r = ev(safety_hazard=True, dne_user_error=True, cosmetic_only=True, ticket_priority="P4")
    assert r["decision"] == "escalate" and r["priority"] == "P1" and r["no_steps"]
    assert ids(r, applied_only=True) == ["AX-SAFETY"] and "SOP-DNE-2" in ids(r)          # DNE superseded
    assert r["notify"] == ["facilities"] and r["reply_prefix"] == SAFETY_TEXT
    assert r["priority_rules"][-1]["id"] == "AX-SAFETY" and r["priority_rules"][-1]["change"] == "P4 -> P1"
    assert sr.handoff_spec()["notify"]["facilities"] == "facilities-logistics@servewell.in"
    # v2.0.2: runs right after verify, before non-IT: a non-IT hazard (fryer smoke) still escalates at P1
    r = ev(safety_hazard=True, is_it=False, ticket_priority="P3")
    assert r["decision"] == "escalate" and r["priority"] == "P1" and r["reply_prefix"] == SAFETY_TEXT
    assert ids(r, applied_only=True) == ["AX-SAFETY"] and "AX-NON-IT" in ids(r) and r["notify"] == ["facilities"]
    # closed ticket: verify mode, flag only, no escalation / prefix / notify
    r = ev(safety_hazard=True, ticket_closed=True)
    assert r["decision"] == "verify" and r["mode"] == "verify" and "safety_hazard_reported" in r["flags"]
    assert r["reply_prefix"] is None and r["notify"] == [] and ids(r, applied_only=True) == ["AX-VERIFY"]
    assert "safety_hazard_reported" not in ev(safety_hazard=True)["flags"]
    assert "safety_hazard_reported" not in ev(ticket_closed=True)["flags"]


def test_ax_power_escalates_facilities_no_steps_and_blocks_dne7():
    r = ev(power_instability=True, dne_resolved_after_reset=True)
    assert r["decision"] == "escalate" and r["no_steps"] and r["notify"] == ["facilities"]
    assert ids(r, applied_only=True) == ["AX-POWER"] and "SOP-DNE-7" not in ids(r)       # never fires, not even superseded
    assert r["reply_prefix"] is None and r["priority"] == "P3"                           # no forced priority
    assert "SOP-DNE-7" in ids(ev(dne_resolved_after_reset=True), applied_only=True)       # unchanged without it
    assert ev(power_instability=True, is_it=False)["decision"] == "non_it"              # stays after non-IT
    assert sr.load_rules()["stages"] == ["verify", "safety", "non_it", "power", "do_not_escalate", "escalate", "clarify"]
