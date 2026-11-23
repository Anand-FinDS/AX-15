"""AX-15 2.0 graph: policy upgrades in decide, guard stripping, dual-query merge, asset resolution,
fail-safe on a failed LLM node, and HITL interrupt / resume (live DB, fake LLMs: no API spend)."""
from __future__ import annotations

import pytest

from ax15 import db as db_mod
from ax15.agents import v2_graph as g
from ax15.agents.v2_schemas import ClassifyOut, ComposeOut, ResolveOut
from ax15.l1_knowledge.search import DocRoute
from ax15.trust import guard


def classify_out(**kw) -> dict:
    base = {f: False for f, info in ClassifyOut.model_fields.items() if info.annotation is bool}
    base.update(intents=[{"kind": "it", "summary": "Kitchen printer offline"}], clarity="clear", clarifying_questions=[], devices_affected=None,
                clean_symptom="Kitchen receipt printer is offline and not printing orders",
                affected_device_type="receipt_printer", error_codes=[], systems_mentioned=[],
                steps_already_tried=[], claims_to_verify=[], requester_tone="calm")
    base.update(kw)
    return ClassifyOut.model_validate(base).model_dump(mode="json")


FACTS = {"is_it": True, "ticket_priority": "P3", "ticket_status": "Open", "opened_hour_ist": 9.0,
         "store_type": "franchise"}
TICKET = {"ticket_id": "INC-T", "priority": "P3", "category": "Printers", "subject": "printer offline",
          "date_opened": "2025-12-22T03:00:00Z", "store_id": "SW-0100"}


def decide_state(route_doc="runbooks/printer-offline.md", doc_text="", **res) -> dict:
    r = {"ok": True, "doc_text": doc_text, "immediate": [], "all_steps_tried": False, "needs_l2_only": False, "criteria_met": [], "criteria_text": [],
         "l2_stubs": [], "steps": ["1. **Physical Check**"], "step_ids": ["S1"], "breadcrumb": "Doc > Immediate"}
    r.update(res)
    return {"trace_id": "test-decide", "ticket": TICKET, "classify": classify_out(), "facts": dict(FACTS),
            "route": {"doc": route_doc, "routing_query": "raw", "flags": []}, "resolve": r,
            "enrich": {"sla": {"priority": "P3"}, "store": {"store_name": "X"}}, "extra": {"flags": []},
            "llm_failed": []}


# --- decide: policy upgrades (no DB, no LLM) ---------------------------------------------------------

def test_decide_l1_keeps_steps():
    out = g.n_decide(decide_state())
    d = out["decision"]
    assert d["decision"] == "l1" and d["routing"] == "l1_guided" and d["steps"] == ["1. **Physical Check**"]


@pytest.mark.parametrize("kw, rule", [
    ({"route_doc": None}, "AX-KB-GAP-NO-DOC"),
    ({"needs_l2_only": True, "l2_stubs": ["L2-only procedure exists: X — must be performed by L2."]}, "AX-L2-ONLY"),
    ({"all_steps_tried": True, "steps": [], "step_ids": []}, "SOP-STEP2-EXHAUSTED"),
    ({"criteria_met": ["E2"], "criteria_text": ["Issue persists after completing all L1 Resolution steps"]},
     "RB-WHEN-TO-ESCALATE"),
])
def test_decide_upgrades_to_escalate(kw, rule):
    out = g.n_decide(decide_state(**kw))
    d = out["decision"]
    assert d["decision"] == "escalate" and d["routing"] == "l2_escalation" and d["should_escalate"]
    assert d["steps"] == []                                     # escalations carry no L1 steps
    assert rule in [f["id"] for f in out["policy"]["fired_rules"] if f["applied"]]
    assert d["handoff"]["priority"] and "error_codes" in d["handoff"]["missing_fields"]


def test_decide_failed_llm_node_escalates():
    st = decide_state()
    st["llm_failed"] = ["resolve"]
    d = g.n_decide(st)["decision"]
    assert d["decision"] == "escalate" and "llm_failed" in d["flags"]


# --- guard ----------------------------------------------------------------------------------------

CORPUS = "1. **Physical Check** - Verify the printer is powered on\n\u0000\n2. Restart the POS application"


