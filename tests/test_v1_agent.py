"""AX-15 1.0 agent: tool-call limit, invalid tool args, final JSON schema, no eval access,
tracing failure tolerance, and a live INC-00031 run (dev split; INC-00162 is held-out) (one proposed action_log row)."""
from __future__ import annotations

import ast
import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ax15 import db as db_mod
from ax15.agents import v1_single as v1
from ax15.settings import ROOT
from ax15.trust import tracing

TICKET = {"ticket_id": "UI-TEST-1", "store_id": "SW-0111", "asset_id": "POS-0111-T1", "priority": "P3",
          "category": "POS", "subject": "Printer offline", "description": "Receipt printer shows offline.",
          "date_opened": "2026-03-04T13:48:00Z", "system_version": "FoodTech POS v4.2.1"}

GOOD = {"routing": "l1_guided", "should_escalate": False, "priority": "P3", "kb_doc": "runbooks/printer-offline.md",
        "kb_section_breadcrumb": "Printer Offline > L1 Resolution", "steps": ["1. Check the cable."],
        "reply_to_store": "Please check the cable.", "l2_handoff": None, "flags": [], "confidence": "high"}


def _usage():
    return SimpleNamespace(input_tokens=100, output_tokens=20, cache_read_input_tokens=0, cache_creation_input_tokens=0)


def _tool_turn(i, name="get_store", args=None):
    return SimpleNamespace(stop_reason="tool_use", usage=_usage(), content=[
        SimpleNamespace(type="tool_use", id=f"tu_{i}", name=name, input=args or {"store_id": "SW-0111"})])


def _text_turn(text):
    return SimpleNamespace(stop_reason="end_turn", usage=_usage(), content=[SimpleNamespace(type="text", text=text)])


class FakeClient:
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        t = self.turns.pop(0)
        return t(len(self.calls)) if callable(t) else t


@pytest.fixture(autouse=True)
def _no_langfuse(monkeypatch, tmp_path):
    monkeypatch.setenv("AX15_LANGFUSE", "0")
    monkeypatch.setattr(tracing, "LANGFUSE", tracing._Langfuse())
    monkeypatch.setattr(tracing, "TRACES_DIR", tmp_path / "traces")
    monkeypatch.setenv("AX15_SESSION", "pytest")
    monkeypatch.setattr(v1.spend, "LEDGER", tmp_path / "ledger.jsonl")
    monkeypatch.setattr(v1.tracing.spend, "LEDGER", tmp_path / "ledger.jsonl")


@pytest.fixture
def stub_tools(monkeypatch):
    monkeypatch.setattr(v1, "execute_tool", lambda name, args, conn=None: ({"status": "ok", "name": name}, False))


# --- schema -----------------------------------------------------------------------------------

def test_final_answer_schema_valid_and_fenced():
    a = v1.parse_answer("```json\n" + json.dumps(GOOD) + "\n```")
    assert a.routing == "l1_guided" and a.mode == "handle" and a.steps == ["1. Check the cable."]


@pytest.mark.parametrize("bad", [
    {**GOOD, "routing": "l3_magic"},
    {**GOOD, "priority": "urgent"},
    {**GOOD, "should_escalate": True},                         # escalating without l2_handoff
    {**GOOD, "l2_handoff": {"summary": "x"}},                  # handoff while not escalating
    {**GOOD, "unexpected": 1},
    {k: v for k, v in GOOD.items() if k != "reply_to_store"},
])
def test_final_answer_schema_rejects(bad):
    with pytest.raises(Exception):
        v1.parse_answer(json.dumps(bad))


def test_routing_vocabulary_matches_chaos_labels():
    routing = set(v1.V1Answer.model_fields["routing"].annotation.__args__)
    labels = json.loads((ROOT / "eval" / "chaos_labels.json").read_text(encoding="utf-8"))
    used = {r for c in labels for r in c["expected_routing"]}
    assert used <= routing and {"l1_guided", "l2_escalation"} <= routing


# --- loop limits and tool errors ----------------------------------------------------------------

def test_tool_call_limit_ends_incomplete(stub_tools):
    client = FakeClient([_tool_turn] * 20)
    r = v1.run_agent(TICKET, client=client, write_action=False)
    assert r.status == "incomplete" and "tool-call limit" in r.error
    assert r.tool_calls == v1.limits()["max_tool_calls"] and r.answer is None
    trace = json.loads(Path(r.trace_path).read_text(encoding="utf-8"))
    assert sum(e["type"] == "span" for e in trace["events"]) == r.tool_calls


def test_invalid_tool_args_return_error_result():
    out, err = v1.execute_tool("get_sla", {"priority": "P2"})                 # missing fields
    assert err and out["status"] == "error" and "invalid arguments" in out["message"]
    out, err = v1.execute_tool("log_action", {"ticket_id": "X"})              # not a model tool
    assert err and "unknown tool" in out["message"]


def test_invalid_args_are_fed_back_and_run_completes(monkeypatch):
    client = FakeClient([lambda i: _tool_turn(i, "get_sla", {"priority": "P2"}), _text_turn(json.dumps(GOOD))])
    r = v1.run_agent(TICKET, client=client, write_action=False)
    assert r.status == "ok"
    result = client.calls[1]["messages"][2]["content"][0]        # user turn with the tool results
    assert result["type"] == "tool_result" and result["is_error"] is True
    assert "invalid arguments" in result["content"]


