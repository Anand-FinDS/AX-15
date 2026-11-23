"""Integrity manifest for the read-only original dataset (the original dataset is read-only).

Every file under DATASET_DIR is hashed (sha256 of raw bytes). The first time a
file is seen its hash becomes the baseline in `source_files`; later runs only
bump `last_verified_at` and never overwrite the baseline, so any change stays
visible until someone deliberately re-baselines.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from ax15.settings import dataset_dir


@dataclass(frozen=True)
class FileHash:
    rel_path: str
    sha256: str
    size_bytes: int


@dataclass
class VerifyResult:
    total: int = 0
    unchanged: list[str] = field(default_factory=list)
    new: list[str] = field(default_factory=list)          # baselined this run
    changed: list[str] = field(default_factory=list)      # hash differs from baseline
    missing: list[str] = field(default_factory=list)      # in baseline, not on disk

    @property
    def ok(self) -> bool:
        return not (self.changed or self.missing)

    def summary(self) -> str:
        s = f"{self.total} files verified, {len(self.changed)} changed"
        extra = []
        if self.missing:
            extra.append(f"{len(self.missing)} missing")
        if self.new:
            extra.append(f"{len(self.new)} newly baselined")
        return s + (f" ({', '.join(extra)})" if extra else "")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def hash_tree(root: Path | None = None) -> dict[str, FileHash]:
    """Hash every regular file under root. Keys are POSIX paths relative to root."""
    root = root or dataset_dir()
    out: dict[str, FileHash] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = p.relative_to(root).as_posix()
            out[rel] = FileHash(rel, sha256_file(p), p.stat().st_size)
    return out


def baseline(conn: psycopg.Connection) -> dict[str, str]:
    return dict(conn.execute("SELECT rel_path, sha256 FROM source_files").fetchall())


def compare(current: dict[str, FileHash], base: dict[str, str]) -> VerifyResult:
    r = VerifyResult(total=len(current))
    for rel, fh in current.items():
        if rel not in base:
            r.new.append(rel)
        elif base[rel] == fh.sha256:
            r.unchanged.append(rel)
        else:
            r.changed.append(rel)
    r.missing = sorted(set(base) - set(current))
    return r


def verify_and_record(conn: psycopg.Connection, root: Path | None = None) -> VerifyResult:
    """Compare files to the stored baseline; baseline new files; mark unchanged files verified."""
    current = hash_tree(root)
    with conn.transaction():
        result = compare(current, baseline(conn))
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO source_files (rel_path, sha256, size_bytes, last_verified_at)"
                " VALUES (%s, %s, %s, now()) ON CONFLICT (rel_path) DO NOTHING",
                [(r, current[r].sha256, current[r].size_bytes) for r in result.new],
            )
            if result.unchanged:
                cur.execute(
                    "UPDATE source_files SET last_verified_at = now() WHERE rel_path = ANY(%s)",
                    (result.unchanged,),
                )
    return result
