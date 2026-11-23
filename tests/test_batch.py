"""L0-5 batch path: intake of CSV / JSON / free text, malformed rows skipped, label columns stripped,
idempotent load as panel_test, eval-mode runs, scoring, report. The agent is faked (no LLM cost);
the live held-out rehearsal is scripts/heldout_rehearsal.py."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field

import pytest

from ax15 import db as db_mod
from ax15.l0_data import batch

HEADER = ["ticket_id", "store_id", "date_opened", "priority", "category", "subject", "description", "asset_id",
          "system_version", "tags", "ticket_history", "escalation_flag", "status", "chaos_type", "correct_routing"]


def _make_batch(folder):
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "tickets.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        w.writerow(["PT-0001", "SW-0111", "2026-04-10T12:30:00+05:30", "P2", "Printers", "Receipt printer offline",
                    "Printer on terminal 1 shows offline, lunch rush, chaos everywhere.", "PRN-0111-01",
                    "", '["printer", "offline"]',
                    json.dumps([{"timestamp": "2026-04-10T12:35:00+05:30", "actor": "L1 Agent",
                                 "note": "Asked store to power-cycle."}]),
                    "False", "Open", "red_herring", "l1_guided"])
        w.writerow(["PT-0002", "SW-0111", "", "P3", "POS", "Card reader declines",
                    "PIN pad declines every card since this morning.", "", "", "pos;pin-pad", "", "", "Open", "", ""])
        w.writerow(["PT-0003", "SW-0111", "", "P3", "POS", "Broken row", "too many cells", "", "", "[]", "[]",
                    "False", "Open", "", "", "EXTRA"])
    (folder / "one.json").write_text(json.dumps({
        "ticket_id": "PT-0004", "store_id": "SW-0111", "priority": "P3", "category": "Wi-Fi / Network",
        "subject": "Guest Wi-Fi down", "description": "Guests cannot connect to Wi-Fi.", "tags": ["wifi"],
        "should_escalate": True, "correct_routing": "l2_escalation", "relevant_kb_docs": ["runbooks/wifi.md"]}),
        encoding="utf-8")
    (folder / "store_message.txt").write_text(
        "Store: SW-0111\nPriority: P2\n\nThe receipt printer PRN-0111-01 is not printing, paper is loaded.",
        encoding="utf-8")
    (folder / "notes.docx").write_text("ignored", encoding="utf-8")
    return folder


def test_intake_csv_json_txt_malformed_row_skipped_and_labels_stripped(tmp_path):
    it = batch.read_batch(_make_batch(tmp_path / "pytest-batch"))
    assert it.batch_id == "pytest-batch" and it.files == ["one.json", "store_message.txt", "tickets.csv"]
    ids = [n.ticket.ticket_id for n in it.items]
    assert {"PT-0001", "PT-0002", "PT-0004"} <= set(ids) and len(ids) == 4
    assert any(t.startswith("TXT-") for t in ids)                       # free text: assigned id
    assert it.rows_read == 5 and len(it.rejected) == 1 and it.rejected[0]["ref"] == "tickets.csv:4"
    assert "16 cells" in it.rejected[0]["error"]
    assert any("notes.docx" in w for w in it.warnings)
    t1 = next(n.ticket for n in it.items if n.ticket.ticket_id == "PT-0001")
    assert t1.tags == ["printer", "offline"] and len(t1.history) == 1 and t1.source == "panel_test"
    assert next(n.ticket for n in it.items if n.ticket.ticket_id == "PT-0002").tags == ["pos", "pin-pad"]
    # eval-only columns never reach the ticket; generator text ("chaos") is stripped by the normaliser
    assert {"chaos_type", "correct_routing", "should_escalate", "relevant_kb_docs"} <= it.dropped_columns
    for n in it.items:
        blob = json.dumps(n.ticket.prompt_fields(), default=str).lower()
        assert "chaos" not in blob and "correct_routing" not in blob and "l2_escalation" not in blob
    assert it.labels == {"PT-0001": {"correct_routing": "l1_guided", "should_escalate": False, "relevant_kb_docs": []},
                         "PT-0004": {"correct_routing": "l2_escalation", "should_escalate": True,
                                     "relevant_kb_docs": ["runbooks/wifi.md"]}}


def test_bad_json_cell_and_bad_folder_names(tmp_path):
    f = tmp_path / "b2"
    f.mkdir()
    (f / "t.csv").write_text('ticket_id,subject,tags\nX-1,ok,"[broken"\nX-2,fine,\n', encoding="utf-8")
    it = batch.read_batch(f)
    assert [n.ticket.ticket_id for n in it.items] == ["X-2"] and it.rejected[0]["ref"] == "t.csv:2"
    for bad in ("train", "ui-20261123", "bad name"):
        (tmp_path / bad).mkdir()
        with pytest.raises(ValueError):
            batch.read_batch(tmp_path / bad)


@dataclass
class _FakeRun:
    ticket_id: str
    answer: dict
    status: str = "ok"
    error: str | None = None
    tool_calls: int = 3
    cost_usd: float = 0.001
    latency_s: float = 1.5
    trace_path: str | None = None
    langfuse_url: str | None = None
    trace_id: str = "fake-trace"
    input_tokens: int = 1200
    output_tokens: int = 300
    extra: dict = field(default_factory=dict)

    def to_dict(self):
        return {k: getattr(self, k) for k in ("ticket_id", "answer", "status", "error", "tool_calls", "cost_usd",
                                              "latency_s", "trace_path", "langfuse_url", "trace_id", "input_tokens",
                                              "output_tokens")}


def _fake_agent(seen):
    from ax15.tools.functions import get_ticket

    def run_agent(tid, *, batch_id=None, split=None, conn=None, write_action=True, memory=True, **_):
        t = get_ticket(tid, batch_id, conn=conn)
        assert t.status == "ok" and t.batch_id == batch_id and t.source == "panel_test"
        seen.append({"tid": tid, "write_action": write_action, "memory": memory, "ticket": t.model_dump(mode="json")})
        esc = "Wi-Fi" in (t.subject or "")
        return _FakeRun(tid, {"routing": "l2_escalation" if esc else "l1_guided", "mode": "guide",
                              "should_escalate": esc, "priority": "P2", "kb_doc": "runbooks/printer-offline.md",
                              "confidence": "medium", "reply_to_store": "Fake reply.", "escalation_reason": None,
                              "kb_section_breadcrumb": None, "l2_handoff": None, "node_path": ["load", "guard"],
                              "steps": [], "flags": ["asset_unverified"] if esc else [],
                              "fired_rules": [{"id": "SOP-TEST", "applied": True, "reason": "test rule", "source": "test"}] if esc else []},
                        trace_path=None if esc else "outputs/traces/x.json")
    return run_agent


@pytest.mark.db
def test_run_batch_loads_runs_scores_reports_and_is_idempotent(tmp_path, monkeypatch):
    if not db_mod.configured():
        pytest.skip("no database configured")
    from ax15.agents import v2_graph
    folder = _make_batch(tmp_path / "pytest-batch-l05")
    seen = []
    monkeypatch.setattr(v2_graph, "run_agent", _fake_agent(seen))
    conn, _ = db_mod.connect(autocommit=True)
    try:
        res = batch.run_batch(folder, agent="v2", workers=2, out_dir=tmp_path / "out")
        q = ("SELECT count(*) FROM tickets WHERE source = 'panel_test' AND batch_id = %s")
        assert conn.execute(q, (res.batch_id,)).fetchone()[0] == 4
        assert all(not s["write_action"] and not s["memory"] for s in seen) and len(seen) == 4
        assert all("chaos" not in json.dumps(s["ticket"]).lower() for s in seen)
        assert conn.execute("SELECT count(*) FROM action_log a JOIN tickets t USING (ticket_uid) WHERE t.batch_id = %s",
                            (res.batch_id,)).fetchone()[0] == 0
        s = res.summary()
        assert (s["loaded"], s["rejected"], s["ok"], s["escalations"], s["labelled"]) == (4, 1, 4, 1, 2)
        assert res.metrics["n"] == 2 and res.metrics["routing_l1_l2"] == 1.0
        md = res.report_md.read_text(encoding="utf-8")
        assert md.index("## Summary") < md.index("## Tickets") and "tickets.csv:4" in md and "P2 → P2" in md
        assert "SOP-TEST" in md and "runbooks/printer-offline.md" in md and "x.json" in md
        rows = list(csv.DictReader(res.report_csv.open(encoding="utf-8")))
        assert len(rows) == 4 and {"routing", "priority_ticket", "priority_agent", "fired_rules", "runbook", "flags",
                                   "cost_usd", "latency_s", "trace", "routing_ok"} <= set(rows[0])
        # re-run: same rows, no duplicates; a ticket removed from the folder is removed from the batch
        (folder / "one.json").unlink()
        res2 = batch.run_batch(folder, agent="v2", workers=2, out_dir=tmp_path / "out")
        assert conn.execute(q, (res2.batch_id,)).fetchone()[0] == 3
        assert conn.execute("SELECT files_loaded, files_rejected, status FROM batch_runs WHERE batch_id = %s",
                            (res2.batch_id,)).fetchone() == (3, 1, "completed")
    finally:
        batch.clear_batch(conn, "pytest-batch-l05")
        assert conn.execute("SELECT count(*) FROM tickets WHERE batch_id = 'pytest-batch-l05'").fetchone()[0] == 0
        conn.close()


def test_cli_parses_run_batch(monkeypatch):
    from ax15 import cli
    got = {}
    monkeypatch.setattr(cli, "_run_batch", lambda a: got.update(vars(a)) or 0)
    assert cli.main(["run-batch", "incoming/x"]) == 0 and got["agent"] == "v2" and got["folder"] == "incoming/x"
    assert cli.main(["run-batch", "incoming/x", "--agent", "v1"]) == 0 and got["agent"] == "v1"


def test_rehearsal_csv_gives_the_same_agent_input_as_the_train_json(tmp_path, monkeypatch):
    """The 10-ticket held-out rehearsal CSV normalises to exactly the tickets the held-out eval ran on
    (so any routing difference in the live rehearsal is model run-to-run variance, not the batch path)."""
    import importlib.util

    from ax15.l0_data.normalise import from_json
    from ax15.settings import ROOT, dataset_dir
    spec = importlib.util.spec_from_file_location("heldout_rehearsal", ROOT / "scripts" / "heldout_rehearsal.py")
    hr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hr)
    monkeypatch.setattr(hr, "FOLDER", tmp_path / "heldout-rehearsal")
    hr.make()
    it = batch.read_batch(tmp_path / "heldout-rehearsal")
    assert len(it.items) == 10 and not it.rejected and len(it.labels) == 10 and "INC-00162" not in it.refs
    assert len({n.ticket.store_id for n in it.items}) == 10
    for n in it.items:
        raw = json.loads((dataset_dir() / "tickets" / "train" / f"{n.ticket.ticket_id}.json").read_text(encoding="utf-8"))
        ref = from_json(raw, source="train", batch_id="train").ticket
        assert n.ticket.prompt_fields() == ref.prompt_fields()
