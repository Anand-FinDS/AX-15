"""L0-2 acceptance: reference loaders + data-quality checks."""
from __future__ import annotations

import pytest

from ax15 import db as db_mod
from ax15.l0_data import quality as q
from ax15.l0_data.models import Store
from ax15.l0_data.reference import ReferenceDataError, read_all, read_csv

EXPECTED = {"stores": 120, "assets": 477, "sla_matrix": 28, "version_matrix": 21}


@pytest.fixture(scope="module")
def data():
    return read_all()


@pytest.fixture(scope="module")
def tickets():
    return q.read_ticket_refs()


def test_reference_counts_from_csv(data):
    assert {k: len(v) for k, v in data.items()} == EXPECTED


def test_validation_rejects_bad_rows(tmp_path):
    (tmp_path / "s.csv").write_text(
        "store_id,store_name,city,neighborhood,store_type,region_manager,support_phone\n"
        "SW-0001,A,X,Y,corporate,M,1\n"
        "SW-1,B,X,Y,kiosk,M,1\n",
        encoding="utf-8",
    )
    with pytest.raises(ReferenceDataError, match="1 invalid row"):
        read_csv("s.csv", Store, root=tmp_path)


def test_assets_not_in_cmdb(data, tickets):
    found = {t.asset_id for t in q.assets_not_in_cmdb(tickets, data["assets"])}
    assert found == {"POS-9791-T1", "POS-8341-T2", "POS-9177-T1", "POS-9072-T5"}


def test_soft_serve_stores_without_asset(data, tickets):
    stores = q.soft_serve_stores_without_asset(tickets, data["assets"])
    assert {"SW-0009", "SW-0037", "SW-0061", "SW-0101", "SW-0102"} <= stores.keys()


def test_version_mismatches(data, tickets):
    mism = q.version_mismatches(tickets, data["assets"])
    assert mism, "expected ticket vs CMDB version mismatches"
    assert all(t.system_version != cmdb for t, cmdb in mism)


def test_prefix_category_mismatches(tickets):
    pm = q.prefix_category_mismatches(tickets)
    assert pm
    assert all(t.category != "Online Orders" for t, _, _ in pm)


def test_foodtech_eol_conflict(data):
    claims = q.foodtech_version_claims(data["version_matrix"])
    legacy = q.legacy_eol_claims(claims)
    assert {c.source for c in legacy} == {
        "kb/system-specs/foodtech-pos.md",
        "kb/system-specs/version-matrix.csv",
        "kb/runbooks/pos-startup-failure.md",
    }
    assert {c.value for c in legacy} == {"2025-12-31", "2026-12", "2026-03-31"}
    assert all(c.version != "v4.2.3" for c in legacy)


def test_sla_sop_vs_matrix_conflict(data):
    conflicts, uniform = q.sla_conflicts(data["sla_matrix"])
    assert uniform
    assert conflicts
    assert any(c.priority == "P1" and c.measure == "resolution" for c in conflicts)


def test_report_has_all_sections(data):
    from ax15.l0_data.source_files import VerifyResult

    report = q.build_report(data, VerifyResult(total=3), {k: v for k, v in EXPECTED.items()})
    assert report.splitlines()[2].startswith("**Source integrity: 3 files verified, 0 changed")
    for needle in ("POS-9791-T1", "SW-0102", "Ticket vs CMDB version", "Asset prefix vs ticket category",
                   "end-of-life", "SLA: escalation SOP"):
        assert needle in report


@pytest.mark.db
def test_reference_counts_in_db():
    if not db_mod.configured():
        pytest.skip("no database configured")
    conn, _ = db_mod.connect()
    with conn:
        got = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in EXPECTED}
    assert got == EXPECTED
