"""L0-4 (label load): 220 train labels in eval.eval_labels, each joined to a train ticket."""
from __future__ import annotations

import pytest

from ax15 import db as db_mod
from ax15.l0_data.load_labels import read_labels


def test_labels_parse():
    labels = read_labels()
    assert len(labels) == 220
    assert {lab.correct_routing for lab, _ in labels} <= {"l1_guided", "l2_escalation"}


@pytest.mark.db
def test_labels_in_db():
    if not db_mod.configured():
        pytest.skip("no database configured")
    conn, _ = db_mod.connect()
    with conn:
        n, joined = conn.execute(
            "SELECT count(*), count(t.ticket_uid) FROM eval.eval_labels l"
            " LEFT JOIN tickets t ON t.ticket_uid = l.ticket_uid AND t.batch_id = 'train'"
        ).fetchone()
    assert (n, joined) == (220, 220)