def _answer(**kw):
    a = {"routing": "l1_guided", "should_escalate": False, "priority": "P3", "confidence": "high",
         "steps": ["1. **Physical Check** - Verify the printer is powered on", "3. Replace the motherboard"],
         "reply_to_store": "Please follow the steps below. Your printer PRN-0100-P1 should come back."}
    a.update(kw)
    return a


def test_guard_strips_non_verbatim_step():
    ans, rep = guard.check(_answer(), decision="l1", sources="PRN-0100-P1", corpus=CORPUS, l2_texts=[], vocab=[],
                           no_steps=False)
    assert ans["steps"] == ["1. **Physical Check** - Verify the printer is powered on"]
    assert rep.stripped_steps == ["3. Replace the motherboard"] and "guard:step_not_verbatim" in rep.flags


def test_guard_escalates_when_nothing_safe_remains():
    ans, rep = guard.check(_answer(steps=["Do something invented here"]), decision="l1", sources="PRN-0100-P1",
                           corpus=CORPUS, l2_texts=[], vocab=[], no_steps=False)
    assert ans["should_escalate"] and ans["routing"] == "l2_escalation" and ans["steps"] == [] and rep.escalated


def test_guard_strips_ungrounded_ids_and_devices_and_l2_text():
    reply = ("Your printer PRN-9999-P9 is faulty. Try the Epson TM-T88VI driver. "
             "Open the registry and set the admin password to the default value for this terminal. Thanks.")
    l2 = ["Open the registry and set the admin password to the default value for this terminal"]
    ans, rep = guard.check(_answer(reply_to_store=reply, steps=[]), decision="l1", sources="StarMC Print v2",
                           corpus=CORPUS, l2_texts=l2, vocab=["Epson TM-T88VI", "StarMC Print v2"], no_steps=False)
    assert ans["reply_to_store"] == "Thanks."
    assert len(rep.stripped_sentences) == 3 and "guard:l2_only_text" in rep.flags


def test_guard_no_steps_policy_and_template_reply():
    ans, rep = guard.check(_answer(reply_to_store=""), decision="escalate", sources="", corpus=CORPUS, l2_texts=[],
                           vocab=[], no_steps=True)
    assert ans["steps"] == [] and "guard:no_steps_policy" in rep.flags and rep.template_reply
    assert "L2" in ans["reply_to_store"]


# --- dual-query routing merge -----------------------------------------------------------------------

def test_dual_query_merge_dedupes_and_caps():
    raw = [DocRoute("runbooks/pos-payment-error.md", 0.05, [], 3), DocRoute("runbooks/pos-pin-pad.md", 0.03, [], 1),
           DocRoute("runbooks/pos-startup-failure.md", 0.02, [], 1)]
    sym = [DocRoute("runbooks/network-devices-not-connecting.md", 0.06, [], 4),
           DocRoute("runbooks/pos-payment-error.md", 0.04, [], 2), DocRoute("runbooks/network-slow.md", 0.035, [], 2)]
    m = g.merge_candidates(raw, sym, n=5)
    assert [c["doc"] for c in m] == ["runbooks/network-devices-not-connecting.md", "runbooks/pos-payment-error.md",
                                     "runbooks/network-slow.md", "runbooks/pos-pin-pad.md",
                                     "runbooks/pos-startup-failure.md"]
    by = {c["doc"]: c for c in m}
    assert by["runbooks/pos-payment-error.md"]["found_by"] == ["raw", "symptom"]
    assert by["runbooks/pos-payment-error.md"]["score"] == 0.05
    assert g.routing_query_of(["raw", "symptom"]) == "both" and g.routing_query_of(["symptom"]) == "symptom"
    assert len(g.merge_candidates(raw, sym, n=3)) == 3


# --- asset resolution (code) ------------------------------------------------------------------------

STORE_ASSETS = {"assets": [{"asset_id": "POS-1-T1", "asset_type": "POS Terminal"},
                           {"asset_id": "PRN-1-P1", "asset_type": "Receipt Printer"},
                           {"asset_id": "RTR-1-01", "asset_type": "Network Router"}]}


def test_asset_not_found_with_no_single_candidate_is_unverified():
    e = {"asset": {"status": "not_found", "asset_id": "POS-9999-T5"}, "store_assets": {"assets": []}}
    r = g.resolve_asset({"asset_id": "POS-9999-T5"}, e, classify_out(affected_device_type="pos_terminal"))
    assert r["asset_unverified"] and any(f.startswith("asset_not_in_cmdb") for f in r["flags"])


