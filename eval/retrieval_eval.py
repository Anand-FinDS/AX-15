"""L1-6b/6c/6d: doc-level routing eval — do we pick the right PRIMARY runbook?

    python eval/retrieval_eval.py              # routing variants only (no LLM)
    python eval/retrieval_eval.py --picker     # + locked pipeline: route_doc top-3 -> pick_doc (cached)
    python eval/retrieval_eval.py --margins    # + margin histograms / threshold tables (off by default)

Locked routing (L1-6d): dense search, top 20 chunks, +0.005 per extra chunk, top 3 runbooks
(search.route_doc defaults), then pick_doc chooses one or none. Three outcomes per ticket:
right (picked a primary runbook) / escalated (picked none) / wrong (picked another runbook).

Leakage guard (no label leakage into prompts): the query, the category filter/boost and the picker input come
only from the public tickets table (subject + description; category). Labels are read in a
separate query and joined only after routing, for scoring.

Scope: the dev split from eval.eval_splits (L0-4). The held-out split is never read here
except for its size; it is scored once, at the end. If no dev split exists, all labels are
used and the report says so (WARNING).

Primary runbook = relevant_kb_docs entries under runbooks/ (hit if any is at rank <= k; MRR
uses the best rank). Pilot scope = tickets whose main (first-listed) runbook is runbooks/pos-*
or runbooks/printer-*. Writes outputs/retrieval_eval.md.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

from ax15 import db
from ax15.config import base_llm
from ax15.l1_knowledge.picker import Pick, PickCache, pick_doc
from ax15.l1_knowledge.search import (ROUTE_COUNT_BONUS, ROUTE_DOC_PREFIX, ROUTE_LEGACY_BONUS, ROUTE_MODE,
                                      ROUTE_TOP_DOCS, ROUTE_TOP_K, RouteResult, embed_queries, rank_docs,
                                      search_kb, ticket_query)
from ax15.settings import OUTPUTS_DIR, ROOT

AUDIENCE = "store"
WORKERS = 8
REPORT = OUTPUTS_DIR / "retrieval_eval.md"
PICKER_CACHE = OUTPUTS_DIR / "picker_cache.json"
PILOT_PREFIXES = ("runbooks/pos-", "runbooks/printer-")
LOCKED = "locked: dense_b005 top-3 runbooks"


@dataclass(frozen=True)
class Variant:
    mode: str
    hard_category: bool = False
    bonus: float = ROUTE_LEGACY_BONUS
    soft_boost: float = 0.0
    doc_prefix: str | None = None
    n_docs: int | None = None


VARIANTS = {
    LOCKED: Variant(ROUTE_MODE, bonus=ROUTE_COUNT_BONUS, doc_prefix=ROUTE_DOC_PREFIX, n_docs=ROUTE_TOP_DOCS),
    "dense":               Variant("dense"),
    "fts":                 Variant("fts"),
    "hybrid":              Variant("hybrid"),
    "hybrid+category":     Variant("hybrid", hard_category=True),
    "hybrid_b005":         Variant("hybrid", bonus=0.005),
    "dense_b005":          Variant("dense", bonus=0.005),
    "hybrid_b005_softcat": Variant("hybrid", bonus=0.005, soft_boost=0.005),
    "hybrid_b0":           Variant("hybrid", bonus=0.0),
    "dense_b0":            Variant("dense", bonus=0.0),
}

# Agent-input fields only. No join to eval.* here.
TICKET_SQL = ("SELECT ticket_uid, ticket_id, category, subject, description FROM tickets"
              " WHERE source = 'train' AND batch_id = 'train' ORDER BY ticket_id")
DOC_CATEGORY_SQL = "SELECT doc_path, category FROM kb_documents"
LABEL_SQL = "SELECT ticket_uid, relevant_kb_docs FROM eval.eval_labels"
DEV_SQL = "SELECT ticket_uid FROM eval.eval_splits WHERE split = 'dev'"
HELDOUT_SIZE_SQL = "SELECT count(*) FROM eval.eval_splits WHERE split = 'heldout'"   # size only, never members

MARGIN_BINS = [0.0, 0.001, 0.0025, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.3, 0.5]
THRESHOLDS = MARGIN_BINS
TARGET_PRECISION = 0.95

_local = threading.local()


def _conn():
    if not hasattr(_local, "conn"):
        _local.conn, _ = db.connect(autocommit=True)
    return _local.conn


def build_query(subject: str | None, description: str | None) -> str:
    """Routing (and picker) query: ticket subject + description only."""
    return ticket_query(subject, description)


@dataclass
class Row:
    ticket_id: str
    ticket_text: str
    primary: list[str]
    sop: list[str]
    faq: list[str]
    route: RouteResult

    @property
    def rank(self) -> int | None:
        ranks = [i for i, d in enumerate(self.route.top(len(self.route.docs)), 1) if d in self.primary]
        return min(ranks) if ranks else None

    def hit(self, k: int) -> bool:
        return self.rank is not None and self.rank <= k

    @property
    def pilot(self) -> bool:
        return bool(self.primary) and self.primary[0].startswith(PILOT_PREFIXES)


def split_docs(relevant: list[str]) -> tuple[list[str], list[str], list[str]]:
    return ([d for d in relevant if d.startswith("runbooks/")],
            [d for d in relevant if d.startswith("sop/")],
            [d for d in relevant if d.startswith("faq/")])


# --- three-outcome scoring --------------------------------------------------------------------

OUTCOMES = ("right", "escalated", "wrong")


def outcome(picked: str | None, primary: list[str]) -> str:
    """right = picked a primary runbook; escalated = picked none; wrong = picked something else."""
    if picked is None:
        return "escalated"
    return "right" if picked in primary else "wrong"


def outcome_counts(pairs: list[tuple[str | None, list[str]]]) -> dict[str, int]:
    c = Counter(outcome(p, prim) for p, prim in pairs)
    return {o: c.get(o, 0) for o in OUTCOMES}


# --- routing metrics ------------------------------------------------------------------------

def metrics(rows: list[Row]) -> dict:
    p = [r for r in rows if r.primary]
    n = len(p) or 1
    out = {"n": len(p)}
    for k in (1, 3, 5):
        out[f"doc@{k}"] = sum(r.hit(k) for r in p) / n
    out["mrr"] = sum(1 / r.rank for r in p if r.rank) / n
    for kind in ("sop", "faq"):
        rel = [r for r in rows if getattr(r, kind)]
        out[kind] = (sum(any(d in r.route.top(5) for d in getattr(r, kind)) for r in rel), len(rel))
    return out


def histogram(rows: list[Row]) -> list[tuple[str, int, int]]:
    p = [r for r in rows if r.primary]
    out = []
    for i, lo in enumerate(MARGIN_BINS):
        hi = MARGIN_BINS[i + 1] if i + 1 < len(MARGIN_BINS) else None
        inb = [r for r in p if r.route.margin >= lo and (hi is None or r.route.margin < hi)]
        if inb:
            label = f"{lo:.4f} – {hi:.4f}" if hi is not None else f"≥ {lo:.4f}"
            out.append((label, sum(r.hit(1) for r in inb), sum(not r.hit(1) for r in inb)))
    return out


def threshold_table(rows: list[Row]) -> list[tuple[float, float, float, int]]:
    p = [r for r in rows if r.primary]
    out = []
    for t in THRESHOLDS:
        above = [r for r in p if r.route.margin >= t]
        if above:
            acc = sum(r.hit(1) for r in above) / len(above)
            out.append((t, len(above) / len(p), acc, sum(not r.hit(1) for r in above)))
    return out


def suggest_threshold(rows: list[Row]) -> tuple[float, float, float] | None:
    return next(((t, c, a) for t, c, a, _ in threshold_table(rows) if a >= TARGET_PRECISION), None)


def route() -> tuple[dict[str, list[Row]], str, int, int]:
    conn, _ = db.connect(autocommit=True)
    tickets = conn.execute(TICKET_SQL).fetchall()
    doc_cat = dict(conn.execute(DOC_CATEGORY_SQL).fetchall())
    labels = dict(conn.execute(LABEL_SQL).fetchall())
    dev = {u for (u,) in conn.execute(DEV_SQL).fetchall()}
    heldout_n = conn.execute(HELDOUT_SIZE_SQL).fetchone()[0]
    conn.close()
    scope = "dev split" if dev else "all labelled tickets"
    keep = dev if dev else set(labels)
    tickets = [t for t in tickets if t[0] in keep and t[0] in labels]

    queries = [build_query(t[3], t[4]) for t in tickets]        # agent-input fields only
    vecs = embed_queries(queries)
    print(f"[eval] {len(tickets)} tickets ({scope}); held-out size {heldout_n} (not scored)")

    searches = {(v.mode, v.hard_category) for v in VARIANTS.values()}

    def one(args):
        t, q, v = args
        return {(m, f): search_kb(_conn(), q, k=ROUTE_TOP_K, audience=AUDIENCE, mode=m,
                                  query_embedding=None if m == "fts" else v, category=t[2] if f else None)
                for m, f in searches}

    with ThreadPoolExecutor(WORKERS) as pool:
        hits = list(pool.map(one, zip(tickets, queries, vecs)))

    results: dict[str, list[Row]] = {}
    for name, v in VARIANTS.items():
        rows = []
        for t, q, h in zip(tickets, queries, hits):
            r = rank_docs(h[(v.mode, v.hard_category)], AUDIENCE, v.bonus, doc_cat,
                          t[2] if v.soft_boost else None, v.soft_boost, v.doc_prefix, v.n_docs)
            rows.append(Row(t[1], q, *split_docs(list(labels[t[0]])), r))   # labels joined only now
        results[name] = rows
        m = metrics(rows)
        print(f"[eval] {name:<34} doc@1={m['doc@1']:.3f} doc@3={m['doc@3']:.3f} MRR={m['mrr']:.3f}")
    return results, scope, len(tickets), heldout_n


@dataclass
class PickRow:
    row: Row
    candidates: list[str]
    pick: Pick

    @property
    def outcome(self) -> str:
        return outcome(self.pick.doc, self.row.primary)


def run_picker(rows: list[Row]) -> list[PickRow]:
    cache = PickCache(PICKER_CACHE)
    rows = [r for r in rows if r.primary]

    def one(r: Row) -> PickRow:
        cands = r.route.top(ROUTE_TOP_DOCS)
        return PickRow(r, cands, pick_doc(r.ticket_text, cands, cache=cache))

    try:
        with ThreadPoolExecutor(WORKERS) as pool:
            out = list(pool.map(one, rows))
    finally:
        cache.save()
    return out


# --- 2.0 dual-query routing (raw ticket text + classify's clean symptom) -------------------------

AGENT_CACHE = OUTPUTS_DIR / "agent_eval_cache" / "v2"


def load_symptoms() -> tuple[dict[str, str], str]:
    """clean_symptom per ticket from the newest cached 2.0 agent-eval run of that ticket (the symptom comes
    from the classify node only, so later policy/guard code changes don't affect it)."""
    newest: dict[str, tuple[float, str, str]] = {}
    for p in AGENT_CACHE.glob("*.json"):
        d = json.loads(p.read_text(encoding="utf-8"))
        sym = (d.get("classify") or {}).get("clean_symptom")
        if sym and (d["ticket_id"] not in newest or p.stat().st_mtime > newest[d["ticket_id"]][0]):
            newest[d["ticket_id"]] = (p.stat().st_mtime, sym, p.stem.rsplit("_", 1)[-1])
    hashes = sorted({h for _, _, h in newest.values()})
    return {t: s for t, (_, s, _) in newest.items()}, ", ".join(hashes)


@dataclass
class DualRow:
    row: Row
    symptom: str | None
    candidates: list[dict]
    pick: Pick
    routing_query: str | None

    @property
    def outcome(self) -> str:
        return outcome(self.pick.doc, self.row.primary)


def run_dual(rows: list[Row], symptoms: dict[str, str]) -> list[DualRow]:
    """Same tickets and picker as the locked pipeline; candidates = merge(route_doc(raw), route_doc(symptom))."""
    from ax15.agents.v2_graph import merge_candidates, routing_query_of
    from ax15.l1_knowledge.search import route_doc
    cache = PickCache(PICKER_CACHE)
    rows = [r for r in rows if r.primary]

    def one(r: Row) -> DualRow:
        sym = symptoms.get(r.ticket_id)
        sym_docs = route_doc(_conn(), sym, {"audience": AUDIENCE}).docs if sym else []
        cands = merge_candidates(r.route.docs, sym_docs, 5)
        pick = pick_doc(r.ticket_text, [c["doc"] for c in cands], cache=cache)
        rq = routing_query_of(next(c["found_by"] for c in cands if c["doc"] == pick.doc)) if pick.doc else None
        return DualRow(r, sym, cands, pick, rq)

    try:
        with ThreadPoolExecutor(WORKERS) as pool:
            out = list(pool.map(one, rows))
    finally:
        cache.save()
    return out


def dual_section(dual: list[DualRow], picks: list[PickRow], n_sym: int, phash: str) -> list[str]:
    base = {p.row.ticket_id: p for p in picks}
    lines = ["## 2.0 dual-query routing vs locked baseline", "",
             f"2.0 runs `route_doc` on the raw subject + description **and** on classify's `clean_symptom`, merges the "
             f"candidates (dedupe, best score, max 5) and gives them all to the same `pick_doc`. Symptoms come from the "
             f"2.0 agent-eval cache (prompt hash `{phash}`): {n_sym} of {len(dual)} tickets have one (the rest route "
             "on the raw text only, same as the baseline).", "",
             "| Scope | Pipeline | n | Right | Escalated (none) | Wrong |", "|---|---|---:|---:|---:|---:|"]
    for label, sel in (("overall", lambda r: True), ("pilot", lambda r: r.pilot)):
        for name, items in (("locked (raw only, top 3)", [(p.pick.doc, p.row) for p in picks]),
                            ("**2.0 dual query (top 5)**", [(d.pick.doc, d.row) for d in dual])):
            its = [(doc, r) for doc, r in items if sel(r)]
            c = outcome_counts([(doc, r.primary) for doc, r in its])
            n = len(its) or 1
            lines.append(f"| {label} | {name} | {len(its)} | {c['right']} ({_pct(c['right'] / n)}) | "
                         f"{c['escalated']} ({_pct(c['escalated'] / n)}) | {c['wrong']} ({_pct(c['wrong'] / n)}) |")
    rq = Counter(d.routing_query for d in dual if d.outcome == "right")
    lines += ["", f"Which query found the chosen runbook (right picks): raw only {rq.get('raw', 0)} · symptom only "
              f"{rq.get('symptom', 0)} · both {rq.get('both', 0)}.", ""]
    changed = [d for d in dual if d.row.ticket_id in base and d.outcome != base[d.row.ticket_id].outcome]
    better = [d for d in changed if d.outcome == "right" or (d.outcome == "escalated"
                                                            and base[d.row.ticket_id].outcome == "wrong")]
    lines += [f"### Outcome changed vs locked ({len(changed)}: {len(better)} better, {len(changed) - len(better)} worse)",
              "", "| Ticket | Pilot | Expected | Locked pick → outcome | Dual pick → outcome | Found by | Symptom |",
              "|---|---|---|---|---|---|---|"]
    for d in sorted(changed, key=lambda x: x.row.ticket_id):
        b = base[d.row.ticket_id]
        lines.append(f"| {d.row.ticket_id} | {'yes' if d.row.pilot else ''} | "
                     f"{', '.join(_short(x) for x in d.row.primary)} | {_short(b.pick.doc)} → {b.outcome} | "
                     f"{_short(d.pick.doc)} → {d.outcome} | {d.routing_query or '—'} | "
                     f"{(d.symptom or '—').replace('|', '/')} |")
    new_tokens = sum(d.pick.input_tokens for d in dual if not d.pick.cached)
    lines += ["", f"- Picker calls not served from cache: {sum(not d.pick.cached for d in dual)} "
              f"({new_tokens:,} input tokens).", ""]
    return lines


def _pct(x: float) -> str:
    return f"{x:.1%}"


def _short(d: str | None) -> str:
    return "none" if d is None else d.removeprefix("runbooks/").removesuffix(".md")


def _variant_table(results: dict[str, list[Row]], pilot: bool) -> list[str]:
    lines = ["| Variant | n | doc@1 | doc@3 | doc@5 | MRR | SOP hit@5 | FAQ hit@5 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for v, rows in results.items():
        m = metrics([r for r in rows if r.pilot] if pilot else rows)
        name = f"**{v}**" if v == LOCKED else v
        lines.append(f"| {name} | {m['n']} | {_pct(m['doc@1'])} | {_pct(m['doc@3'])} | {_pct(m['doc@5'])} | "
                     f"{m['mrr']:.3f} | {m['sop'][0]}/{m['sop'][1]} | {m['faq'][0]}/{m['faq'][1]} |")
    return lines


def outcome_table(picks: list[PickRow], base_rows: list[Row]) -> list[str]:
    """Three outcomes for the pipeline (route_doc -> pick_doc) vs routing rank 1 alone."""
    lines = ["| Scope | n | Right | Escalated (none) | Wrong | Routing rank 1 alone: right / wrong |",
             "|---|---:|---:|---:|---:|---:|"]
    for label, sel in (("overall", lambda r: True), ("pilot", lambda r: r.pilot)):
        ps = [p for p in picks if sel(p.row)]
        c = outcome_counts([(p.pick.doc, p.row.primary) for p in ps])
        b = outcome_counts([(r.route.top(1)[0] if r.route.docs else None, r.primary)
                            for r in base_rows if r.primary and sel(r)])
        n = len(ps) or 1
        lines.append(f"| {label} | {len(ps)} | {c['right']} ({_pct(c['right'] / n)}) | "
                     f"{c['escalated']} ({_pct(c['escalated'] / n)}) | {c['wrong']} ({_pct(c['wrong'] / n)}) | "
                     f"{_pct(b['right'] / n)} / {_pct(b['wrong'] / n)} |")
    return lines


def picker_cost(picks: list[PickRow]) -> dict:
    price = base_llm().get("price_per_mtok", {"input": 0.0, "output": 0.0})
    inp = sum(p.pick.input_tokens for p in picks)
    out = sum(p.pick.output_tokens for p in picks)
    return {"in": inp, "out": out, "cost": inp / 1e6 * price["input"] + out / 1e6 * price["output"],
            "cached": sum(p.pick.cached for p in picks), "invalid": sum(not p.pick.valid for p in picks),
            "retried": sum(p.pick.attempts == 2 for p in picks)}


def render(results: dict[str, list[Row]], scope: str, n: int, heldout_n: int,
           picks: list[PickRow] | None, margins: bool, dual: list[str] | None = None) -> str:
    lines = ["# AX-15 — Doc-level routing eval (L1-6b … L1-6d)", ""]
    lines.append(f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · scope: **{scope}** · "
                 f"**{n} tickets scored** · held-out: **{heldout_n} tickets (not scored)** · "
                 f"audience `{AUDIENCE}` · docs ranked from top {ROUTE_TOP_K} chunks._")
    lines.append("")
    if scope != "dev split":
        lines += ["> **WARNING:** no dev split in `eval.eval_splits`, so this uses **all labelled train "
                  "tickets**. Re-check on the dev/held-out split once it exists.", ""]
    rows0 = results[LOCKED]
    no_rb = sum(1 for r in rows0 if not r.primary)
    n_pilot = sum(1 for r in rows0 if r.pilot)
    lines += [
        f"**Locked routing:** `route_doc` defaults: {ROUTE_MODE} search, top {ROUTE_TOP_K} chunks, doc_score = "
        f"max chunk RRF + {ROUTE_COUNT_BONUS} × (extra chunks), top {ROUTE_TOP_DOCS} `{ROUTE_DOC_PREFIX}` docs; "
        "then `pick_doc` picks one or none. Query = ticket subject + description only. Primary runbook = "
        f"`relevant_kb_docs` entries under `runbooks/`; {no_rb} ticket(s) with no runbook label excluded. "
        f"Pilot scope = main runbook `pos-*` / `printer-*` (n = {n_pilot}).", "",
    ]
    if picks is not None:
        s = picker_cost(picks)
        lines += ["## Locked pipeline: three outcomes (route_doc → pick_doc)", "",
                  *outcome_table(picks, rows0), "",
                  f"- **Right** = picked a primary runbook · **Escalated** = picked none (no candidate fits, "
                  "hand to L2) · **Wrong** = picked another runbook (the costly case).",
                  f"- Picker `{base_llm()['model']}`: {s['in']:,} in / {s['out']:,} out tokens, "
                  f"**${s['cost']:.4f}**; {s['cached']} of {len(picks)} from cache (`outputs/{PICKER_CACHE.name}`); "
                  f"{s['invalid']} invalid after retry; {s['retried']} retried.", ""]
        for title, kind in (("Wrong picks", "wrong"), ("Escalated (none)", "escalated")):
            sel = [p for p in picks if p.outcome == kind]
            lines += [f"### {title} ({len(sel)})", "",
                      "| Ticket | Pilot | Expected runbook(s) | Candidates | Picked | Reason |", "|---|---|---|---|---|---|"]
            lines += [f"| {p.row.ticket_id} | {'yes' if p.row.pilot else ''} | "
                      f"{', '.join(_short(d) for d in p.row.primary)} | {', '.join(_short(c) for c in p.candidates)} | "
                      f"{_short(p.pick.doc)} | {p.pick.reason.replace('|', '/')} |" for p in sel]
            lines.append("")

    if dual:
        lines += dual
    lines += ["## Routing variants (no LLM) — all tickets in scope", "", *_variant_table(results, False), "",
              f"## Routing variants — pilot scope (n = {n_pilot})", "", *_variant_table(results, True), "",
              "Variants other than the locked one rank all doc types (runbooks, FAQs, specs, SOPs); the locked "
              "one ranks runbooks only and returns 3, so its doc@5 equals doc@3.", ""]

    if margins:
        lines += ["## Margin: hits vs misses at doc@1", ""]
        for v, rows in results.items():
            lines += [f"### {v}", "", "| Margin | Hits | Misses |", "|---|---:|---:|"]
            lines += [f"| {lbl} | {h} | {m} |" for lbl, h, m in histogram(rows)]
            lines += ["", "| Threshold (margin ≥) | Covered | doc@1 among covered | Misses |", "|---:|---:|---:|---:|"]
            lines += [f"| {t:.4f} | {_pct(c)} | {_pct(a)} | {mi} |" for t, c, a, mi in threshold_table(rows)]
            s = suggest_threshold(rows)
            lines += ["", "Suggested threshold: " + (f"**{s[0]:.4f}** ({_pct(s[1])} covered, {_pct(s[2])} doc@1)."
                                                  if s else "none reaches the target."), ""]

    lines += [f"## Routing misses: {LOCKED} (primary runbook not at rank 1)", "",
              "| Ticket | Expected runbook(s) | Rank | Top-3 routed | Margin |", "|---|---|---:|---|---:|"]
    misses = sorted((r for r in rows0 if r.primary and not r.hit(1)), key=lambda r: -r.route.margin)
    lines += [f"| {r.ticket_id} | {', '.join(_short(d) for d in r.primary)} | {r.rank or '—'} | "
              f"{', '.join(_short(d) for d in r.route.top(3))} | {r.route.margin:.4f} |" for r in misses]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--picker", action="store_true", help="run the locked pipeline incl. pick_doc (cached)")
    ap.add_argument("--margins", action="store_true", help="include margin histograms / threshold tables")
    ap.add_argument("--dual", action="store_true", help="also run 2.0 dual-query routing (needs the v2 agent-eval cache)")
    args = ap.parse_args()
    results, scope, n, heldout_n = route()
    picks = None
    dual_lines = None
    if args.picker or args.dual:
        picks = run_picker(results[LOCKED])
        c = outcome_counts([(p.pick.doc, p.row.primary) for p in picks])
        print(f"[eval] pipeline: right={c['right']} escalated={c['escalated']} wrong={c['wrong']} "
              f"cost=${picker_cost(picks)['cost']:.4f}")
    if args.dual:
        symptoms, ph = load_symptoms()
        dual = run_dual(results[LOCKED], symptoms)
        c = outcome_counts([(d.pick.doc, d.row.primary) for d in dual])
        print(f"[eval] dual query: right={c['right']} escalated={c['escalated']} wrong={c['wrong']} "
              f"(symptoms for {len(symptoms)} tickets)")
        dual_lines = dual_section(dual, picks, sum(1 for d in dual if d.symptom), ph)
    if scope != "dev split":
        print("[eval] WARNING: no dev split; using all labelled tickets")
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(render(results, scope, n, heldout_n, picks, args.margins, dual_lines), encoding="utf-8")
    print(f"[eval] wrote {REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
