"""Quick retrieval check: document-level recall@5 vs relevant_kb_docs on labelled train tickets.

    python -m ax15.l1_knowledge.retrieval_check

The query is built only from prompt-safe ticket fields (subject, version, description);
labels are read for scoring only. Main runbook = the first `runbooks/` entry in a
ticket's relevant_kb_docs. Writes outputs/retrieval_check.md.
"""
from __future__ import annotations

import sys
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from statistics import mean

from ax15 import db
from ax15.config import embedding_cfg
from ax15.l1_knowledge.search import embed_queries, search_kb, ticket_query
from ax15.settings import OUTPUTS_DIR, ROOT

K = 5
WORKERS = 8        # parallel DB connections; Neon round trips dominate the run time
_local = threading.local()


def _thread_conn():
    if not hasattr(_local, "conn"):
        _local.conn, _ = db.connect(autocommit=True)
    return _local.conn
ESCALATION_SOP = "sop/escalation-procedure.md"
VARIANTS = [("hybrid", "agent"), ("dense", "agent"), ("fts", "agent"), ("hybrid", "store")]


def top_docs(hits, k: int = K) -> list[str]:
    seen: list[str] = []
    for h in hits:
        if h.doc_path not in seen:
            seen.append(h.doc_path)
        if len(seen) == k:
            break
    return seen


def recall(relevant: list[str], got: list[str]) -> float | None:
    return len(set(relevant) & set(got)) / len(relevant) if relevant else None


def main() -> int:
    conn, _ = db.connect(autocommit=True)
    rows = conn.execute(
        "SELECT t.ticket_id, t.category, t.subject, t.description, t.system_version, l.relevant_kb_docs"
        " FROM eval.eval_labels l JOIN tickets t USING (ticket_uid) WHERE t.batch_id = 'train' ORDER BY t.ticket_id"
    ).fetchall()
    queries = [ticket_query(r[2], r[3], r[4]) for r in rows]
    vecs = embed_queries(queries)
    print(f"[check] {len(rows)} labelled tickets; embedded queries")

    def run_one(args):
        q, v, mode, aud = args
        return search_kb(_thread_conn(), q, k=50, audience=aud, mode=mode, query_embedding=v)

    results = {}
    pool = ThreadPoolExecutor(WORKERS)
    for mode, aud in VARIANTS:
        all_hits = list(pool.map(run_one, [(q, v, mode, aud) for q, v in zip(queries, vecs, strict=True)]))
        rec, rec_ex, main_doc, main_chunk = [], [], [], []
        per_cat = defaultdict(list)
        for r, hits in zip(rows, all_hits, strict=True):
            rel = list(r[5])
            docs = top_docs(hits)
            chunk_docs = {h.doc_path for h in hits[:K]}
            rec.append(recall(rel, docs))
            rex = recall([d for d in rel if d != ESCALATION_SOP], docs)
            if rex is not None:
                rec_ex.append(rex)
            main = next((d for d in rel if d.startswith("runbooks/")), None)
            if main:
                main_doc.append(main in docs)
                main_chunk.append(main in chunk_docs)
                per_cat[r[1]].append(main in docs)
        results[(mode, aud)] = {
            "recall": mean(x for x in rec if x is not None), "recall_ex": mean(rec_ex),
            "main_doc": mean(main_doc), "main_chunk": mean(main_chunk), "n_main": len(main_doc),
            "per_cat": {c: (mean(v), len(v)) for c, v in sorted(per_cat.items())},
        }
        print(f"[check] {mode:<6} {aud:<5} recall@5={results[(mode, aud)]['recall']:.3f} "
              f"main@5={results[(mode, aud)]['main_doc']:.3f}")

    counts = conn.execute(
        "SELECT d.doc_type, count(DISTINCT d.doc_path), count(c.chunk_id),"
        " count(*) FILTER (WHERE c.metadata->>'audience' = 'L2'),"
        " round(avg(array_length(regexp_split_to_array(trim(c.text), '\\s+'), 1)))"
        " FROM kb_documents d JOIN kb_chunks c USING (doc_path) GROUP BY d.doc_type ORDER BY d.doc_type"
    ).fetchall()
    run = conn.execute(
        "SELECT run_id, chunks_total, chunks_embedded, chunks_unchanged FROM ingestion_runs"
        " WHERE status = 'completed' ORDER BY run_id DESC LIMIT 1"
    ).fetchone()

    L = ["# AX-15 — Quick retrieval check\n",
         f"_Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · {len(rows)} labelled train tickets · "
         f"embeddings `{embedding_cfg()['model']}` @ {embedding_cfg()['dimensions']} dims · "
         f"latest ingestion run {run[0]} ({run[1]} chunks, {run[2]} embedded, {run[3]} unchanged)._\n",
         "## KB chunks\n", "| Doc type | Docs | Chunks | L2-only chunks | Avg words |", "|---|---:|---:|---:|---:|"]
    for dt, nd, nc, nl2, aw in counts:
        L.append(f"| {dt} | {nd} | {nc} | {nl2} | {aw} |")
    L.append(f"| **total** | **{sum(c[1] for c in counts)}** | **{sum(c[2] for c in counts)}** | "
             f"**{sum(c[3] for c in counts)}** | |\n")

    L += ["## Recall@5 (document level)\n",
          "Top 5 **distinct documents** from the ranked chunks, compared with the ticket's `relevant_kb_docs`. "
          "*Main runbook* = first `runbooks/` entry in the label. The escalation SOP is relevant to many "
          "tickets and inflates recall, so recall without it is shown too. "
          "*Main runbook in top-5 chunks* = what the bot actually shows (5 chunks, possibly fewer docs).\n",
          "| Variant | Audience | Recall@5 | Recall@5 excl. escalation SOP | Main runbook in top-5 docs | "
          "Main runbook in top-5 chunks |",
          "|---|---|---:|---:|---:|---:|"]
    for (mode, aud), r in results.items():
        L.append(f"| {mode} | {aud} | {r['recall']:.1%} | {r['recall_ex']:.1%} | {r['main_doc']:.1%} | "
                 f"{r['main_chunk']:.1%} |")
    h = results[("hybrid", "agent")]
    L += [f"\n{h['n_main']} of {len(rows)} labels name a runbook.\n",
          "## Main-runbook hit@5 by category (hybrid, agent)\n", "| Category | Hit@5 | Tickets |", "|---|---:|---:|"]
    for cat, (v, n) in h["per_cat"].items():
        L.append(f"| {cat} | {v:.1%} | {n} |")
    L += ["\n**Caveats:** all 220 labelled tickets are used (dev/held-out split not built yet), so these are "
          "development numbers, not a held-out score. Audience *agent* searches all approved chunks; "
          "*store* excludes L2-only chunks, which can hide relevant spec-sheet sections.\n"]
    OUTPUTS_DIR.mkdir(exist_ok=True)
    out = OUTPUTS_DIR / "retrieval_check.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"[check] wrote {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