def test_cmdb_gap_is_flag_not_block():
    e = {"asset": {"status": "mismatch", "mismatch_kind": "system", "asset_type": "Receipt Printer"},
         "store_assets": STORE_ASSETS}
    r = g.resolve_asset({"asset_id": "PRN-1-P1"}, e, classify_out(affected_device_type="soft_serve_machine"))
    assert not r["asset_unverified"] and not r["system_mismatch"]
    assert any(f.startswith("cmdb_gap") for f in r["flags"])


def test_system_mismatch_on_matching_device_type():
    e = {"asset": {"status": "mismatch", "mismatch_kind": "system", "asset_type": "Receipt Printer"},
         "store_assets": STORE_ASSETS}
    r = g.resolve_asset({"asset_id": "PRN-1-P1"}, e, classify_out(affected_device_type="receipt_printer"))
    assert r["system_mismatch"]


def test_build_facts_unknown_system_error_code_peak_and_recurrence():
    ref = {"vocab": ["FoodTech POS", "StarMC Print v2"], "corpus_lower": "foodtech pos error 0x8004 printer"}
    t = {**TICKET, "system_version": "KitchenBridge v2.3", "subcategory": "Printer Offline",
         "date_opened": "2025-12-22T06:56:00Z"}                     # 12:26 IST
    e = {"asset": {"status": "not_found"}, "store_assets": {"assets": []}, "store": {"store_type": "corporate"},
         "recent": {"tickets": [{"ticket_id": "INC-1", "subcategory": "Printer Offline"},
                                {"ticket_id": "INC-2", "subcategory": "printer offline"},
                                {"ticket_id": "INC-3", "subcategory": "Paper Jam"}]},
         "past_actions": {"actions": []}}
    c = classify_out(error_codes=["0x8004", "ERR-4521"], systems_mentioned=["FoodTech POS v4.2.1"],
                     affected_device_type="online_ordering")
    f, extra = g.build_facts(t, e, c, ref)
    assert f["unknown_system"] and f["unknown_systems"] == ["KitchenBridge"]
    assert f["unknown_error_codes"] == ["ERR-4521"]
    assert f["opened_hour_ist"] == pytest.approx(12.43, abs=0.01) and f["recurrence_count"] == 2
    assert f["store_type"] == "corporate" and not f["ticket_closed"]


# --- HITL interrupt / resume on the live DB with fake LLM nodes --------------------------------------

@pytest.fixture()
def fake_llms(monkeypatch):
    from ax15.agents import llm
    from ax15.l1_knowledge import picker

    def fake_structured(node, system, user, schema, trace_id=None, client=None, fallback_client=None):
        if schema is ClassifyOut:
            parsed = ClassifyOut.model_validate(classify_out(device_unusable=True))
        elif schema is ResolveOut:
            parsed = ResolveOut(section="Immediate Steps", step_ids=["S1", "S2"], skipped_already_tried=[],
                                escalation_criteria_met=[], needs_l2_only=False, reason="first steps")
        else:
            parsed = ComposeOut(reply_to_store="Please follow the steps below and tell us the result.",
                                escalation_reason=None, l2_handoff=None)
        return llm.LLMResult(parsed, "fake-model", "fake", calls=1)

    monkeypatch.setattr(llm, "structured", fake_structured)
    monkeypatch.setattr(picker, "pick_doc", lambda text, cands, client=None, cache=None, cards=None:
                        picker.Pick("runbooks/printer-offline.md", "fake pick", valid=True, attempts=1))


@pytest.mark.db
def test_interrupt_and_resume(fake_llms, monkeypatch):
    if not db_mod.configured():
        pytest.skip("no database configured")
    monkeypatch.setenv("AX15_LANGFUSE", "0")
    r = g.run_agent("INC-00031", batch_id="train", split="dev", write_action=True)
    conn, _ = db_mod.connect(autocommit=True)
    try:
        assert r.status == "ok" and r.awaiting_review and r.action_id, r.error
        assert r.node_path[-1] == "hitl_propose" and r.checkpointer in ("postgres", "sqlite")
        assert r.answer["steps"] and r.answer["routing"] == "l1_guided"
        assert conn.execute("SELECT status FROM action_log WHERE action_id = %s", (r.action_id,)).fetchone()[0] \
            == "proposed"
        assert g.pending_review(r.trace_id)["action_id"] == r.action_id
        out = g.resume(r.trace_id, "reject", "human:pytest")
        assert out["resumed"] and out["applied"] and out["node_path"][-1] == "hitl_review"
        row = conn.execute("SELECT status, verified_by_human, verified_by FROM action_log WHERE action_id = %s",
                           (r.action_id,)).fetchone()
        assert row == ("rejected", True, "human:pytest")
        assert g.pending_review(r.trace_id) is None and not g.resume(r.trace_id, "approve")["resumed"]
    finally:
        if r.action_id:
            conn.execute("DELETE FROM action_log WHERE action_id = %s", (r.action_id,))
        for t in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            conn.execute(f"DELETE FROM {t} WHERE thread_id = %s", (r.trace_id,))
        conn.close()


