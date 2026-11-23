"""Offline checks for the freeze-stage eval scripts: held-out runner, what-if scoring, compare labels."""
from __future__ import annotations

import importlib.util
import sys

import pytest

from ax15.settings import ROOT


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "eval" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_heldout_runner_excludes_smoke_ticket_and_dev_eval_stays_dev_only():
    h = _load("heldout_eval")
    assert h.EXCLUDE == ("INC-00162",) and "split = 'heldout'" in h.HELDOUT_SQL
    src = (ROOT / "eval" / "agent_eval.py").read_text(encoding="utf-8")
    assert src.count("'heldout'") == 1                      # agent_eval only counts the held-out size


def test_whatif_routing_and_scores():
    w = _load("whatif_network_rule")
    assert w.routing_of({"decision": "escalate", "routing_hint": None, "verify_decision": None}) == ("l2_escalation", True)
    assert w.routing_of({"decision": "verify", "routing_hint": None, "verify_decision": "escalate"}) == \
        ("l2_escalation", False)                           # verify: rules class, never an escalation
    s = w.score([("l2_escalation", True, "l2_escalation", True), ("l1_guided", False, "l2_escalation", True)])
    assert s["routing"] == 0.5 and s["precision"] == 1.0 and s["recall"] == 0.5


def test_compare_render_uses_given_labels_and_handles_no_chaos():
    c = _load("agent_compare")
    row = {"status": "ok", "routing": "l1_guided", "should_escalate": False, "label_escalate": False,
           "label_routing": "l1_guided", "routing_collapsed_ok": True, "kb_hit": True, "pilot": True, "n_steps": 1,
           "fidelity": 1.0, "tool_calls": 3, "cost_usd": 0.001, "latency_s": 1.0, "fired": [], "flags": [],
           "kb_doc": "runbooks/x.md"}
    md = c.render({"dev": {"T1": row}, "chaos": {}}, {"dev": {"T1": row}, "chaos": {}}, "iter 2", "final",
                  scope="held-out split")
    assert "| Metric | iter 2 all dev | final all dev |" in md and "Held-out split and pilot scope" in md
    assert "Chaos set" not in md
