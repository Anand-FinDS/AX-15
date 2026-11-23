"""RAG golden-set scorer: offline checks with a stub judge."""
from __future__ import annotations

from ax15.eval import rag_golden as rg
from ax15.l1_knowledge.answer import NOT_COVERED, Answer
from ax15.l1_knowledge.search import Hit


def _hit(doc, crumb):
    return Hit(1, doc, crumb, "text", 0.03, "L1", "resolution", 1, 1)


def _judge(answer_text, statement, kind):
    return statement.lower() in answer_text.lower()


def test_golden_set_loads_and_is_well_formed():
    items = rg.load_golden()
    assert len(items) >= 10
    for it in items:
        assert it["answer_type"] in {"answer", "not_in_kb", "l2_only"}
        assert it["question"] and isinstance(it["must_include"], list)


def test_score_answer_item():
    item = {"id": "X", "question": "q", "answer_type": "answer", "expected_docs": ["runbooks/a.md"],
            "expected_section": "Immediate Steps", "must_include": ["hold power", "wait 30"],
            "must_not": ["registry"]}
    hits = [_hit("faq/b.md", "B > Other"), _hit("runbooks/a.md", "A > Immediate Steps")]
    r = rg.score_item(item, hits, Answer("Hold power for 10 s [2].", True, "m"), _judge)
    assert r.retrieval_pass and r.section_hit is True
    assert r.fact_share == 0.5 and r.must_not_pass


def test_must_not_fails_and_hit_only_counts_top5():
    item = {"id": "X", "question": "q", "answer_type": "answer", "expected_docs": ["runbooks/a.md"],
            "expected_section": "Fix", "must_include": [], "must_not": ["registry"]}
    hits = [_hit("faq/b.md", "B")] * 5 + [_hit("runbooks/a.md", "A > Fix")]
    r = rg.score_item(item, hits, Answer("Edit the registry.", True, "m"), _judge)
    assert not r.retrieval_pass and r.section_hit is False
    assert r.violations == ["registry"] and r.fact_share is None


def test_not_in_kb_passes_only_when_answer_says_not_covered():
    item = {"id": "X", "question": "q", "answer_type": "not_in_kb", "expected_docs": [],
            "expected_section": "", "must_include": [], "must_not": []}
    ok = rg.score_item(item, [], Answer(f"{NOT_COVERED} Nothing on KitchenBridge.", False, "m"), _judge)
    bad = rg.score_item(item, [], Answer("Reset KitchenBridge from settings.", True, "m"), _judge)
    assert ok.retrieval_pass and ok.section_hit is None
    assert not bad.retrieval_pass
    md = rg.render([ok, bad], "m")
    assert "Retrieval hit@5" in md and "| X | not_in_kb |" in md
