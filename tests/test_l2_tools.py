"""L2 tools against the live DB: POS end-to-end, not_found / mismatch paths, verbatim KB sections,
L2-only stubs, the single write, and registry schemas."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import jsonschema
import pytest

from ax15 import db as db_mod
from ax15 import tools as T
from ax15.tools import registry
from ax15.tools.functions import actor_type, parse_claimed

POS_TICKET = "INC-00162"          # POS / printer integration, store SW-0111, asset POS-0111-T1
MISMATCH_TICKET = "INC-00014"     # chaos unknown_system: ticket names KitchenBridge v2.3, CMDB asset is a router


# --- offline ----------------------------------------------------------------------------------

def test_registry_schemas_validate():
    specs = registry.schemas()
    assert [s["name"] for s in specs] == ["get_ticket", "get_store", "get_asset", "list_store_assets",
                                          "get_recent_tickets", "get_sla", "kb_route", "kb_section", "get_past_actions",
                                          "log_action"]
    for s in specs:
        jsonschema.Draft202012Validator.check_schema(s["input_schema"])
        assert s["input_schema"]["type"] == "object" and s["description"].endswith(".")
        assert len(s["description"]) < 160
    assert [t.name for t in registry.TOOLS.values() if t.writes] == ["log_action"]
    # sample arguments validate against the published schemas
    jsonschema.validate({"ticket_id": "INC-00162"}, registry.TOOLS["get_ticket"].input_schema())
    jsonschema.validate({"priority": "P2", "category": "POS", "created_at": "2026-03-04T13:48:00Z"},
                        registry.TOOLS["get_sla"].input_schema())
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"ticket_id": "X", "action": "delete", "actor": "a"},
                            registry.TOOLS["log_action"].input_schema())


def test_parse_claimed_and_actor_type():
    assert parse_claimed("FoodTech POS v4.2.1", ["FoodTech POS", "OrbitPOS"]) == ("FoodTech POS", "v4.2.1")
    assert parse_claimed("KitchenBridge v2.3", ["FoodTech POS"]) == ("KitchenBridge", "v2.3")
    assert parse_claimed("StarMC Print v2 v3.0", ["StarMC Print v2"]) == ("StarMC Print v2", "v3.0")
    assert (actor_type("resolver"), actor_type("human:anand"), actor_type("system:batch")) == ("agent", "human", "system")


# --- live DB ----------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def conn():
    if not db_mod.configured():
        pytest.skip("no database configured")
    c, _ = db_mod.connect(autocommit=True)
    yield c
    c.close()


@pytest.mark.db
def test_pos_ticket_end_to_end(conn):
    """ticket -> store -> asset -> SLA -> kb_route -> kb_section (one real picker call)."""
    t = T.get_ticket(POS_TICKET, conn=conn)
    assert t.status == "ok" and t.category == "POS" and t.store_id and t.asset_id
    s = T.get_store(t.store_id, conn=conn)
    a = T.get_asset(t.asset_id, claimed_system=t.system_version, conn=conn)
    sla = T.get_sla(t.priority, t.category, t.date_opened, now=t.date_opened + timedelta(minutes=45), conn=conn)
    recent = T.get_recent_tickets(t.store_id, days=60, as_of=t.date_opened, exclude_ticket_id=t.ticket_id, conn=conn)
    route = T.kb_route(f"{t.subject}\n{t.description}", conn=conn)
    sec = T.kb_section(route.doc or route.candidates[0].doc, f"{t.subject}\n{t.description}", conn=conn)

    assert s.status == "ok" and s.store_id == t.store_id
    assert a.status == "ok" and a.system == "FoodTech POS" and a.version == "v4.2.1" and not a.mismatch
    assert a.version_status == "known_issues" and "flicker" in a.known_bugs
    assert sla.status == "ok" and sla.response_breached and not sla.breached and sla.minutes_left_resolution == 195
    assert recent.status == "ok" and all(x.source == "history" for x in recent.tickets)
    assert route.status == "ok" and len(route.candidates) == 3
    assert all(c.doc.startswith("runbooks/") for c in route.candidates)
    assert route.doc == "runbooks/pos-printer-integration.md"
    assert sec.status == "ok" and sec.section.chunks and sec.escalation and "Escalate" in sec.escalation.h2
    assert sec.citations

    print(f"\n[e2e] ticket   {t.ticket_id} · {t.priority} {t.category}/{t.subcategory} · {t.subject!r}")
    print(f"[e2e] store    {s.store_id} · {s.store_name} · {s.store_type} · RM {s.region_manager}")
    print(f"[e2e] asset    {a.asset_id} · {a.system} {a.version} · {a.version_status}: {a.known_bugs} · "
          f"patch: {a.patch_available} · mismatch={a.mismatch}")
    print(f"[e2e] sla      response due {sla.response_due:%H:%M} (breached={sla.response_breached}) · "
          f"resolution due {sla.resolution_due:%H:%M} ({sla.minutes_left_resolution} min left at +45 min)")
    print(f"[e2e] recent   {recent.recurrence_count} other ticket(s) at the store in 60 days: "
          f"{[x.ticket_id for x in recent.tickets]}")
    print(f"[e2e] kb_route candidates {[c.doc for c in route.candidates]}")
    print(f"[e2e] kb_route chosen {route.doc} · {route.reason}")
    print(f"[e2e] section  {sec.matched_breadcrumb}")
    print(f"[e2e] section  H2 '{sec.section.h2}', {len(sec.section.chunks)} chunks, {len(sec.section.text)} chars; "
          f"escalation H2 '{sec.escalation.h2}', {len(sec.escalation.chunks)} chunk(s)")
    print("[e2e] section  first lines: " + " | ".join([ln for ln in sec.section.text.splitlines() if ln.strip()][:5]))


@pytest.mark.db
def test_not_found_paths(conn):
    assert T.get_store("SW-9999", conn=conn).status == "not_found"
    assert T.list_store_assets("SW-9999", conn=conn).status == "not_found"
    assert T.get_recent_tickets("SW-9999", conn=conn).status == "not_found"
    assert T.get_asset("POS-8341-T2", conn=conn).status == "not_found"      # chaos false_asset INC-00048
    assert T.get_ticket("INC-99999", conn=conn).status == "not_found"
    assert T.get_sla("P2", "Laundry", datetime.now(timezone.utc), conn=conn).status == "not_found"
    assert T.kb_section("runbooks/kitchenbridge.md", "reset", conn=conn).status == "not_found"
    assert T.log_action("INC-99999", "update", {}, "pytest", conn=conn).status == "not_found"


@pytest.mark.db
def test_asset_mismatch_from_chaos_set(conn):
    t = T.get_ticket(MISMATCH_TICKET, conn=conn)
    a = T.get_asset(t.asset_id, claimed_system=t.system_version, conn=conn)
    assert a.status == "mismatch" and a.mismatch and a.mismatch_kind == "system"
    assert a.system == "NetLink NL-3000" and "KitchenBridge" in a.message
    v = T.get_asset("POS-0111-T1", claimed_system="FoodTech POS v5.1.1", conn=conn)
    assert v.status == "mismatch" and v.mismatch_kind == "version"


@pytest.mark.db
def test_list_store_assets(conn):
    out = T.list_store_assets("SW-0111", conn=conn)
    assert out.status == "ok" and "POS-0111-T1" in {x.asset_id for x in out.assets}
    assert all(x.system and x.version for x in out.assets)


@pytest.mark.db
def test_kb_section_is_verbatim(conn):
    sec = T.kb_section("runbooks/pos-printer-integration.md", "printer shows offline after power cycle", conn=conn)
    assert sec.status == "ok"
    for block in (sec.section, sec.escalation):
        for ch in block.chunks:
            db_text = conn.execute("SELECT text FROM kb_chunks WHERE chunk_id = %s", (ch.chunk_id,)).fetchone()[0]
            assert ch.text == db_text                                   # string-equal, original numbering
        assert block.text == "\n\n".join(ch.text for ch in block.chunks)


@pytest.mark.db
def test_kb_section_never_returns_l2_text_to_store(conn):
    doc, q = "system-specs/foodtech-pos.md", "version upgrade procedure"
    store = T.kb_section(doc, q, audience="store", conn=conn)
    agent = T.kb_section(doc, q, audience="agent", conn=conn)
    l2 = {r[0]: r[1] for r in conn.execute(
        "SELECT chunk_id, text FROM kb_chunks WHERE doc_path = %s AND metadata->>'audience' = 'L2'", (doc,))}
    assert l2
    store_chunks = [c for b in (store.section, store.escalation) if b for c in b.chunks]
    assert any(c.l2_stub for c in store_chunks)                         # stub, not silence
    for c in store_chunks:
        if c.chunk_id in l2:
            assert c.text is None and c.l2_stub.startswith("L2-only procedure exists:")
    store_text = " ".join(b.text for b in (store.section, store.escalation) if b)
    assert not any(t in store_text for t in l2.values())
    agent_chunks = [c for b in (agent.section, agent.escalation) if b for c in b.chunks]
    assert any(c.chunk_id in l2 and c.text == l2[c.chunk_id] for c in agent_chunks)


@pytest.mark.db
def test_log_action_writes_one_row(conn):
    before = conn.execute("SELECT count(*) FROM action_log").fetchone()[0]
    out = T.log_action(POS_TICKET, "work_note", {"note": "pytest L2 tool check"}, "pytest", conn=conn)
    try:
        assert out.status == "ok" and out.action_type == "work_note" and out.action_status == "proposed"
        assert out.trace_id and conn.execute("SELECT count(*) FROM action_log").fetchone()[0] == before + 1
        row = conn.execute("SELECT trace_id, actor_type, payload, verified_by_human FROM action_log"
                           " WHERE action_id = %s", (out.action_id,)).fetchone()
        assert row == (out.trace_id, "agent", {"note": "pytest L2 tool check"}, False)
    finally:
        conn.execute("DELETE FROM action_log WHERE action_id = %s", (out.action_id,))


@pytest.mark.db
def test_registry_call_round_trip(conn):
    out = registry.call("get_store", {"store_id": "SW-0111"}, conn=conn)
    assert out["status"] == "ok" and json.dumps(out)
    out = registry.call("get_asset", {"asset_id": "NOPE-1"}, conn=conn)
    assert out["status"] == "not_found"


@pytest.mark.db
def test_get_ticket_is_prompt_safe(conn):
    out = T.get_ticket("INC-00298", conn=conn).model_dump_json().lower()
    assert "chaos" not in out and "relevant_kb_docs" not in out and "correct_routing" not in out


@pytest.mark.db
def test_get_past_actions_returns_only_human_verified(conn):
    """proposed rows are never returned; approved / rejected rows are, with verdict and reviewer."""
    tk = T.get_ticket(POS_TICKET, conn=conn)
    made = []
    try:
        for status, human in (("proposed", False), ("approved", True), ("rejected", True)):
            made.append(conn.execute(
                "INSERT INTO action_log (ticket_uid, trace_id, actor, actor_type, action_type, payload, status,"
                " verified_by_human, verified_by, verified_at) SELECT ticket_uid, 'pytest-past', 'pytest', 'agent',"
                " 'escalate_to_l2', %s::jsonb, %s, %s, CASE WHEN %s THEN 'human:pytest' END, CASE WHEN %s THEN now() END"
                " FROM tickets WHERE ticket_id = %s AND batch_id = %s RETURNING action_id",
                (json.dumps({"kb_doc": "runbooks/pos-printer-integration.md", "routing": "l2_escalation"}),
                 status, human, human, human, POS_TICKET, tk.batch_id)).fetchone()[0])
        out = T.get_past_actions(tk.store_id, tk.asset_id, conn=conn)
        mine = [a for a in out.actions if a.ticket_id == POS_TICKET and a.verified_by == "human:pytest"]
        assert out.status == "ok" and sorted(a.verdict for a in mine) == ["approved", "rejected"]
        assert all(a.runbook == "runbooks/pos-printer-integration.md" and a.routing == "l2_escalation" for a in mine)
        assert T.get_past_actions("SW-NOPE", conn=conn).status == "not_found"
        assert registry.call("get_past_actions", {"store_id": tk.store_id}, conn=conn)["status"] == "ok"
    finally:
        conn.execute("DELETE FROM action_log WHERE action_id = ANY(%s)", (made,))
