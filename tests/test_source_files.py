"""Hard rule 1 guard: the original dataset must not change.

The DB test fails if any file under DATASET_DIR has a different sha256 from the
baseline in public.source_files, or if files were added or removed.
Baseline is written by `python -m ax15.l0_data.reference`.
"""
from __future__ import annotations

import pytest

from ax15 import db as db_mod
from ax15.l0_data.source_files import baseline, compare, hash_tree


def test_compare_detects_changes(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("b")
    base = {k: v.sha256 for k, v in hash_tree(tmp_path).items()}
    assert set(base) == {"a.txt", "sub/b.txt"}

    (tmp_path / "a.txt").write_text("A")
    (tmp_path / "sub" / "b.txt").unlink()
    (tmp_path / "c.txt").write_text("c")
    r = compare(hash_tree(tmp_path), base)
    assert (r.changed, r.missing, r.new) == (["a.txt"], ["sub/b.txt"], ["c.txt"])
    assert not r.ok


@pytest.mark.db
def test_original_dataset_unchanged():
    if not db_mod.configured():
        pytest.skip("no database configured")
    conn, _ = db_mod.connect()
    with conn:
        base = baseline(conn)
    assert base, "no baseline in source_files — run: python -m ax15.l0_data.reference"
    r = compare(hash_tree(), base)
    assert not r.changed, f"original dataset files changed: {r.changed}"
    assert not r.missing, f"original dataset files missing: {r.missing}"
    assert not r.new, f"files added to the original dataset: {r.new}"
