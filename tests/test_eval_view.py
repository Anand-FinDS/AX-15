"""Evaluator view: hit/miss scoring, and labels never reach the answer model."""
from __future__ import annotations

import pytest

from ax15 import db as db_mod
from ax15.l1_knowledge import eval_view
from ax15.l1_knowledge.search import Hit


def test_score_docs_marks_first_rank_and_main_runbook():
    expected = ["sop/escalation-procedure.md", "runbooks/printer-offline.md", "runbooks/pos-scanner.md"]
    s = eval_view.score_docs(expected, ["runbooks/printer-offline.md", "faq/pos-faq.md",
                                        "runbooks/printer-offline.md", "sop/escalation-procedure.md"])
    assert s.expected == [("sop/escalation-procedure.md", 4), ("runbooks/printer-offline.md", 1),
                          ("runbooks/pos-scanner.md", None)]
    assert s.found == 2 and s.recall == pytest.approx(2 / 3)
    assert s.main_runbook == "runbooks/printer-offline.md" and s.main_hit is True


def test_score_docs_without_runbook():
    s = eval_view.score_docs(["sop/escalation-procedure.md"], [])
    assert s.main_runbook is None and s.main_hit is None and s.recall == 0.0


@pytest.mark.db
def test_bot_loads_labels_only_after_answer_and_never_sends_them(monkeypatch):
    if not db_mod.configured():
        pytest.skip("no database configured")
    from streamlit.testing.v1 import AppTest

    from ax15.l1_knowledge import answer as answer_mod
    from ax15.l1_knowledge import search as search_mod

    calls: list[str] = []
    seen: dict = {}
    hits = [Hit(1, "runbooks/pos-printer-integration.md", "POS Printer > Fix", "Restart the spooler.",
                0.03, "L1", "resolution", 1, 2),
            Hit(2, "faq/pos-faq.md", "POS FAQ > Paper", "Reload the paper.", 0.02, "L1", "other", 2, None)]

    def fake_search(conn, query, **kw):
        calls.append("search")
        return search_mod.Retrieval(hits)

    def fake_answer(question, hs, client=None, stubs=()):
        calls.append("answer")
        seen["prompt"] = answer_mod.build_prompt(question, hs, stubs)
        return answer_mod.Answer("Restart the spooler [1].", True, "stub")

    real_expected = eval_view.expected_docs

    def spy_expected(conn, uid):
        calls.append("labels")
        seen["expected"] = real_expected(conn, uid)
        return seen["expected"]

    monkeypatch.setattr(search_mod, "search_with_stubs", fake_search)
    monkeypatch.setattr(answer_mod, "answer", fake_answer)
    monkeypatch.setattr(eval_view, "expected_docs", spy_expected)

    at = AppTest.from_file("app/retrieval_bot.py", default_timeout=60).run()
    next(t for t in at.toggle if t.label == "Evaluator view").set_value(True).run()
    at.selectbox[0].set_value("INC-00162").run()
    at.button[0].click().run()
    assert not at.exception, at.exception

    assert calls == ["search", "answer", "labels"]          # labels read only after the model call
    expected = seen["expected"]
    assert expected and "runbooks/pos-printer-integration.md" in expected
    prompt = seen["prompt"]
    retrieved = {h.doc_path for h in hits}
    for doc in expected:
        if doc not in retrieved:
            assert doc not in prompt                        # no label content in the prompt
    assert "relevant_kb_docs" not in prompt and "eval_labels" not in prompt

    labels = [e.label for e in at.expander]
    assert any(lbl.startswith("✅ HIT · [1]") for lbl in labels)
    assert any(lbl.startswith("❌ MISS · [2]") for lbl in labels)