def test_invalid_json_gets_one_repair_then_fails(stub_tools):
    r = v1.run_agent(TICKET, client=FakeClient([_text_turn("not json"), _text_turn("{still bad")]), write_action=False)
    assert r.status == "invalid_json"
    r = v1.run_agent(TICKET, client=FakeClient([_text_turn("oops"), _text_turn(json.dumps(GOOD))]), write_action=False)
    assert r.status == "ok" and r.answer["routing"] == "l1_guided"


def test_ticket_dict_is_filtered_to_prompt_safe_fields(stub_tools):
    client = FakeClient([_text_turn(json.dumps(GOOD))])
    v1.run_agent({**TICKET, "chaos_type": "vague", "relevant_kb_docs": ["x"], "correct_routing": "l2"},
                 client=client, write_action=False)
    msg = client.calls[0]["messages"][0]["content"]
    assert "chaos" not in msg and "relevant_kb_docs" not in msg and "correct_routing" not in msg
    assert "Printer offline" in msg


# --- tracing must never break a run -------------------------------------------------------------

def test_tracing_failure_does_not_break_run(stub_tools, monkeypatch):
    class Boom:
        def start_as_current_observation(self, **kw):
            raise RuntimeError("langfuse down")
    lf = tracing._Langfuse()
    lf.client, lf.enabled, lf._checked = Boom(), True, True
    monkeypatch.setattr(tracing, "LANGFUSE", lf)
    r = v1.run_agent(TICKET, client=FakeClient([_tool_turn, _text_turn(json.dumps(GOOD))]), write_action=False)
    assert r.status == "ok" and r.trace_path and Path(r.trace_path).exists()
    assert lf.enabled is False                                   # sink disabled after the failure
    trace = json.loads(Path(r.trace_path).read_text(encoding="utf-8"))
    assert trace["input_tokens"] == 200 and trace["tool_calls"] == 1


def test_missing_langfuse_keys_is_a_warning_only(monkeypatch, caplog):
    monkeypatch.setenv("AX15_LANGFUSE", "1")
    for k in ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST"):
        monkeypatch.delenv(k, raising=False)
    lf = tracing._Langfuse()
    monkeypatch.setattr(tracing, "LANGFUSE", lf)
    tid = tracing.start_trace("T1")
    s = tracing.end_trace(tid, None, "ok")
    assert lf.enabled is False and "keys not set" in lf.reason and Path(s["trace_path"]).exists()


# --- agent code never touches eval --------------------------------------------------------------

AGENT_DIRS = ["src/ax15/agents", "src/ax15/tools", "src/ax15/trust", "prompts"]


def test_agent_code_never_touches_eval():
    for d in AGENT_DIRS:
        for p in (ROOT / d).rglob("*"):
            if p.suffix not in (".py", ".md") or "__pycache__" in p.parts:
                continue
            src = p.read_text(encoding="utf-8")
            assert not __import__("re").search(r"\beval\.\w+", src), f"{p} references eval.*"
            if p.suffix == ".py":
                for node in ast.walk(ast.parse(src)):
                    mods = []
                    if isinstance(node, ast.Import):
                        mods = [a.name for a in node.names]
                    elif isinstance(node, ast.ImportFrom):
                        mods = [node.module or ""]
                    for m in mods:
                        assert not m.startswith(("eval", "ax15.eval")) and "eval_view" not in m, f"{p} imports {m}"


def test_agent_import_graph_has_no_eval_modules():
    for m in [m for m in sys.modules if m.startswith(("ax15.eval", "retrieval_eval", "agent_eval"))
              or m.endswith("eval_view")]:
        sys.modules.pop(m)
    importlib.reload(v1)
    loaded = [m for m in sys.modules if m.startswith("ax15.eval") or m.endswith("eval_view") or m == "agent_eval"]
    assert loaded == []


# --- live run ---------------------------------------------------------------------------------

@pytest.mark.db
@pytest.mark.llm
def test_inc_00031_end_to_end_writes_one_proposed_action(monkeypatch, tmp_path):
    if not db_mod.configured():
        pytest.skip("no database configured")
    conn, _ = db_mod.connect(autocommit=True)
    with conn:
        r = v1.run_agent("INC-00031", conn=conn)
        try:
            assert r.status == "ok", r.error
            v1.V1Answer.model_validate(r.answer)
            assert r.answer["kb_doc"] and r.answer["kb_doc"].startswith("runbooks/")
            rows = conn.execute("SELECT status, verified_by_human, actor FROM action_log WHERE trace_id = %s",
                                (r.trace_id,)).fetchall()
            assert rows == [("proposed", False, "v1_agent")]
            trace = json.loads(Path(r.trace_path).read_text(encoding="utf-8"))
            spans = [e for e in trace["events"] if e["type"] == "span"]
            assert len(spans) == r.tool_calls and all("latency_ms" in s for s in spans)
            assert trace["input_tokens"] > 0 and trace["output_tokens"] > 0
        finally:
            conn.execute("DELETE FROM action_log WHERE trace_id = %s", (r.trace_id,))