@pytest.mark.db
def test_eval_mode_stops_after_guard(fake_llms, monkeypatch):
    if not db_mod.configured():
        pytest.skip("no database configured")
    monkeypatch.setenv("AX15_LANGFUSE", "0")
    r = g.run_agent("INC-00031", batch_id="train", split="dev", write_action=False, memory=False)
    assert r.status == "ok" and r.node_path[-1] == "guard" and r.action_id is None and r.checkpointer is None
    assert r.answer["node_path"][-1] == "guard" and "get_past_actions" not in r.tools_used


def test_agent_code_never_touches_eval():
    import inspect
    import re
    from ax15.agents import llm, v2_schemas
    from ax15.l1_knowledge import steps
    from ax15.trust import sop_rules
    for mod in (g, guard, llm, v2_schemas, steps, sop_rules):
        src = inspect.getsource(mod)
        assert not re.search(r"eval\.(eval_|chaos_)|eval_labels|chaos_labels|eval_splits", src), mod.__name__
        assert not re.search(r"^\s*(from|import)\s+eval\b", src, re.M), mod.__name__


def test_retargeted_asset_skips_system_check(monkeypatch):
    """The claimed system named the original (wrong) device, so it is not compared with the retargeted asset."""
    from ax15.tools import functions as T
    from ax15.tools.models import AssetOut
    monkeypatch.setattr(T, "get_asset", lambda asset_id, claimed_system=None, conn=None: AssetOut(
        status="mismatch", asset_id=asset_id, asset_type="Receipt Printer", mismatch=True, mismatch_kind="system"))
    e = {"asset": {"status": "ok", "asset_type": "POS Terminal", "asset_id": "POS-1-T1"}, "store_assets": STORE_ASSETS}
    r = g.resolve_asset({"asset_id": "POS-1-T1", "system_version": "FoodTech POS v4.2.1"}, e,
                        classify_out(affected_device_type="receipt_printer"))
    assert r["asset_id"] == "PRN-1-P1" and not r["system_mismatch"]
    assert "system_check_skipped:retargeted" in r["flags"]


# --- 2.0 freeze changes (2026-11-23) ------------------------------------------------------------------

def test_verify_mode_is_information_only():
    st = decide_state()
    st["facts"].update(ticket_closed=True, ticket_status="Closed", integration_failure=True)
    d = g.n_decide(st)["decision"]
    assert d["mode"] == "verify" and not d["should_escalate"] and d["handoff"] is None
    assert d["verify_rules_routing"] == "l2_escalation" and d["routing"] == "l1_guided" and d["steps"] == []
    assert g._after_guard({"write_action": True, "batch_id": "train", "answer": {"mode": "verify"}}) == "__end__"
    assert g._after_guard({"write_action": True, "batch_id": "train", "answer": {"mode": "handle"}}) == "hitl_propose"


REF = {"vocab": ["FoodTech POS", "EpsonTM-T88VI"], "corpus_lower": "printer foodtech pos"}
E0 = {"asset": {"status": "ok", "asset_type": "Receipt Printer"}, "store_assets": {"assets": []},
      "store": {"store_type": "franchise"}, "recent": {"tickets": []}, "past_actions": {"actions": []}}


def test_pure_non_it_ticket_is_non_it_without_uplift():
    from ax15.trust import sop_rules
    c = classify_out(intents=[{"kind": "non_it", "summary": "Salary not credited -> HR/payroll"}])
    f, extra = g.build_facts({**TICKET, "priority": "P2", "date_opened": "2025-12-22T06:56:00Z"}, E0, c, REF)
    assert f["is_it"] is False and extra["non_it_redirects"] == ["Salary not credited -> HR/payroll"]
    r = sop_rules.evaluate({**f, "store_type": "corporate"})
    assert r["decision"] == "non_it" and r["priority"] == "P2" and r["no_steps"]
    assert [p["id"] for p in r["priority_rules"]] == ["AX-PRI-NON-IT"]          # no peak / corporate uplift


