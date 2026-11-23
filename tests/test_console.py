"""Console: UI ticket creation (source='ui', same model), HITL approve/reject, no labels in the UI,
and a live end-to-end run from the New ticket tab."""
from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest

from ax15 import db as db_mod
from ax15.l0_data.normalise import TicketValidationError
from ax15.l0_data.ui_tickets import create_ui_ticket, ui_batch_id
from ax15.settings import ROOT
from ax15.tools.functions import log_action
from ax15.trust import hitl

LABEL_WORDS = re.compile(r"eval_labels|relevant_kb_docs|correct_routing|should_escalate_label|chaos_type|"
                         r"expected_behavior|expected_routing|\beval\.(?:eval_|chaos_)\w+", re.I)


def test_console_source_has_no_label_fields():
    src = (ROOT / "app" / "console.py").read_text(encoding="utf-8")
    assert not LABEL_WORDS.search(src)


def test_ui_batch_id_format():
    assert ui_batch_id(datetime(2026, 11, 22, tzinfo=timezone.utc)) == "ui-20261122"


@pytest.fixture
def conn():
    if not db_mod.configured():
        pytest.skip("no database configured")
    c, _ = db_mod.connect(autocommit=True)
    yield c
    c.execute("DELETE FROM tickets WHERE source = 'ui' AND submitted_by = 'pytest-console'")
    c.close()


@pytest.mark.db
def test_create_ui_ticket_and_review(conn):
    t, uid = create_ui_ticket(conn, store_id="SW-0111", asset_id="POS-0111-T1", category="POS", priority="P2",
                              subject="Receipt printer offline", description="Printer shows offline after restart.",
                              submitted_by="pytest-console")
    row = conn.execute("SELECT source, batch_id, system_version, store_name, status FROM tickets WHERE ticket_uid = %s",
                       (uid,)).fetchone()
    assert row[0] == "ui" and row[1] == ui_batch_id() and row[2] == "FoodTech POS v4.2.1" and row[3] and row[4] == "Open"
    assert t.ticket_id.startswith("UI-")
    # never mixed into train/eval queries
    assert conn.execute("SELECT count(*) FROM tickets WHERE batch_id = 'train' AND source = 'ui'").fetchone()[0] == 0

    a = log_action(t.ticket_id, "update", {"x": 1}, "v1_agent", trace_id="pytest-trace-1", batch_id=t.batch_id, conn=conn)
    b = log_action(t.ticket_id, "escalate", {"x": 2}, "v1_agent", trace_id="pytest-trace-1", batch_id=t.batch_id, conn=conn)
    assert hitl.review_action(conn, a.action_id, "approve", "console-user")
    assert hitl.review_action(conn, b.action_id, "reject", "console-user")
    assert not hitl.review_action(conn, a.action_id, "reject")                 # already reviewed
    acts = {x["action_id"]: x for x in hitl.proposed_actions(conn, "pytest-trace-1")}
    assert acts[a.action_id]["status"] == "approved" and acts[b.action_id]["status"] == "rejected"
    assert all(x["verified_by_human"] and x["verified_by"] == "console-user" and x["verified_at"] for x in acts.values())


@pytest.mark.db
def test_create_ui_ticket_rejects_bad_input(conn):
    with pytest.raises(TicketValidationError):
        create_ui_ticket(conn, store_id="SW-9999", category="POS", priority="P2", subject="x", description="y")
    with pytest.raises(TicketValidationError):
        create_ui_ticket(conn, store_id="SW-0111", asset_id="POS-0001-T1", category="POS", priority="P2",
                         subject="x", description="y")                         # asset of another store


@pytest.mark.db
@pytest.mark.llm
def test_console_new_ticket_end_to_end_and_approve(conn, monkeypatch):
    from streamlit.testing.v1 import AppTest
    monkeypatch.setattr("ax15.l0_data.ui_tickets.create_ui_ticket.__defaults__", None, raising=False)
    at = AppTest.from_file(str(ROOT / "app" / "console.py"), default_timeout=300).run()
    assert not at.exception
    at.selectbox(key="new_store").set_value("SW-0111").run()
    at.selectbox(key="new_asset").set_value("POS-0111-T1").run()
    at.selectbox(key="new_category").set_value("POS").run()
    at.selectbox(key="new_priority").set_value("P2").run()
    at.text_input(key="new_subject").set_value("Receipt printer offline on terminal 1").run()
    at.text_area(key="new_description").set_value(
        "Receipt printer shows offline in POS since this morning. Power-cycled it twice, still offline.").run()
    at.button(key="new_run").click().run()
    assert not at.exception, at.exception
    res = at.session_state["new_result"]
    tid, bid = at.session_state["new_ticket"]
    conn.execute("UPDATE tickets SET submitted_by = 'pytest-console' WHERE ticket_id = %s AND batch_id = %s", (tid, bid))
    assert res["status"] == "ok", res.get("error")
    assert any(m.label == "Routing" for m in at.metric)
    page = " ".join(m.value for m in at.markdown)
    assert not LABEL_WORDS.search(page)
    acts = hitl.proposed_actions(conn, res["trace_id"])
    assert len(acts) == 1 and acts[0]["status"] == "proposed"
    approve = [b for b in at.button if b.label == "Approve"]
    assert approve
    approve[0].click().run()
    assert hitl.proposed_actions(conn, res["trace_id"])[0]["status"] == "approved"


