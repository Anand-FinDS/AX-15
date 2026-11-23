"""L0-2 data-quality checks → outputs/data_quality_report.md.

Reads reference CSVs, KB files and a few ticket fields directly from DATASET_DIR
(tickets are loaded into the DB in L0-3). Never reads tickets/train_index.csv or
labels/ (no label leakage into prompts), so no evaluation-only information shapes these checks.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ax15.l0_data.models import Asset, SlaRow, Store, VersionRow
from ax15.l0_data.source_files import VerifyResult
from ax15.settings import dataset_dir

EXPECTED_COUNTS = {"stores": 120, "assets": 477, "sla_matrix": 28, "version_matrix": 21}

# Ticket category → CMDB asset prefix expected for that category.
# Online Orders has no dedicated asset class (portal is SaaS), so it is not checked.
CATEGORY_PREFIX = {
    "POS": "POS",
    "Printers": "PRN",
    "Wi-Fi / Network": "RTR",
    "Soft Serve": "SSM",
    "Kiosks": "KSK",
}
TICKET_FIELDS = ("ticket_id", "store_id", "store_name", "category", "asset_id", "system_version")


@dataclass(frozen=True)
class TicketRef:
    """Only the ticket fields the data-quality checks need."""
    ticket_id: str
    store_id: str
    store_name: str | None
    category: str
    asset_id: str | None
    system_version: str | None


def read_ticket_refs(root: Path | None = None) -> list[TicketRef]:
    # Hard rule 4: glob tickets/train/*.json (the index CSV paths point to raw/).
    root = root or dataset_dir()
    out = []
    for p in sorted((root / "tickets" / "train").glob("*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        out.append(TicketRef(**{k: d.get(k) for k in TICKET_FIELDS}))
    return out


# ---------------------------------------------------------------- ticket ↔ CMDB checks

def assets_not_in_cmdb(tickets: list[TicketRef], assets: list[Asset]) -> list[TicketRef]:
    ids = {a.asset_id for a in assets}
    return [t for t in tickets if t.asset_id and t.asset_id not in ids]


def soft_serve_stores_without_asset(
    tickets: list[TicketRef], assets: list[Asset]
) -> dict[str, list[str]]:
    """Stores with Soft Serve tickets but no SSM asset in the CMDB → {store_id: [ticket_ids]}."""
    ssm_stores = {a.store_id for a in assets if a.prefix == "SSM"}
    out: dict[str, list[str]] = defaultdict(list)
    for t in tickets:
        if t.category == "Soft Serve" and t.store_id not in ssm_stores:
            out[t.store_id].append(t.ticket_id)
    return dict(sorted(out.items()))


def version_mismatches(tickets: list[TicketRef], assets: list[Asset]) -> list[tuple[TicketRef, str]]:
    """Tickets whose system_version differs from the linked CMDB asset's version."""
    by_id = {a.asset_id: a for a in assets}
    return [
        (t, by_id[t.asset_id].system_version)
        for t in tickets
        if t.asset_id in by_id and t.system_version != by_id[t.asset_id].system_version
    ]


def versions_not_in_matrix(values: list[str | None], matrix: list[VersionRow]) -> list[str]:
    known = {f"{v.system} {v.version}" for v in matrix}
    return sorted({v for v in values if v and v not in known})


def prefix_category_mismatches(tickets: list[TicketRef]) -> list[tuple[TicketRef, str, str]]:
    """Tickets whose asset_id prefix does not match the asset class implied by the category."""
    out = []
    for t in tickets:
        expected = CATEGORY_PREFIX.get(t.category)
        if expected and t.asset_id:
            actual = t.asset_id.split("-", 1)[0]
            if actual != expected:
                out.append((t, expected, actual))
    return out


def ticket_store_mismatches(
    tickets: list[TicketRef], stores: list[Store], assets: list[Asset]
) -> list[str]:
    names = {s.store_id: s.store_name for s in stores}
    asset_store = {a.asset_id: a.store_id for a in assets}
    issues = []
    for t in tickets:
        if t.store_id not in names:
            issues.append(f"{t.ticket_id}: store {t.store_id} not in stores.csv")
        elif t.store_name and t.store_name != names[t.store_id]:
            issues.append(f"{t.ticket_id}: store_name {t.store_name!r} ≠ {names[t.store_id]!r}")
        if t.asset_id in asset_store and asset_store[t.asset_id] != t.store_id:
            issues.append(f"{t.ticket_id}: asset {t.asset_id} belongs to {asset_store[t.asset_id]}, ticket says {t.store_id}")
    return issues


# ---------------------------------------------------------------- CMDB sanity