def test_mixed_ticket_handles_it_part_and_redacts_redirect():
    c = classify_out(intents=[{"kind": "it", "summary": "Printer jam"},
                              {"kind": "non_it", "summary": "Salary for employee ID: PA-0021-847 -> HR/payroll"}])
    f, extra = g.build_facts(TICKET, E0, c, REF)
    assert f["is_it"] is True
    assert extra["non_it_redirects"] == ["Salary for employee ID: [redacted] -> HR/payroll"]


def test_guard_redacts_employee_ids():
    ans, rep = guard.check(_answer(steps=[], reply_to_store="We saw employee ID: PA-0021-847 in the ticket. Thanks.",
                                   l2_handoff={"summary": "User PA-0021-847 cannot log in", "evidence": [],
                                               "attempted_steps": []}),
                           decision="l1", sources="my staff credentials (employee ID: PA-0021-847) RTR-0102-01 PTW-043-001",
                           corpus=CORPUS, l2_texts=[], vocab=[], no_steps=False)
    assert "PA-0021-847" not in ans["reply_to_store"] + ans["l2_handoff"]["summary"]
    assert guard.redact("router RTR-0102-01, item PTW-043-001") == "router RTR-0102-01, item PTW-043-001"
    assert "guard:redacted_personal_id" in rep.flags


TWO_SAME = {"assets": [{"asset_id": "PRN-1-P1", "asset_type": "Receipt Printer", "model": "EpsonTM-T88VI"},
                       {"asset_id": "PRN-1-P2", "asset_type": "Receipt Printer", "model": "EpsonTM-T88VI"}]}
TWO_DIFF = {"assets": [{"asset_id": "PRN-1-P1", "asset_type": "Receipt Printer", "model": "StarMC Print v2"},
                       {"asset_id": "PRN-1-P2", "asset_type": "Receipt Printer", "model": "EpsonTM-T88VI"}]}


def test_same_model_candidates_proceed_and_ask_which():
    e = {"asset": {"status": "ok", "asset_type": "POS Terminal"}, "store_assets": TWO_SAME}
    r = g.resolve_asset({"asset_id": "POS-1-T1"}, e, classify_out(affected_device_type="receipt_printer"))
    assert not r["asset_unverified"] and r["ask_which_device"] and len(r["candidates"]) == 2


@pytest.mark.parametrize("doc_text, clarify", [("Epson TM-T88VI and Star MC Print v2 printers", False),
                                                ("Epson TM-T88VI only", True)])
def test_different_model_candidates_depend_on_runbook_coverage(doc_text, clarify):
    e = {"asset": {"status": "ok", "asset_type": "POS Terminal"}, "store_assets": TWO_DIFF}
    r = g.resolve_asset({"asset_id": "POS-1-T1"}, e, classify_out(affected_device_type="receipt_printer"))
    assert r["needs_model_check"] and not r["asset_unverified"]
    st = decide_state(doc_text=doc_text)
    st["extra"] = {"flags": [], "needs_model_check": True, "candidates": r["candidates"]}
    st["facts"]["asset_id"] = "POS-1-T1"
    d = g.n_decide(st)["decision"]
    assert (d["decision"] == "clarify") is clarify
    assert bool(d["steps"]) is (not clarify)
    if not clarify:
        assert d["ask_which_device"]


def test_asset_not_found_proposes_cmdb_correction():
    e = {"asset": {"status": "not_found", "asset_id": "POS-9999-T5"}, "store_assets": {"assets": [
        {"asset_id": "POS-1-T1", "asset_type": "POS Terminal", "model": "A"},
        {"asset_id": "POS-1-T2", "asset_type": "POS Terminal", "model": "B"}]}}
    r = g.resolve_asset({"asset_id": "POS-9999-T5"}, e, classify_out(affected_device_type="pos_terminal"))
    assert r["asset_unverified"] and len(r["candidates"]) == 2
    assert r["cmdb_correction"]["ticket_asset_id"] == "POS-9999-T5" and r["cmdb_correction"]["suggested"] is None


