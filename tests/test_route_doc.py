"""L1-6b: route_doc scoring, store audience never counts L2-only chunks, eval leakage guard."""
from __future__ import annotations

import importlib.util
import sys

import pytest

from ax15 import db as db_mod
from ax15.l1_knowledge import search as search_mod
from ax15.l1_knowledge.search import Hit, route_doc
from ax15.settings import ROOT


def _hit(cid, doc, score, aud="L1"):
    return Hit(cid, doc, f"{doc} > s{cid}", "text", score, aud, "other", cid, None)


@pytest.fixture
def fake_search(monkeypatch):
    seen = {}

    def fake(conn, query, k=5, audience="store", mode="hybrid", query_embedding=None, category=None):
        seen.update(k=k, audience=audience, mode=mode, category=category)
        return [_hit(1, "runbooks/a.md", 0.033), _hit(2, "runbooks/l2.md", 0.032, "L2"),
                _hit(3, "runbooks/l2.md", 0.031, "L2"), _hit(4, "runbooks/b.md", 0.030),
                _hit(5, "runbooks/b.md", 0.020), _hit(6, "runbooks/l2.md", 0.019, "L2")]

    monkeypatch.setattr(search_mod, "search_kb", fake)
    return seen


def test_route_doc_score_and_margin(fake_search):
    r = route_doc(None, "q", {"audience": "agent", "category": "POS", "bonus": 0.1}, mode="hybrid", n_docs=None)
    assert fake_search == {"k": 20, "audience": "agent", "mode": "hybrid", "category": "POS"}
    scores = {d.doc: d.score for d in r.docs}
    assert scores["runbooks/l2.md"] == pytest.approx(0.032 + 0.2)   # max + 0.1 * (3 - 1)
    assert scores["runbooks/b.md"] == pytest.approx(0.030 + 0.1)
    assert r.top(3) == ["runbooks/l2.md", "runbooks/b.md", "runbooks/a.md"]
    assert r.margin == pytest.approx(0.232 - 0.130)


def test_route_doc_store_never_counts_l2_chunks(fake_search):
    r = route_doc(None, "q")                          # default audience = store
    assert fake_search["audience"] == "store"
    assert "runbooks/l2.md" not in r.top(10)
    assert all("l2.md" not in b for d in r.docs for b in d.top_chunk_breadcrumbs)
    assert r.top(2) == ["runbooks/b.md", "runbooks/a.md"]


@pytest.mark.db
def test_route_doc_store_vs_agent_on_l2_query():
    if not db_mod.configured():
        pytest.skip("no database configured")
    conn, _ = db_mod.connect(autocommit=True)
    with conn:
        q = "FoodTech POS version upgrade procedure stop services run upgrade registry paths"
        agent_hits = search_mod.search_kb(conn, q, k=20, audience="agent", mode="fts")
        l2_ids = {h.chunk_id for h in agent_hits if h.audience == "L2"}
        assert l2_ids
        store = route_doc(conn, q, {"audience": "store"}, mode="fts")
        store_crumbs = {b for d in store.docs for b in d.top_chunk_breadcrumbs}
        assert not store_crumbs & {h.breadcrumb for h in agent_hits if h.audience == "L2"}


def _eval_module():
    spec = importlib.util.spec_from_file_location("retrieval_eval", ROOT / "eval" / "retrieval_eval.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["retrieval_eval"] = mod           # dataclasses need the module registered
    spec.loader.exec_module(mod)
    return mod


def test_eval_query_uses_agent_input_fields_only():
    ev = _eval_module()
    assert "eval." not in ev.TICKET_SQL.lower() and "label" not in ev.TICKET_SQL.lower()
    assert ev.build_query("Printer offline", "Kitchen printer shows offline.") == \
        "Printer offline\nKitchen printer shows offline."
    assert ev.split_docs(["sop/escalation-procedure.md", "runbooks/a.md", "faq/pos-faq.md"]) == \
        (["runbooks/a.md"], ["sop/escalation-procedure.md"], ["faq/pos-faq.md"])


def test_route_doc_locked_defaults(fake_search, monkeypatch):
    """Locked config: dense, 0.005 bonus, runbooks only, top 3."""
    def fake(conn, query, k=5, audience="store", mode="hybrid", query_embedding=None, category=None):
        fake_search.update(mode=mode)
        return [_hit(1, "faq/pos-faq.md", 0.017), _hit(2, "runbooks/a.md", 0.016), _hit(3, "runbooks/b.md", 0.0161),
                _hit(4, "runbooks/b.md", 0.015), _hit(5, "runbooks/c.md", 0.014), _hit(6, "runbooks/d.md", 0.0139)]
    monkeypatch.setattr(search_mod, "search_kb", fake)
    r = route_doc(None, "q")
    assert fake_search["mode"] == "dense"
    assert r.top(10) == ["runbooks/b.md", "runbooks/a.md", "runbooks/c.md"]   # FAQ dropped, 3 returned
    assert r.docs[0].score == pytest.approx(0.0161 + 0.005)


def test_three_outcome_scoring():
    ev = _eval_module()
    prim = ["runbooks/a.md", "runbooks/b.md"]
    assert ev.outcome("runbooks/b.md", prim) == "right"
    assert ev.outcome(None, prim) == "escalated"
    assert ev.outcome("runbooks/c.md", prim) == "wrong"
    assert ev.outcome_counts([("runbooks/a.md", prim), (None, prim), (None, prim), ("runbooks/x.md", prim)]) == \
        {"right": 1, "escalated": 2, "wrong": 1}


def test_route_and_pick_calls_picker_on_route_top3(monkeypatch):
    from ax15.l1_knowledge import picker
    from ax15.l1_knowledge.search import DocRoute, RouteResult
    route = RouteResult([DocRoute(f"runbooks/{x}.md", 0.1, [], 1) for x in "abc"], 0.0)
    monkeypatch.setattr(picker, "route_doc", lambda conn, text, filters=None, query_embedding=None: route)
    seen = {}

    def fake_pick(text, cands, client=None, cache=None):
        seen["cands"] = cands
        return picker.Pick(None, "no fit")
    monkeypatch.setattr(picker, "pick_doc", fake_pick)
    r, p = picker.route_and_pick(None, "ticket")
    assert seen["cands"] == ["runbooks/a.md", "runbooks/b.md", "runbooks/c.md"]
    assert p.as_dict() == {"doc": None, "reason": "no fit"}


def test_tuning_script_never_reads_heldout_members():
    src = (ROOT / "eval" / "retrieval_eval.py").read_text(encoding="utf-8")
    ev = _eval_module()
    assert "split = 'dev'" in ev.DEV_SQL
    assert ev.HELDOUT_SIZE_SQL.lower().startswith("select count(*)")
    assert src.count("'heldout'") == 1                      # only the size query
