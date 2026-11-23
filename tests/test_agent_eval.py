"""Agent eval scoring (offline): verdict parsing, routing collapse, severity, metrics, held-out guard."""
from __future__ import annotations

import importlib.util
import sys

import pytest

from ax15.settings import ROOT


@pytest.fixture(scope="module")
def ev():
    spec = importlib.util.spec_from_file_location("agent_eval", ROOT / "eval" / "agent_eval.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["agent_eval"] = mod
    spec.loader.exec_module(mod)
    yield mod
    sys.modules.pop("agent_eval", None)


def _run(status="ok", **ans):
    a = {"routing": "l1_guided", "should_escalate": False, "kb_doc": "runbooks/a.md", "steps": [], **ans}
    return {"status": status, "answer": a if status == "ok" else None, "tool_calls": 5, "cost_usd": 0.004,
            "latency_s": 20.0, "error": None if status == "ok" else "x", "trace_path": None}


LABEL = {"correct_routing": "l2_escalation", "should_escalate": True, "relevant_kb_docs": ["runbooks/a.md"]}


def test_parse_verdict(ev):
    assert ev.parse_verdict('{"verdict": "PASS", "reason": "ok"}') == ("PASS", "ok")
    assert ev.parse_verdict('text {"verdict": "FAIL", "reason": "missed"} more') == ("FAIL", "missed")
    assert ev.parse_verdict('{"verdict": "MAYBE", "reason": "x"}') is None
    assert ev.parse_verdict("nope") is None


def test_collapse(ev):
    assert [ev.collapse(r) for r in ("l1_guided", "l1_self_service", "l2_escalation", "non_it", None)] == \
        ["l1", "l1", "l2", "other", None]


def test_metrics_and_severity(ev):
    corpus = "Check the cable."
    hit = ev.score_run("A", _run(routing="l2_escalation", should_escalate=True, steps=["1. Check the cable."]),
                       LABEL, corpus)
    miss = ev.score_run("B", _run(), LABEL, corpus)                        # missed escalation
    bad = ev.score_run("C", _run("invalid_json"), LABEL, corpus)
    m = ev.metrics([hit, miss, bad])
    assert m["routing_exact"] == pytest.approx(1 / 3) and m["esc_recall"] == pytest.approx(1 / 3)
    assert m["esc_precision"] == 1.0 and m["kb_doc_hit"] == pytest.approx(2 / 3)
    assert m["invalid_json"] == pytest.approx(1 / 3) and m["step_fidelity"] == 1.0
    assert ev.severity(miss)[0] > ev.severity(hit)[0] == 0
    assert "missed escalation" in ev.severity(miss)[1]
    assert ev.severity(hit, {"verdict": "FAIL", "reason": "trap"})[0] == 3


def test_eval_never_runs_heldout(ev):
    assert "split = 'dev'" in ev.DEV_SQL
    assert ev.HELDOUT_SIZE_SQL.lower().startswith("select count(*)")
    src = (ROOT / "eval" / "agent_eval.py").read_text(encoding="utf-8")
    assert src.count("'heldout'") == 1
