"""L1: chunker + metadata (offline), RRF and answer guard (offline), search + ingestion (DB)."""
from __future__ import annotations

import re
from collections import Counter

import pytest

from ax15 import db as db_mod
from ax15.l1_knowledge import answer as ans
from ax15.l1_knowledge.chunker import MIN_WORDS, audience, chunk_kb, section_type
from ax15.l1_knowledge.search import Hit, rrf


@pytest.fixture(scope="module")
def docs():
    return chunk_kb()


@pytest.fixture(scope="module")
def chunks(docs):
    return [c for d in docs for c in d.chunks]


def test_all_kb_docs_chunked(docs):
    assert Counter(d.doc_type for d in docs) == {"runbook": 32, "faq": 6, "sop": 3, "system_spec": 7}
    assert all(d.chunks for d in docs)


def test_every_chunk_has_breadcrumb_starting_with_title(docs):
    for d in docs:
        for c in d.chunks:
            assert c.breadcrumb.startswith(d.title), (c.doc_path, c.breadcrumb)
            # only a doc's intro (text before the first H2) has the bare title as breadcrumb
            assert " > " in c.breadcrumb or c.chunk_index == 0, (c.doc_path, c.breadcrumb)


def test_small_chunks_merged(chunks):
    small = [c for c in chunks if c.words < MIN_WORDS and c.metadata["doc_type"] != "faq"]
    assert small == []


def test_no_chunk_starts_mid_numbered_list(chunks):
    assert not [c for c in chunks if re.match(r"\s*(?:[2-9]|\d{2,})\.\s", c.text)]


def test_numbered_lists_never_split(docs):
    """A numbered item n>1 at the start of a chunk's first list must follow item n-1 in the same chunk."""
    for d in docs:
        for c in d.chunks:
            first = re.search(r"^\s*(\d+)\.\s", c.text, re.M)
            if first and not c.text[: first.start()].strip("# \n").count("\n") and first.group(1) != "1":
                pytest.fail(f"{c.doc_path}#{c.chunk_index} starts at list item {first.group(1)}")


def test_faq_one_qa_per_chunk(docs):
    for d in (d for d in docs if d.doc_type == "faq"):
        for c in d.chunks:
            assert c.text.count("**Q:") <= 1, (c.doc_path, c.chunk_index)
        assert sum(c.text.count("**Q:") for c in d.chunks) >= 10


def test_section_type_rules():
    assert section_type(["Immediate Steps (First 2 Minutes)"]) == "immediate"
    assert section_type(["L1 Diagnosis Steps", "Step 1: Confirm"]) == "diagnosis"
    assert section_type(["L1 Resolution", "Resolution Path 1"]) == "resolution"
    assert section_type(["When to Escalate to L2"]) == "escalate"
    assert section_type(["Overview"]) == "other"


def test_audience_rules():
    assert audience(["Version Upgrade Path", "Upgrade Procedure (L2 Only)"], "") == "L2"
    assert audience(["Configuration", "Registry Paths"], "") == "L2"
    assert audience(["Restore"], "# CRITICAL: Requires L2 approval and maintenance window") == "L2"
    assert audience(["L1 Resolution", "Resolution Path C: Password Issue"], "") == "L1"
    assert audience(["Steps"], "Do not attempt to repair it yourself. This requires L2 intervention.") == "L1"


def test_foodtech_l2_sections_flagged(docs):
    ft = next(d for d in docs if d.doc_path == "system-specs/foodtech-pos.md")
    l2 = " | ".join(c.breadcrumb for c in ft.chunks if c.metadata["audience"] == "L2")
    for needle in ("Upgrade Procedure (L2 Only)", "Registry Paths", "Restore Procedures", "Rollback Procedure"):
        assert needle in l2


def test_rrf_combines_rankings():
    s = rrf([1, 2, 3], [3, 1])
    assert max(s, key=s.get) == 1                 # high in both lists
    assert s[3] > s[2]                            # in both lists beats one list
    assert s[2] == pytest.approx(1 / 62)


def test_answer_without_hits_does_not_call_model():
    class Boom:
        @property
        def messages(self):
            raise AssertionError("model must not be called without chunks")
    a = ans.answer("anything", [], client=Boom())
    assert not a.covered and a.text.startswith(ans.NOT_COVERED)


def test_prompt_numbers_chunks():
    hits = [Hit(i, f"runbooks/x{i}.md", f"Doc > S{i}", f"text {i}", 0.1, "L1", "other", i, None) for i in (1, 2)]
    p = ans.build_prompt("q?", hits)
    assert '<excerpt n="1" doc="runbooks/x1.md"' in p and '<excerpt n="2"' in p