def asset_date_issues(assets: list[Asset]) -> list[str]:
    out = []
    for a in assets:
        if a.install_date and a.last_service_date and a.last_service_date < a.install_date:
            out.append(f"{a.asset_id}: last_service_date {a.last_service_date} before install_date {a.install_date}")
        if a.install_date and a.warranty_expiry and a.warranty_expiry < a.install_date:
            out.append(f"{a.asset_id}: warranty_expiry {a.warranty_expiry} before install_date {a.install_date}")
    return out


# ---------------------------------------------------------------- KB conflicts

def _md_tables(text: str) -> list[list[dict[str, str]]]:
    """Very small markdown table parser: returns each table as a list of row dicts."""
    tables, block = [], []
    for line in text.splitlines() + [""]:
        if line.strip().startswith("|"):
            block.append(line)
            continue
        if len(block) >= 3:
            cells = [[c.strip().strip("*").strip() for c in r.strip().strip("|").split("|")] for r in block]
            header, body = cells[0], [r for r in cells[2:] if len(r) == len(cells[0])]
            tables.append([dict(zip(header, r)) for r in body])
        block = []
    return tables


@dataclass(frozen=True)
class VersionClaim:
    source: str
    version: str
    field: str
    value: str


def foodtech_version_claims(matrix: list[VersionRow], root: Path | None = None) -> list[VersionClaim]:
    """Release / EOL claims for FoodTech POS from the spec sheet and version-matrix.csv."""
    root = root or dataset_dir()
    spec = "kb/system-specs/foodtech-pos.md"
    claims: list[VersionClaim] = []
    for table in _md_tables((root / spec).read_text(encoding="utf-8")):
        if table and {"Version", "Release Date", "EOL Date"} <= table[0].keys():
            for r in table:
                claims.append(VersionClaim(spec, r["Version"], "release", r["Release Date"]))
                claims.append(VersionClaim(spec, r["Version"], "eol", r["EOL Date"]))
    vm = "kb/system-specs/version-matrix.csv"
    for v in matrix:
        if v.system != "FoodTech POS":
            continue
        if v.release_date:
            claims.append(VersionClaim(vm, v.version, "release", v.release_date.isoformat()))
        m = re.search(r"EOL\s+(?:scheduled\s+)?(\d{4}-\d{2}(?:-\d{2})?)", v.notes or "", re.I)
        if m:
            claims.append(VersionClaim(vm, v.version, "eol", m.group(1)))
    # Prose statements elsewhere in the KB, e.g. runbooks:
    # "FoodTech POS v4.2.x (legacy systems, end-of-support: 2026-03-31)"
    for p in sorted((root / "kb").rglob("*.md")):
        rel = p.relative_to(root).as_posix()
        if rel == spec:
            continue
        for m in _PROSE_EOL.finditer(p.read_text(encoding="utf-8")):
            claims.append(VersionClaim(rel, m.group(1), "eol", m.group(2)))
    return claims


_PROSE_EOL = re.compile(
    r"FoodTech POS (v\d+\.\d+\.(?:x|\d+))[^\n]*?"
    r"(?:end[- ]of[- ](?:support|life)|EOL)\s*[:\-]?\s*(\d{4}-\d{2}(?:-\d{2})?)",
    re.I,
)
# v4.2.3 has its own (later) date in the spec and is not part of the legacy-EOL conflict.
LEGACY_42 = ("v4.2.1", "v4.2.2")


def _covers(claim_version: str, version: str) -> bool:
    """'v4.2.x' covers 'v4.2.1'; an exact version covers only itself."""
    if claim_version.endswith(".x"):
        return version.startswith(claim_version[:-1])
    return claim_version == version


def legacy_eol_claims(claims: list[VersionClaim]) -> list[VersionClaim]:
    """EOL claims that apply to the legacy FoodTech v4.2.1 / v4.2.2 line."""
    return [c for c in claims if c.field == "eol" and any(_covers(c.version, v) for v in LEGACY_42)]


def conflicting(claims: list[VersionClaim], field: str) -> dict[str, list[VersionClaim]]:
    """Versions where sources disagree on `field` → {version: claims}."""
    by_ver: dict[str, list[VersionClaim]] = defaultdict(list)
    for c in claims:
        if c.field == field:
            by_ver[c.version].append(c)
    return {v: cs for v, cs in sorted(by_ver.items()) if len({c.value for c in cs}) > 1}


_UNIT_MIN = {"minute": 1, "hour": 60}


def _to_minutes(text: str) -> int | None:
    """'15 minutes' → 15, '3 hours' → 180; business-time values ('8 business hours') → None."""
    m = re.fullmatch(r"(\d+)\s+(minute|hour)s?", text.strip(), re.I)
    return int(m.group(1)) * _UNIT_MIN[m.group(2).lower()] if m else None