@pytest.mark.db
def test_clear_ui_tickets_only_touches_ui_rows():
    """Runs inside a transaction that is rolled back, so real console tickets are not deleted by the test."""
    if not db_mod.configured():
        pytest.skip("no database configured")
    from ax15.l0_data.ui_tickets import clear_ui_tickets, ui_ticket_counts
    c, _ = db_mod.connect()                                     # not autocommit: everything below is rolled back
    try:
        train_before = c.execute("SELECT count(*) FROM tickets WHERE source <> 'ui'").fetchone()[0]
        t, _uid = create_ui_ticket(c, store_id="SW-0111", asset_id="POS-0111-T1", category="POS", priority="P3",
                                   subject="pytest clear", description="pytest clear", submitted_by="pytest-console")
        log_action(t.ticket_id, "update", {"x": 1}, "v2_agent", trace_id="pytest-clear", batch_id=t.batch_id, conn=c)
        before = ui_ticket_counts(c)
        assert before["tickets"] >= 1 and before["actions"] >= 1
        out = clear_ui_tickets(c)
        assert out["tickets"] == before["tickets"] and out["actions"] == before["actions"]
        assert ui_ticket_counts(c) == {"tickets": 0, "actions": 0}
        assert c.execute("SELECT count(*) FROM tickets WHERE source <> 'ui'").fetchone()[0] == train_before
    finally:
        c.rollback()
        c.close()


def test_console_metric_formats():
    """Short metric values so st.metric doesn't truncate them (46.2K tokens, $0.0051)."""
    import ast
    tree = ast.parse((ROOT / "app" / "console.py").read_text(encoding="utf-8"))
    fns = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in ("k", "usd")]
    ns: dict = {}
    exec(compile(ast.Module(body=fns, type_ignores=[]), "console_fmt", "exec"), ns)
    assert ns["k"](46213) == "46.2K" and ns["k"](950) == "950" and ns["k"](2_500_000) == "2.5M"
    assert ns["usd"](0.005123) == "$0.0051"


@pytest.mark.db
@pytest.mark.llm
def test_console_compare_mode_runs_both_agents():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "app" / "console.py"), default_timeout=400).run()
    at.radio(key="agent").set_value("Compare 1.0 vs 2.0").run()
    at.radio(key="ex_kind").set_value("Dev split").run()
    at.selectbox(key="ex_ticket").set_value("INC-00031").run()
    at.button(key="ex_run").click().run()
    assert not at.exception, at.exception
    res = at.session_state["ex_result"]
    assert [r["agent"] for r in res["compare"]] == ["v1", "v2"]
    assert all(r["status"] == "ok" for r in res["compare"]), [r.get("error") for r in res["compare"]]
    assert all(r["action_id"] is None for r in res["compare"])           # compare mode proposes nothing
    page = " ".join(m.value for m in at.markdown)
    assert "2.0 workflow" in page and "SOP rules" in page and not LABEL_WORDS.search(page)


def _red_checks():
    from ax15 import status
    return {"neon": status.Check("Neon Postgres", True, "ok"), "anthropic": status.Check("Anthropic", False, "missing"),
            "langfuse": status.Check("Langfuse", None, "off")}


@pytest.mark.db
def test_console_status_sidebar_disables_run_when_anthropic_red(monkeypatch):
    if not db_mod.configured():
        pytest.skip("no database configured")
    from streamlit.testing.v1 import AppTest

    from ax15 import status
    monkeypatch.setattr(status, "console_checks", _red_checks)
    at = AppTest.from_file(str(ROOT / "app" / "console.py"), default_timeout=120).run()
    assert not at.exception, at.exception
    side = " ".join(m.value for m in at.sidebar.markdown)
    assert "Neon Postgres" in side and "Anthropic" in side and "🔴" in side
    assert any("Run is disabled" in e.value for e in at.sidebar.error)
    assert at.button(key="ex_run").disabled and at.button(key="batch_run").disabled


