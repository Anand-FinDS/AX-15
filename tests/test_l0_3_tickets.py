"""L0-3 acceptance: ticket normaliser (JSON + free text), leakage stripping, train load."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from ax15 import db as db_mod
from ax15.l0_data.leakage import has_leak
from ax15.l0_data.load_train import normalise_train
from ax15.l0_data.normalise import TicketValidationError, from_json, from_text

VERSIONS = ["FoodTech POS v4.2.1", "FoodTech POS v4.2.2", "NetLink NL-3000 v2.1", "StarMC Print v2 v3.0"]
T0 = datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)


def _prompt_text(ticket) -> str:
    return json.dumps(ticket.prompt_fields(), default=str)


# --------------------------------------------------------------------- train set (offline)

@pytest.fixture(scope="module")
def train():
    items, errors = normalise_train()
    assert errors == []
    return {n.ticket.ticket_id: n for n in items}


def test_train_counts(train):
    assert len(train) == 256
    assert sum(len(n.ticket.history) for n in train.values()) == 252
    assert all(n.ticket.source == "train" and n.ticket.batch_id == "train" for n in train.values())


def test_no_prompt_field_contains_chaos(train):
    leaking = [tid for tid, n in train.items() if "chaos" in _prompt_text(n.ticket).lower()]
    assert leaking == []
    assert not any(has_leak(_prompt_text(n.ticket)) for n in train.values())


def test_inc_00298_generator_text_removed(train):
    n = train["INC-00298"]
    assert len(n.ticket.history) == 2                     # row kept, sentence removed
    note = n.ticket.history[1].note
    assert note == "Ticket routed to Infrastructure team for KitchenBridge v2.3 investigation."
    assert n.leaks.sentences_removed == [
        ("ticket_history[1].note", "Automatic escalation triggered due to unknown_system chaos type.")
    ]


def test_only_inc_00298_needed_stripping(train):
    assert [tid for tid, n in train.items() if n.leaks.any] == ["INC-00298"]


# --------------------------------------------------------------------- JSON intake

def _json_ticket(**over):
    base = {"ticket_id": "INC-90001", "store_id": "SW-0001", "subject": "Printer offline",
            "description": "Receipt printer not printing.", "priority": "P3", "category": "Printers",
            "ticket_history": []}
    base.update(over)
    return base


def test_json_drops_label_keys_and_generator_sentences():
    n = from_json(
        _json_ticket(chaos_type="vague", correct_routing="l1_guided",
                     description="Printer is down. This is a red_herring test. Please help."),
        source="panel_test", batch_id="b1",
    )
    assert set(n.leaks.keys_dropped) == {"chaos_type", "correct_routing"}
    assert n.ticket.description == "Printer is down. Please help."
    assert "chaos" not in _prompt_text(n.ticket).lower()


def test_json_unknown_fields_and_bad_priority_are_warnings():
    n = from_json(_json_ticket(foo=1, priority="urgent", category="wifi"), source="panel_test", batch_id="b1")
    assert n.ticket.priority is None and n.ticket.category == "Wi-Fi / Network"
    assert any("unknown fields: foo" in w for w in n.warnings)
    assert any("priority" in w for w in n.warnings)


@pytest.mark.parametrize("bad", [
    _json_ticket(ticket_id=""),
    _json_ticket(subject=None, description=None),
    _json_ticket(ticket_history="not a list"),
    ["not", "an", "object"],
])
def test_json_rejects_unusable_tickets(bad):
    with pytest.raises(TicketValidationError):
        from_json(bad, source="panel_test", batch_id="b1")


# --------------------------------------------------------------------- free-text intake

def test_free_text_with_headers():
    msg = (
        "Ticket: INC-91000\nStore: SW-0042\nFrom: Priya Nair\nPriority: P2\nCategory: printer\n"
        "Subject: Kitchen printer jammed\n\n"
        "The kitchen printer PRN-0042-P1 keeps jamming since 9am. Same as INC-00015."
    )
    t = from_text(msg, source="panel_test", batch_id="b1", known_versions=VERSIONS, received_at=T0).ticket
    assert (t.ticket_id, t.store_id, t.submitted_by, t.priority) == ("INC-91000", "SW-0042", "Priya Nair", "P2")
    assert t.category == "Printers" and t.subject == "Kitchen printer jammed"
    assert t.asset_id == "PRN-0042-P1"
    assert t.related_tickets == ["INC-00015"]
    assert t.description.startswith("The kitchen printer") and t.input_format == "free_text"


def test_free_text_unstructured_message():
    msg = ("Hi team, store SW-0023 here. Terminal POS-0023-T2 on FoodTech POS v4.2.1 froze during lunch rush, "
           "this is P1 for us!!")
    n = from_text(msg, source="panel_test", batch_id="b1", known_versions=VERSIONS, received_at=T0)
    t = n.ticket
    assert (t.store_id, t.asset_id, t.priority) == ("SW-0023", "POS-0023-T2", "P1")
    assert t.system_version == "FoodTech POS v4.2.1"
    assert t.category == "POS"                              # from the asset prefix
    assert t.ticket_id.startswith("TXT-") and t.date_opened == T0
    assert t.subject and len(t.subject) <= 120
    assert t.raw_input == msg
    assert any("assigned TXT-" in w for w in n.warnings)


def test_free_text_id_is_deterministic():
    a = from_text("Wifi is down at SW-0001.", source="panel_test", batch_id="b1").ticket.ticket_id
    b = from_text("Wifi is down at SW-0001.", source="panel_test", batch_id="b1").ticket.ticket_id
    assert a == b


def test_free_text_keyword_category_and_unknown_category():
    t = from_text("Soft serve machine shows error E04, mix level low.", source="panel_test", batch_id="b1").ticket
    assert t.category == "Soft Serve" and t.asset_id is None
    n = from_text("Something is wrong, please call me.", source="panel_test", batch_id="b1")
    assert n.ticket.category is None
    assert any("category not determined" in w for w in n.warnings)


def test_free_text_does_not_split_versions_or_leak():
    msg = "Router keeps dropping. Automatic escalation due to unknown_system chaos type. NetLink NL-3000 v2.1 here."
    n = from_text(msg, source="panel_test", batch_id="b1", known_versions=VERSIONS)
    assert n.ticket.raw_input == "Router keeps dropping. NetLink NL-3000 v2.1 here."
    assert n.ticket.system_version == "NetLink NL-3000 v2.1"
    assert "chaos" not in _prompt_text(n.ticket).lower()


@pytest.mark.parametrize("msg", ["", "   \n ", "This is chaos."])
def test_free_text_rejects_empty(msg):
    with pytest.raises(TicketValidationError):
        from_text(msg, source="panel_test", batch_id="b1")


# --------------------------------------------------------------------- database

@pytest.fixture(scope="module")
def db():
    if not db_mod.configured():
        pytest.skip("no database configured")
    conn, _ = db_mod.connect()
    yield conn
    conn.close()


@pytest.mark.db
def test_train_loaded_in_db(db):
    n_t = db.execute("SELECT count(*) FROM tickets WHERE source = 'train' AND batch_id = 'train'").fetchone()[0]
    n_h = db.execute(
        "SELECT count(*) FROM ticket_history h JOIN tickets t USING (ticket_uid) WHERE t.batch_id = 'train'"
    ).fetchone()[0]
    status = db.execute("SELECT status, files_loaded FROM batch_runs WHERE batch_id = 'train'").fetchone()
    assert (n_t, n_h) == (256, 252)
    assert status == ("completed", 256)


@pytest.mark.db
def test_no_chaos_in_db_text(db):
    hits = db.execute(
        """
        SELECT t.ticket_id FROM tickets t
        WHERE concat_ws(' ', t.ticket_id, t.store_name, t.submitted_by, t.subject, t.description,
                        t.subcategory, t.system_version, t.resolution_notes, t.status, t.raw_input,
                        array_to_string(t.tags, ' '), array_to_string(t.related_tickets, ' ')) ILIKE '%%chaos%%'
        UNION
        SELECT t.ticket_id FROM ticket_history h JOIN tickets t USING (ticket_uid)
        WHERE concat_ws(' ', h.actor, h.note) ILIKE '%%chaos%%'
        """
    ).fetchall()
    assert hits == []


@pytest.mark.db
def test_inc_00298_history_in_db(db):
    notes = db.execute(
        "SELECT h.note FROM ticket_history h JOIN tickets t USING (ticket_uid)"
        " WHERE t.batch_id = 'train' AND t.ticket_id = 'INC-00298' ORDER BY h.seq"
    ).fetchall()
    assert len(notes) == 2
    assert notes[1][0] == "Ticket routed to Infrastructure team for KitchenBridge v2.3 investigation."