def test_escalation_shows_first_response_steps_unless_no_steps():
    imm = ["1. **Physical Check**", "2. **Printer Self-Test**", "3. **Restart POS Application**"]
    st = decide_state(criteria_met=["E1"], criteria_text=["persists"], immediate=imm)
    d = g.n_decide(st)["decision"]
    assert d["should_escalate"] and d["steps"] == [] and d["interim_steps"] == imm
    st = decide_state(criteria_met=["E1"], criteria_text=["persists"], immediate=imm)
    st["facts"].update(asset_unverified=True, payment_failure=True, asset_id="X")
    assert g.n_decide(st)["decision"]["interim_steps"] == []                 # no_steps flag fired
    st = decide_state(needs_l2_only=True, l2_stubs=["L2-only procedure exists: X"], immediate=imm)
    assert g.n_decide(st)["decision"]["interim_steps"] == []                 # the fix is L2-only


def test_guard_strips_non_verbatim_interim_step():
    ans, rep = guard.check(_answer(steps=[], interim_steps=["1. **Physical Check** - Verify the printer is powered on",
                                                            "Invented step"]),
                           decision="escalate", sources="PRN-0100-P1", corpus=CORPUS, l2_texts=[], vocab=[],
                           no_steps=False)
    assert ans["interim_steps"] == ["1. **Physical Check** - Verify the printer is powered on"]
    assert "guard:interim_step_removed" in rep.flags


def test_dual_query_off_routes_raw_text_only(monkeypatch):
    from ax15.config import models
    from ax15.l1_knowledge import picker
    assert models()["routing"]["dual_query"] is False
    queries = []

    def fake_route(conn, q, filters=None, **kw):
        queries.append(q)
        return type("R", (), {"docs": [DocRoute("runbooks/printer-offline.md", 0.05, [], 2)]})()
    monkeypatch.setattr(g, "route_doc", fake_route)
    monkeypatch.setattr(g._db, "run", lambda fn, conn=None: fn(None))
    monkeypatch.setattr(picker, "pick_doc", lambda text, cands, client=None, cache=None, cards=None:
                        picker.Pick(cands[0], "fake", valid=True, attempts=1))
    out = g.n_route({"trace_id": "test-route", "ticket": TICKET,
                     "classify": classify_out(clean_symptom="a different symptom sentence")})
    assert queries == ["printer offline"] and out["route"]["routing_query"] == "raw"
    assert len(out["route"]["candidates"]) <= 3


@pytest.mark.parametrize("code, ok", [("0x8004", True), ("E-402", True), ("ERR-4521", True), ("Code 43", True),
                                      ("No DHCP lease", False), ("Session Authentication Failed", False)])
def test_error_code_extractor_ignores_messages(code, ok):
    assert g.is_error_code(code) is ok


def test_safety_rule_end_of_graph_p1_facilities_and_fixed_reply_opening(monkeypatch):
    """v2.0.1 AX-SAFETY through decide + guard: escalate P1, no steps or interim steps, facilities in the handoff
    notify list, and the fixed safety text opens the reply (added in code, after the model and the guard)."""
    from ax15.trust import sop_rules
    st = decide_state(immediate=["1. **Physical Check**"])
    st["facts"].update(safety_hazard=True, dne_user_error=True)
    out = g.n_decide(st)
    d = out["decision"]
    assert d["decision"] == "escalate" and d["priority"] == "P1" and d["steps"] == [] and d["interim_steps"] == []
    assert "facilities-logistics@servewell.in" in d["handoff"]["notify"] and "oncall@servewell.in" in d["handoff"]["notify"]
    monkeypatch.setattr(g, "_ref", lambda conn: {"corpus": CORPUS, "l2": [], "vocab": [], "sop": ""})
    gs = {**st, **out, "compose": {"reply_to_store": "We have escalated this to the specialist (L2) team as P1.",
                                   "l2_handoff": None, "escalation_reason": None}, "models": [], "node_path": []}
    ans = g.n_guard(gs)["answer"]
    text = sop_rules.evaluate({**FACTS, "safety_hazard": True})["reply_prefix"]
    assert ans["reply_to_store"].startswith(text + "\n\n") and ans["reply_to_store"].count(text) == 1
    assert ans["steps"] == [] and ans["should_escalate"] and ans["priority"] == "P1"
