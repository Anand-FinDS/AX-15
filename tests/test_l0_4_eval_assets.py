"""L0-4: chaos labels from the authored doc, seeded stratified split, DB loads."""
from __future__ import annotations

import json
from collections import Counter

import pytest

from ax15 import db as db_mod
from ax15.l0_data import eval_assets as ea


@pytest.fixture(scope="module")
def chaos():
    return ea.build_chaos_labels()


def test_chaos_labels_cover_all_36_and_match_index(chaos):
    assert len(chaos) == 36 and len({c["ticket_id"] for c in chaos}) == 36
    assert Counter(c["chaos_type"] for c in chaos) == {
        "vague": 6, "emotional": 5, "miscategorized": 5, "misdirection": 5,
        "contradictory": 4, "false_asset": 4, "red_herring": 4, "unknown_system": 3}
    assert all(set(c["expected_routing"]) <= ea.ROUTING_CLASSES for c in chaos)
    assert all(c["confidence"] in "HML" for c in chaos)
    assert sum(c["expected_mode"] == "verify" for c in chaos) == 7


def test_chaos_parser_handles_irregular_entries(chaos):
    by = {c["ticket_id"]: c for c in chaos}
    assert by["INC-00155"]["expected_routing"] == ["l2_escalation", "needs_clarification"]
    assert by["INC-00198"]["expected_mode"] == "verify" and by["INC-00198"]["expected_routing"] == ["l1_self_service"]
    assert by["INC-00048"]["expected_routing"] == ["l2_escalation"] and by["INC-00048"]["confidence"] == "M"
    assert by["INC-00130"]["expected_routing"] == ["l2_escalation"] and by["INC-00130"]["confidence"] == "H"


def test_chaos_json_matches_doc(chaos):
    assert json.loads(ea.CHAOS_JSON.read_text(encoding="utf-8")) == chaos


def test_stratified_split_is_seeded_and_balanced():
    items = [(f"T{i:03}", "A" if i < 50 else ("B" if i < 90 else "C")) for i in range(100)]
    dev, held = ea.stratified_split(items)
    assert (dev, held) == ea.stratified_split(items)              # reproducible
    assert len(held) == 20 and not set(dev) & set(held) and len(dev) + len(held) == 100
    strata = dict(items)
    assert Counter(strata[t] for t in held) == {"A": 10, "B": 8, "C": 2}
    assert ea.stratified_split(items, seed=7)[1] != held          # seed matters


def test_splits_json_is_220_dev_heldout():
    s = json.loads(ea.SPLITS_JSON.read_text(encoding="utf-8"))
    assert len(s["dev"]) + len(s["heldout"]) == 220 and len(s["heldout"]) == 44
    assert not set(s["dev"]) & set(s["heldout"])
    assert s["seed"] == ea.SEED and s["split_version"] == ea.SPLIT_VERSION


@pytest.mark.db
def test_db_has_chaos_labels_and_split():
    if not db_mod.configured():
        pytest.skip("no database configured")
    conn, _ = db_mod.connect(autocommit=True)
    with conn:
        assert conn.execute("SELECT count(*) FROM eval.chaos_labels").fetchone()[0] == 36
        sizes = dict(conn.execute("SELECT split, count(*) FROM eval.eval_splits"
                                  " WHERE split_version = %s GROUP BY 1", (ea.SPLIT_VERSION,)).fetchall())
        assert sizes == {"dev": 176, "heldout": 44}
        s = json.loads(ea.SPLITS_JSON.read_text(encoding="utf-8"))
        db_dev = {r[0] for r in conn.execute(
            "SELECT t.ticket_id FROM eval.eval_splits s JOIN tickets t USING (ticket_uid)"
            " WHERE s.split = 'dev' AND s.split_version = %s", (ea.SPLIT_VERSION,))}
        assert db_dev == set(s["dev"])
