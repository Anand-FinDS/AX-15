"""L0-4: chaos labels + seeded dev/held-out split.

    python -m ax15.l0_data.eval_assets

1. Chaos labels: parses eval/chaos_expected_behavior.md (our authored
   expected behaviour for the 36 adversarial tickets) -> eval/chaos_labels.json -> eval.chaos_labels.
   chaos_type is the doc's section heading, cross-checked against train_index.csv.
2. Split: the 220 labelled train tickets, ~80/20 dev/held-out, stratified by category + routing,
   fixed seed -> eval/splits.json -> eval.eval_splits. The 36 chaos tickets are the adversarial dev
   set (per the chaos doc) and are not part of this split.

Everything here is evaluation-only (no label leakage into prompts). Tuning scripts read split = 'dev' only; the
held-out set is scored once, at the end.
"""
from __future__ import annotations

import csv
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

from ax15 import db
from ax15.settings import ROOT, dataset_dir

CHAOS_DOC = ROOT / "eval" / "chaos_expected_behavior.md"
CHAOS_JSON = ROOT / "eval" / "chaos_labels.json"
SPLITS_JSON = ROOT / "eval" / "splits.json"
SEED = 42
HELDOUT_SHARE = 0.20
SPLIT_VERSION = f"v1-seed{SEED}"
ROUTING_CLASSES = {"l1_self_service", "l1_guided", "l2_escalation", "non_it", "needs_clarification"}

_ENTRY_RE = re.compile(r"^\*\*(INC-\d{5})\*\*")
_SECTION_RE = re.compile(r"^## (\w+)")
_BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
_CONF_RE = re.compile(r"\(([HML])[;)]")


# --- chaos labels ---------------------------------------------------------------------------

def parse_chaos_doc(text: str) -> list[dict]:
    """One record per **INC-xxxxx** entry, in its '## <chaos_type>' section."""
    entries: list[dict] = []
    section, note, cur = None, [], None
    for line in text.splitlines():
        if m := _SECTION_RE.match(line):
            section, note, cur = m.group(1), [], None
            continue
        if line.startswith("---") or section is None:
            continue
        if m := _ENTRY_RE.match(line):
            cur = {"ticket_id": m.group(1), "chaos_type": section, "lines": [line], "section_note": " ".join(note)}
            entries.append(cur)
        elif cur is not None:
            if line.strip():
                cur["lines"].append(line)
        elif line.strip():
            note.append(line.strip())
    out = []
    for e in entries:
        body = "\n".join(e["lines"]).strip()
        outcome = None                   # last bold span made only of routing classes / "verify"
        for span in _BOLD_RE.findall(body):
            tokens = [t.strip() for t in re.split(r"[+/]", span)]
            if tokens and all(t in ROUTING_CLASSES | {"verify"} for t in tokens):
                outcome = tokens
        conf = _CONF_RE.findall(body)
        if outcome is None or not conf:
            raise ValueError(f"{e['ticket_id']}: no bold routing outcome with (H/M/L) confidence")
        tokens = outcome
        out.append({
            "ticket_id": e["ticket_id"],
            "chaos_type": e["chaos_type"],
            "expected_routing": [t for t in tokens if t in ROUTING_CLASSES],
            "expected_mode": "verify" if "verify" in tokens else "handle",
            "confidence": conf[-1],
            "expected_behavior": body,
            "section_note": e["section_note"] or None,
        })
    return out


def index_chaos_types(root: Path | None = None) -> dict[str, str]:
    with ((root or dataset_dir()) / "tickets" / "train_index.csv").open(encoding="utf-8", newline="") as f:
        return {r["ticket_id"]: r["chaos_type"] for r in csv.DictReader(f) if r["chaos_type"]}


def build_chaos_labels() -> list[dict]:
    labels = parse_chaos_doc(CHAOS_DOC.read_text(encoding="utf-8"))
    idx = index_chaos_types()
    ids = {l["ticket_id"] for l in labels}
    if ids != set(idx):
        raise ValueError(f"chaos doc vs train_index mismatch: only in doc {sorted(ids - set(idx))}, "
                         f"only in index {sorted(set(idx) - ids)}")
    bad = [l["ticket_id"] for l in labels if l["chaos_type"] != idx[l["ticket_id"]]]
    if bad:
        raise ValueError(f"chaos_type differs from train_index for {bad}")
    return labels