@pytest.mark.db
def test_console_batch_tab_runs_folder_and_opens_a_row(monkeypatch):
    """Batch tab: folder in incoming/ -> Run -> summary + table -> selected row shows answer + trace. Fake agent."""
    if not db_mod.configured():
        pytest.skip("no database configured")
    import shutil

    from streamlit.testing.v1 import AppTest
    from test_batch import _fake_agent, _make_batch

    from ax15 import status
    from ax15.agents import v2_graph
    from ax15.l0_data import batch
    green = {"neon": status.Check("Neon Postgres", True), "anthropic": status.Check("Anthropic", True)}
    monkeypatch.setattr(status, "console_checks", lambda: green)
    monkeypatch.setattr(v2_graph, "run_agent", _fake_agent([]))
    folder = _make_batch(ROOT / "incoming" / "pytest-console-batch")
    c, _ = db_mod.connect(autocommit=True)
    try:
        at = AppTest.from_file(str(ROOT / "app" / "console.py"), default_timeout=180).run()
        at.radio(key="batch_src").set_value("Folder in incoming/").run()
        at.selectbox(key="batch_folder").set_value("pytest-console-batch").run()
        at.button(key="batch_run").click().run()
        assert not at.exception, at.exception
        res = at.session_state["batch_result"]
        assert res.summary()["loaded"] == 4 and res.summary()["rejected"] == 1
        assert any(m.label == "Rejected" and m.value == "1" for m in at.metric)
        assert len(at.dataframe) >= 1
        at.session_state["batch_table"] = {"selection": {"rows": [0], "columns": []}}
        at.run()
        assert not at.exception, at.exception
        assert any(m.label == "Routing" for m in at.metric) and any(m.label == "Tool calls" for m in at.metric)
        page = " ".join(m.value for m in at.markdown)
        assert not LABEL_WORDS.search(page)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
        batch.clear_batch(c, "pytest-console-batch")
        c.close()


_HELDOUT_MD = """# AX-15 held-out run-to-run variance: 1.0 and 2.0 (frozen), 3 runs each

_43 held-out tickets (split `v1-seed42` minus INC-00162) · frozen code · session spend $0.818._

> **Partial:** 1.0 run 3 (not run: INC-00313, INC-00316): stopped at the spend cap, so its metrics cover fewer.

## 1.0

| Metric | Run 1 | Run 2 | Run 3 | Mean | Min | Max | Original held-out run |
|---|---:|---:|---:|---:|---:|---:|---:|
| Routing L1/L2 | 76.7% | 74.4% | 80.5% | 77.2% | 74.4% | 80.5% | 65.1% |
| Escalation precision (verify excluded) | 85.7% | 95.7% | 95.7% | 92.3% | 85.7% | 95.7% | 90.9% |
| Escalation recall (verify excluded) | 80.0% | 73.3% | 75.9% | 76.4% | 73.3% | 80.0% | 66.7% |

**Routing changed between the 3 runs on 13 of 43 tickets** (30%). Run 1 vs the original held-out run: 12 tickets differ.

| Ticket | Pilot | Label | Run 1 | Run 2 | Run 3 | Original run |
|---|---|---|---|---|---|---|
| INC-00053 |  | l2_escalation | l2_escalation | needs_clarification | l2_escalation | needs_clarification |

## 2.0

| Metric | Run 1 | Run 2 | Run 3 | Mean | Min | Max | Original held-out run |
|---|---:|---:|---:|---:|---:|---:|---:|
| Routing L1/L2 | 72.1% | 79.1% | 81.4% | 77.5% | 72.1% | 81.4% | 74.4% |
| Escalation precision (verify excluded) | 81.5% | 85.7% | 86.2% | 84.5% | 81.5% | 86.2% | 82.1% |
| Escalation recall (verify excluded) | 73.3% | 80.0% | 83.3% | 78.9% | 73.3% | 83.3% | 76.7% |

**Routing changed between the 3 runs on 6 of 43 tickets** (14%). Run 1 vs the original held-out run: 3 tickets differ.

## Reading this
"""


def _console_fn(name):
    import ast
    tree = ast.parse((ROOT / "app" / "console.py").read_text(encoding="utf-8"))
    ns: dict = {}
    exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name],
                            type_ignores=[]), "console_fn", "exec"), ns)
    return ns[name]


def test_heldout_summary_reads_aggregates_only():
    s = _console_fn("heldout_summary")(_HELDOUT_MD)
    assert (s["tickets"], s["excluded"]) == (43, "INC-00162")
    assert s["partial"] == ["1.0 run 3 (not run: INC-00313, INC-00316)"]
    a1, a2 = s["agents"]["1.0"], s["agents"]["2.0"]
    assert a1 == {"runs": 3, "routing": ("77.2%", "74.4%", "80.5%"), "esc_p": "92.3%", "esc_r": "76.4%",
                  "changed": (13, 43)}
    assert a2["routing"] == ("77.5%", "72.1%", "81.4%") and a2["changed"] == (6, 43)
    assert "l2_escalation" not in repr(s)           # per-ticket label rows are never read
    assert _console_fn("heldout_summary")("") == {"agents": {}, "tickets": None, "excluded": None, "partial": []}