@dataclass(frozen=True)
class SlaConflict:
    priority: str
    measure: str          # first_response | resolution
    sop_text: str
    sop_minutes: int | None
    matrix_minutes: int


def sla_conflicts(sla: list[SlaRow], root: Path | None = None) -> tuple[list[SlaConflict], bool]:
    """Compare the SOP's L2 SLA table with sla_matrix.csv.

    Returns (conflicts, matrix_uniform) — matrix_uniform is True when every category
    has the same minutes per priority (so a single comparison per priority suffices).
    """
    root = root or dataset_dir()
    text = (root / "kb/sop/escalation-procedure.md").read_text(encoding="utf-8")
    sop_rows = []
    for table in _md_tables(text):
        if table and {"Priority", "Initial Response Time", "Resolution Target"} <= table[0].keys():
            sop_rows = table
    per_priority: dict[str, set[tuple[int, int]]] = defaultdict(set)
    for r in sla:
        per_priority[r.priority].add((r.first_response_minutes, r.resolution_target_minutes))
    uniform = all(len(v) == 1 for v in per_priority.values())

    out = []
    for r in sop_rows:
        prio = r["Priority"].split()[0]                       # "P1 – Critical" → "P1"
        if prio not in per_priority:
            continue
        first, resolution = sorted(per_priority[prio])[0]
        for measure, sop_text, matrix_min in (
            ("first_response", r["Initial Response Time"], first),
            ("resolution", r["Resolution Target"], resolution),
        ):
            sop_min = _to_minutes(sop_text)
            if sop_min != matrix_min:
                out.append(SlaConflict(prio, measure, sop_text, sop_min, matrix_min))
    return out, uniform


# ---------------------------------------------------------------- report

def _fmt_min(m: int) -> str:
    return f"{m} min" if m < 60 else (f"{m // 60} h" if m % 60 == 0 else f"{m} min")