def load_chaos(conn, labels: list[dict]) -> int:
    uid = _uids(conn)
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO eval.chaos_labels (ticket_uid, chaos_type, expected_behavior, expected_routing,"
            " expected_mode, confidence, raw) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)"
            " ON CONFLICT (ticket_uid) DO UPDATE SET chaos_type = EXCLUDED.chaos_type,"
            " expected_behavior = EXCLUDED.expected_behavior, expected_routing = EXCLUDED.expected_routing,"
            " expected_mode = EXCLUDED.expected_mode, confidence = EXCLUDED.confidence, raw = EXCLUDED.raw,"
            " loaded_at = now()",
            [(uid[l["ticket_id"]], l["chaos_type"], l["expected_behavior"], l["expected_routing"],
              l["expected_mode"], l["confidence"], json.dumps(l)) for l in labels],
        )
    return conn.execute("SELECT count(*) FROM eval.chaos_labels").fetchone()[0]


# --- split ----------------------------------------------------------------------------------

def stratified_split(items: list[tuple[str, str]], share: float = HELDOUT_SHARE, seed: int = SEED
                     ) -> tuple[list[str], list[str]]:
    """items = (ticket_id, stratum). Held-out total = round(share * N), allocated per stratum by
    largest remainder; members chosen by a seeded shuffle. Deterministic for a given seed."""
    rng = random.Random(seed)
    strata: dict[str, list[str]] = defaultdict(list)
    for tid, s in sorted(items):
        strata[s].append(tid)
    keys = sorted(strata)
    for k in keys:
        rng.shuffle(strata[k])
    target = round(share * len(items))
    quota = {k: int(share * len(strata[k])) for k in keys}
    rest = sorted(keys, key=lambda k: (-(share * len(strata[k]) - quota[k]), k))
    for k in rest[: target - sum(quota.values())]:
        quota[k] += 1
    held = sorted(t for k in keys for t in strata[k][: quota[k]])
    dev = sorted(t for k in keys for t in strata[k][quota[k]:])
    return dev, held


def build_split(conn) -> dict:
    rows = conn.execute(
        "SELECT t.ticket_id, t.category, l.correct_routing FROM eval.eval_labels l"
        " JOIN tickets t USING (ticket_uid) WHERE t.batch_id = 'train' ORDER BY t.ticket_id"
    ).fetchall()
    strata = {tid: f"{cat}|{routing}" for tid, cat, routing in rows}
    dev, held = stratified_split(list(strata.items()))
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"dev": 0, "heldout": 0})
    for t in dev:
        counts[strata[t]]["dev"] += 1
    for t in held:
        counts[strata[t]]["heldout"] += 1
    return {"split_version": SPLIT_VERSION, "seed": SEED, "heldout_share": HELDOUT_SHARE,
            "stratified_by": "category + correct_routing", "population": "220 labelled train tickets",
            "strata": dict(sorted(counts.items())), "dev": dev, "heldout": held}


def load_split(conn, split: dict) -> dict[str, int]:
    uid = _uids(conn)
    rows = [(split["split_version"], uid[t], name, split["seed"]) for name in ("dev", "heldout") for t in split[name]]
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM eval.eval_splits WHERE split_version = %s", (split["split_version"],))
        cur.executemany("INSERT INTO eval.eval_splits (split_version, ticket_uid, split, seed) VALUES (%s, %s, %s, %s)", rows)
    return dict(conn.execute("SELECT split, count(*) FROM eval.eval_splits WHERE split_version = %s GROUP BY 1",
                             (split["split_version"],)).fetchall())


def _uids(conn) -> dict[str, int]:
    return dict(conn.execute(
        "SELECT ticket_id, ticket_uid FROM tickets WHERE source = 'train' AND batch_id = 'train'").fetchall())


def _write(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    chaos = build_chaos_labels()
    _write(CHAOS_JSON, chaos)
    conn, var = db.connect()
    with conn:
        n_chaos = load_chaos(conn, chaos)
        split = build_split(conn)
        _write(SPLITS_JSON, split)
        sizes = load_split(conn, split)
    print(f"[eval-assets] connected via {var}; chaos labels: {len(chaos)} parsed, {n_chaos} in eval.chaos_labels")
    print(f"[eval-assets] split {split['split_version']}: dev={sizes.get('dev', 0)} heldout={sizes.get('heldout', 0)}"
          f" ({len(split['strata'])} strata: category + routing)")
    return 0 if n_chaos == 36 and sum(sizes.values()) == 220 else 1


if __name__ == "__main__":
    sys.exit(main())