@pytest.mark.db
def test_console_last_eval_panel_shows_heldout_status():
    """System tab, Last eval: dev rows labelled 'tuned on', a held-out row per agent from heldout_variance.md."""
    from ax15.settings import OUTPUTS_DIR
    if not db_mod.configured():
        pytest.skip("no database configured")
    res = ROOT / "docs" / "results"                     # shipped reports; outputs/ wins when generated locally
    if not all((OUTPUTS_DIR / a).exists() or (res / b).exists() for a, b in (
            ("agent_eval_v1.md", "agent_eval_v1.md"), ("agent_eval_v2.md", "agent_eval_v2_final.md"),
            ("heldout_variance.md", "heldout_variance.md"))):
        pytest.skip("eval reports not generated")
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "app" / "console.py"), default_timeout=120).run()
    assert not at.exception, at.exception
    page = " ".join(m.value for m in at.markdown) + " " + " ".join(c.value for c in at.caption)
    assert "never run" not in page
    assert page.count("| dev (tuned on) |") == 2 and page.count("| dev pilot (tuned on)") == 2
    assert page.count("| held-out (never tuned on): mean of 3 runs | 43 |") == 2
    assert page.count("3 runs × 43 tickets (INC-00162 excluded)") == 2
    assert "routing changed between the 3 runs on 13 / 43" in page and "on 6 / 43" in page
    assert not LABEL_WORDS.search(page)


@pytest.mark.db
def test_console_ask_the_kb_tab_shows_sections_stubs_and_answer(monkeypatch):
    """Ask the KB: question -> top sections (breadcrumb, document, score, L2-only marked) + short cited answer.
    Search and answer are faked (no embedding or model call); no evaluator view, no label text."""
    if not db_mod.configured():
        pytest.skip("no database configured")
    from streamlit.testing.v1 import AppTest

    from ax15 import status
    from ax15.l1_knowledge import answer as answer_mod
    from ax15.l1_knowledge import search as search_mod
    seen = {}

    def fake_search(conn, query, k=5, audience="store", **_):
        seen.update(query=query, k=k, audience=audience)
        hits = [search_mod.Hit(1, "runbooks/printer-offline.md", "Receipt Printer Offline > L1 Resolution > Step 1",
                               "Power-cycle the printer.", 0.0328, "L1", "resolution", 1, 2),
                search_mod.Hit(2, "runbooks/printer-driver.md", "Printer Driver > L2 Notes", "Reinstall the driver.",
                               0.0301, "L2", "l2_notes", 3, 1)][:k]
        stubs = [search_mod.L2Stub(9, "runbooks/printer-driver.md", "Printer Driver > Escalation", 2)]
        return search_mod.Retrieval(hits, stubs)

    monkeypatch.setattr(search_mod, "search_with_stubs", fake_search)
    monkeypatch.setattr(answer_mod, "answer", lambda q, hits, stubs=(), **_: answer_mod.Answer(
        "Power-cycle the printer [1].", True, "fake-model", 120, 12))
    green = {n: status.Check(n, True) for n in ("neon", "anthropic", "openai")}
    monkeypatch.setattr(status, "console_checks", lambda: green)
    at = AppTest.from_file(str(ROOT / "app" / "console.py"), default_timeout=120).run()
    assert not at.exception, at.exception
    assert at.button(key="kb_ask").disabled                       # empty question
    at.text_area(key="kb_question").set_value("Receipt printer stopped printing").run()
    at.radio(key="kb_audience").set_value("L2 agent").run()
    at.button(key="kb_ask").click().run()
    assert not at.exception, at.exception
    assert seen == {"query": "Receipt printer stopped printing", "k": 5, "audience": "agent"}
    assert any("plain RAG" in c.value and "select-don't-write" in c.value for c in at.caption)
    labels = [e.label for e in at.expander]
    assert any(l.startswith("[1] Receipt Printer Offline > L1 Resolution > Step 1") and "0.0328" in l for l in labels)
    assert any(l.startswith("[2] Printer Driver > L2 Notes") and "🔒 L2-only" in l for l in labels)
    assert any("runbooks/printer-offline.md" in c.value for c in at.caption)
    assert any("🔒 L2-only: " in i.value and "withheld" in i.value for i in at.info)
    assert any(s.value == "Power-cycle the printer [1]." for s in at.success)
    page = " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)
    assert "Evaluator view" not in page and not LABEL_WORDS.search(page)