def build_report(
    data: dict[str, list],
    integrity: VerifyResult,
    counts: dict[str, int],
    root: Path | None = None,
) -> str:
    stores, assets, sla, matrix = data["stores"], data["assets"], data["sla_matrix"], data["version_matrix"]
    tickets = read_ticket_refs(root)
    L: list[str] = []
    w = L.append

    w("# AX-15 — Data quality report (L0-2)\n")
    status = "OK" if integrity.ok else "**FAILED — original dataset has changed**"
    w(f"**Source integrity: {integrity.summary()}** — {status}\n")
    if integrity.changed or integrity.missing:
        for rel in integrity.changed:
            w(f"- changed: `{rel}`")
        for rel in integrity.missing:
            w(f"- missing: `{rel}`")
        w("")
    w(f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC from `{dataset_dir().name}/` "
      f"({len(tickets)} train tickets read for cross-checks; labels and train_index not read)._\n")

    w("## Reference load\n")
    w("| Table | Expected | Loaded | |")
    w("|---|---:|---:|---|")
    for t, exp in EXPECTED_COUNTS.items():
        w(f"| {t} | {exp} | {counts.get(t, 0)} | {'✓' if counts.get(t) == exp else '✗'} |")
    w("")

    # 1
    missing = assets_not_in_cmdb(tickets, assets)
    w(f"## 1. Ticket asset_ids not in CMDB ({len(missing)})\n")
    w("| Ticket | Store | Category | asset_id | Ticket version |")
    w("|---|---|---|---|---|")
    for t in missing:
        w(f"| {t.ticket_id} | {t.store_id} | {t.category} | `{t.asset_id}` | {t.system_version} |")
    w("")

    # 2
    ss = soft_serve_stores_without_asset(tickets, assets)
    ss_total = sum(1 for t in tickets if t.category == "Soft Serve")
    ss_linked = sum(1 for t in tickets if t.category == "Soft Serve" and (t.asset_id or "").startswith("SSM"))
    w(f"## 2. Stores reporting Soft Serve issues with no soft-serve asset in CMDB ({len(ss)} stores)\n")
    w(f"Only {ss_linked} of {ss_total} Soft Serve tickets link to an SSM asset. "
      "Five of these stores (SW-0009, 0037, 0061, 0101, 0102) are the chaos-ticket stores; "
      "the full ticket set shows more.\n")
    w("| Store | Soft Serve tickets |")
    w("|---|---|")
    for sid, tids in ss.items():
        w(f"| {sid} | {', '.join(tids)} |")
    w("")

    # 3
    vm = version_mismatches(tickets, assets)
    w(f"## 3. Ticket vs CMDB version mismatches ({len(vm)})\n")
    w("| Ticket | asset_id | Ticket says | CMDB says |")
    w("|---|---|---|---|")
    for t, cmdb in vm:
        w(f"| {t.ticket_id} | `{t.asset_id}` | {t.system_version} | {cmdb} |")
    unknown_t = versions_not_in_matrix([t.system_version for t in tickets], matrix)
    unknown_a = versions_not_in_matrix([a.system_version for a in assets], matrix)
    w("")
    w(f"Ticket versions not in version-matrix.csv: {', '.join(unknown_t) or 'none'}  ")
    w(f"CMDB versions not in version-matrix.csv: {', '.join(unknown_a) or 'none'}\n")

    # 4
    pm = prefix_category_mismatches(tickets)
    w(f"## 4. Asset prefix vs ticket category mismatches ({len(pm)})\n")
    w("Expected prefix per category: " + ", ".join(f"{c} → {p}" for c, p in CATEGORY_PREFIX.items())
      + ". Online Orders is not checked (no dedicated asset class).\n")
    by_pair: dict[tuple[str, str], list[str]] = defaultdict(list)
    for t, exp, act in pm:
        by_pair[(t.category, act)].append(t.ticket_id)
    w("| Category | Expected | Linked asset prefix | Count | Tickets |")
    w("|---|---|---|---:|---|")
    for (cat, act), tids in sorted(by_pair.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        w(f"| {cat} | {CATEGORY_PREFIX[cat]} | {act} | {len(tids)} | {', '.join(tids)} |")
    w("")

    # 5
    w("## 5. KB conflicts\n")
    claims = foodtech_version_claims(matrix, root)
    legacy = legacy_eol_claims(claims)
    sources = sorted({c.source for c in legacy})
    distinct = sorted({c.value for c in legacy})
    w(f"### 5a. FoodTech POS v4.2.x end-of-life — {len(sources)} sources, {len(distinct)} different dates\n")
    w("| Source | Applies to | EOL / end of support |")
    w("|---|---|---|")
    for c in sorted(legacy, key=lambda c: (c.source, c.version)):
        w(f"| `{c.source}` | {c.version} | {c.value} |")
    w("")
    for ver in LEGACY_42:
        cs = [c for c in legacy if _covers(c.version, ver)]
        if len({c.value for c in cs}) > 1:
            w(f"- **{ver}: sources contradict** — "
              + " vs ".join(f"{c.value} (`{c.source.split('/')[-1]}`)" for c in cs))
    v423 = [c for c in claims if c.field == "eol" and c.version == "v4.2.3"]
    if v423:
        w(f"\nNot counted: v4.2.3 is a separate version with its own date ({v423[0].value} in "
          f"`{v423[0].source.split('/')[-1]}`). The runbook's blanket \"v4.2.x\" wording would technically cover it too.")
    rel_conf = conflicting(claims, "release")
    if rel_conf:
        w("")
        w(f"Release dates also disagree for {len(rel_conf)} FoodTech versions:\n")
        w("| Version | foodtech-pos.md | version-matrix.csv |")
        w("|---|---|---|")
        for ver, cs in rel_conf.items():
            vals = {c.source.split("/")[-1]: c.value for c in cs}
            w(f"| {ver} | {vals.get('foodtech-pos.md', '—')} | {vals.get('version-matrix.csv', '—')} |")
    w("")

    conf, uniform = sla_conflicts(sla, root)
    w(f"### 5b. SLA: escalation SOP vs sla_matrix.csv ({len(conf)} disagreements)\n")
    w("sla_matrix.csv is " + ("identical across all categories" if uniform else "**not** uniform across categories")
      + ", so it is compared per priority. The SOP table is titled *L2 Response* SLAs; the matrix does not say "
      "whether its first-response/resolution columns are L1 or L2, which is itself an ambiguity to resolve.\n")
    w("| Priority | Measure | SOP (escalation-procedure.md) | sla_matrix.csv |")
    w("|---|---|---|---|")
    for c in conf:
        sop = c.sop_text + ("" if c.sop_minutes is not None else " (business time)")
        w(f"| {c.priority} | {c.measure} | {sop} | {_fmt_min(c.matrix_minutes)} |")
    w("")

    # 6
    w("## 6. Other consistency checks\n")
    ts = ticket_store_mismatches(tickets, stores, assets)
    w(f"**Ticket store / asset ownership ({len(ts)})**" + (": none" if not ts else ""))
    for i in ts:
        w(f"- {i}")
    w("")
    di = asset_date_issues(assets)
    w(f"**CMDB date anomalies ({len(di)})** — service/warranty dates before install date")
    if di:
        w("")
        w("<details><summary>show</summary>\n")
        for i in di:
            w(f"- {i}")
        w("\n</details>")
    w("")
    return "\n".join(L)