# ------------------------------------------------------------------ database

@pytest.fixture(scope="module")
def conn():
    if not db_mod.configured():
        pytest.skip("no database configured")
    c, _ = db_mod.connect(autocommit=True)
    yield c
    c.close()


@pytest.mark.db
def test_chunks_stored_and_embedded(conn, chunks):
    n, embedded = conn.execute("SELECT count(*), count(embedding) FROM kb_chunks").fetchone()
    assert n == len(chunks) and embedded == n


@pytest.mark.db
def test_last_ingestion_run_embedded_nothing_new(conn):
    total, embedded, unchanged = conn.execute(
        "SELECT chunks_total, chunks_embedded, chunks_unchanged FROM ingestion_runs"
        " WHERE status = 'completed' ORDER BY run_id DESC LIMIT 1"
    ).fetchone()
    assert embedded == 0 and unchanged == total


@pytest.mark.db
def test_search_returns_fields_and_hides_l2_for_store(conn):
    q = "FoodTech POS version upgrade procedure stop services run upgrade registry paths"
    store = __import__("ax15.l1_knowledge.search", fromlist=["search_kb"]).search_kb(conn, q, k=10, audience="store", mode="fts")
    agent = __import__("ax15.l1_knowledge.search", fromlist=["search_kb"]).search_kb(conn, q, k=10, audience="agent", mode="fts")
    assert store and all(h.audience == "L1" for h in store)
    assert any(h.audience == "L2" for h in agent)
    h = agent[0]
    assert h.breadcrumb and h.doc_path and h.text and h.score > 0


# --- contextual prefix, L2-only stubs, category filter -------------------------------------

def test_context_prefix_is_title_plus_overview_sentence(docs):
    d = next(d for d in docs if d.doc_path == "runbooks/pos-startup-failure.md")
    assert d.summary.startswith("POS terminals fail to boot")
    c = d.chunks[1]
    assert c.context_prefix == f"{d.title} — {d.summary}"
    assert c.embed_text.startswith(c.context_prefix) and c.embed_text.endswith(c.text)
    assert c.context_prefix not in c.text                      # display text stays the original


def test_summary_falls_back_to_first_paragraph_and_faq_uses_title(docs):
    sop = next(d for d in docs if d.doc_path == "sop/escalation-procedure.md")
    assert sop.summary.startswith("To establish a standardized process")   # "**Purpose:**" label stripped
    faq = next(d for d in docs if d.doc_path == "faq/pos-faq.md")
    assert faq.summary == "" and faq.chunks[0].context_prefix == faq.title
    assert all(d.summary for d in docs if d.doc_type != "faq")


def test_stub_prompt_has_breadcrumb_not_text():
    from ax15.l1_knowledge.search import L2Stub
    stub = L2Stub(9, "runbooks/pos-software-update.md", "FoodTech POS > Upgrade > Rollback Procedure", 2)
    p = ans.build_prompt("q", [], [stub])
    assert "<l2_only>" in p and "L2-only procedure exists: FoodTech POS > Upgrade > Rollback Procedure" in p
    assert "must be performed by L2" in p and "l2_only" in ans.SYSTEM


@pytest.mark.db
def test_store_gets_stubs_without_l2_text(conn):
    from ax15.l1_knowledge.search import search_with_stubs
    q = "FoodTech POS version upgrade procedure stop services run upgrade registry paths"
    r = search_with_stubs(conn, q, k=10, audience="store", mode="fts")
    agent = search_with_stubs(conn, q, k=10, audience="agent", mode="fts")
    assert r.stubs and not agent.stubs
    assert all(h.audience == "L1" for h in r.hits)
    l2_ids = {h.chunk_id for h in agent.hits if h.audience == "L2"}
    assert {s.chunk_id for s in r.stubs} == l2_ids             # exactly the L2 chunks that would have ranked
    assert not hasattr(r.stubs[0], "text")


@pytest.mark.db
def test_category_filter(conn):
    from ax15.l1_knowledge.search import search_kb
    q = "device not connecting to the network"
    hits = search_kb(conn, q, k=10, audience="agent", mode="fts", category="POS")
    assert hits
    cats = {c for (c,) in conn.execute(
        "SELECT DISTINCT metadata->>'category' FROM kb_chunks WHERE chunk_id = ANY(%s)", ([h.chunk_id for h in hits],))}
    assert cats == {"POS"}
