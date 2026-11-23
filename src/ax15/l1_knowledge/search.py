"""search_kb: hybrid retrieval over kb_chunks.

Dense (pgvector cosine) and full-text (Postgres tsvector) rankings are combined
with Reciprocal Rank Fusion: score = sum over rankings of 1 / (60 + rank).
Only approved chunks are searched; audience='store' never returns L2-only chunks.

L2-only stubs (search_with_stubs): for audience='store', an L2-only chunk that would
have ranked in the top k is not dropped silently. It comes back as a one-line stub
(breadcrumb only; its text is never read) so the answer can say "this is done by L2".

Optional category filter (metadata.category, e.g. "POS"): not used by the bot yet;
the 2.0 classifier will set it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import psycopg
from openai import OpenAI

from ax15.config import embedding_cfg
from ax15.l1_knowledge.ingest import vector_literal

Audience = Literal["store", "agent"]
Mode = Literal["hybrid", "dense", "fts"]
RRF_K = 60
POOL = 50          # candidates taken from each ranking before fusion
L2_HEADROOM = 25   # extra candidates fetched for store, so POOL remain after removing L2-only ones


@dataclass(frozen=True)
class Hit:
    chunk_id: int
    doc_path: str
    breadcrumb: str
    text: str
    score: float
    audience: str
    section_type: str
    dense_rank: int | None
    fts_rank: int | None


@dataclass(frozen=True)
class L2Stub:
    """An L2-only chunk withheld from store staff. Carries no chunk text by design."""
    chunk_id: int
    doc_path: str
    breadcrumb: str
    rank: int                  # where it would have ranked among all audiences (1-based)

    @property
    def line(self) -> str:
        return f"L2-only procedure exists: {self.breadcrumb} — must be performed by L2."


@dataclass(frozen=True)
class Retrieval:
    hits: list[Hit]
    stubs: list[L2Stub] = field(default_factory=list)


_client: OpenAI | None = None


def embed_queries(texts: list[str]) -> list[list[float]]:
    global _client
    _client = _client or OpenAI()
    cfg = embedding_cfg()
    out: list[list[float]] = []
    for i in range(0, len(texts), cfg["batch_size"]):
        resp = _client.embeddings.create(model=cfg["model"], dimensions=cfg["dimensions"],
                                         input=texts[i: i + cfg["batch_size"]])
        out.extend(e.embedding for e in resp.data)
    return out


def rrf(*rankings: list[int], k: int = RRF_K) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking, start=1):
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank)
    return scores


def _top(scores: dict[int, float], k: int) -> list[int]:
    return sorted(scores, key=lambda c: (-scores[c], c))[:k]


# Candidates are fetched across all audiences with their audience flag; L2-only ones
# are removed in Python for store staff (same visible ranking as filtering in SQL).
_FILTER = ("status = 'approved' AND (%(category)s::text IS NULL OR metadata->>'category' = %(category)s)"
           " AND (%(doc_path)s::text IS NULL OR doc_path = %(doc_path)s)")


def _dense(conn, qvec: list[float], n: int, category: str | None, doc_path: str | None = None
           ) -> list[tuple[int, bool]]:
    return [(r[0], r[1]) for r in conn.execute(
        f"SELECT chunk_id, metadata->>'audience' = 'L2' FROM kb_chunks WHERE embedding IS NOT NULL AND {_FILTER}"
        " ORDER BY embedding <=> %(q)s::vector LIMIT %(n)s",
        {"q": vector_literal(qvec), "n": n, "category": category, "doc_path": doc_path},
    )]


def _fts(conn, query: str, n: int, category: str | None, doc_path: str | None = None
         ) -> list[tuple[int, bool]]:
    # OR the query terms together: ticket text is long, AND semantics would match nothing.
    return [(r[0], r[1]) for r in conn.execute(
        f"""
        WITH q AS (SELECT NULLIF(replace(plainto_tsquery('english', %(text)s)::text, '&', '|'), '')::tsquery AS q)
        SELECT chunk_id, metadata->>'audience' = 'L2' FROM kb_chunks, q
        WHERE q.q IS NOT NULL AND tsv @@ q.q AND {_FILTER}
        ORDER BY ts_rank_cd(tsv, q.q) DESC, chunk_id LIMIT %(n)s
        """,
        {"text": query, "n": n, "category": category, "doc_path": doc_path},
    )]


def search_with_stubs(
    conn: psycopg.Connection,
    query: str,
    k: int = 5,
    audience: Audience = "store",
    mode: Mode = "hybrid",
    query_embedding: list[float] | None = None,
    category: str | None = None,
    doc_path: str | None = None,
) -> Retrieval:
    """Top-k visible chunks, plus stubs for L2-only chunks that store staff may not see."""
    if audience not in ("store", "agent"):
        raise ValueError(f"audience must be 'store' or 'agent', not {audience!r}")
    if not query or not query.strip():
        return Retrieval([])
    store = audience == "store"
    n = POOL + L2_HEADROOM if store else POOL
    dense: list[tuple[int, bool]] = []
    fts: list[tuple[int, bool]] = []
    if mode in ("hybrid", "dense"):
        dense = _dense(conn, query_embedding or embed_queries([query])[0], n, category, doc_path)
    if mode in ("hybrid", "fts"):
        fts = _fts(conn, query, n, category, doc_path)

    def ids(ranking, visible_only):
        return [c for c, is_l2 in ranking if not (visible_only and is_l2)][:POOL]

    vis_dense, vis_fts = ids(dense, store), ids(fts, store)
    scores = rrf(vis_dense, vis_fts)
    top = _top(scores, k)

    stubs: list[L2Stub] = []
    if store:
        l2 = {c for c, is_l2 in dense + fts if is_l2}
        full = _top(rrf(ids(dense, False), ids(fts, False)), k)
        ranked_l2 = [(rank, c) for rank, c in enumerate(full, 1) if c in l2]
        if ranked_l2:
            meta = {r[0]: r for r in conn.execute(     # path + breadcrumb only: L2 text is never selected
                "SELECT chunk_id, doc_path, breadcrumb FROM kb_chunks WHERE chunk_id = ANY(%s)",
                ([c for _, c in ranked_l2],),
            )}
            stubs = [L2Stub(c, meta[c][1], meta[c][2], rank) for rank, c in ranked_l2]

    if not top:
        return Retrieval([], stubs)
    rows = {r[0]: r for r in conn.execute(
        "SELECT chunk_id, doc_path, breadcrumb, text, metadata->>'audience', metadata->>'section_type'"
        " FROM kb_chunks WHERE chunk_id = ANY(%s)", (top,),
    )}
    d_rank = {c: i for i, c in enumerate(vis_dense, 1)}
    f_rank = {c: i for i, c in enumerate(vis_fts, 1)}
    hits = [
        Hit(c, rows[c][1], rows[c][2], rows[c][3], round(scores[c], 5), rows[c][4], rows[c][5],
            d_rank.get(c), f_rank.get(c))
        for c in top
    ]
    return Retrieval(hits, stubs)


def search_kb(
    conn: psycopg.Connection,
    query: str,
    k: int = 5,
    audience: Audience = "store",
    mode: Mode = "hybrid",
    query_embedding: list[float] | None = None,
    category: str | None = None,
    doc_path: str | None = None,
) -> list[Hit]:
    """Top-k chunks for `query`. audience='store' hides L2-only content; 'agent' sees everything approved.
    category (optional) restricts to one KB category, e.g. "POS"; doc_path to one document."""
    return search_with_stubs(conn, query, k, audience, mode, query_embedding, category, doc_path).hits


# --- Stage 1 of two-stage retrieval: doc-level routing (no LLM calls) -------------------------

# Locked routing config (L1-6d, from outputs/retrieval_eval.md): dense search, top 20 chunks,
# 0.005 per extra chunk, return the top 3 runbooks. The 0.1 bonus was the L1-6b starting point.
ROUTE_TOP_K = 20
ROUTE_MODE: "Mode" = "dense"
ROUTE_COUNT_BONUS = 0.005
ROUTE_LEGACY_BONUS = 0.1
ROUTE_TOP_DOCS = 3
ROUTE_DOC_PREFIX = "runbooks/"


@dataclass(frozen=True)
class DocRoute:
    doc: str
    score: float
    top_chunk_breadcrumbs: list[str]
    n_chunks: int


@dataclass(frozen=True)
class RouteResult:
    docs: list[DocRoute]
    margin: float              # score of doc 1 minus doc 2 (doc 1's score if only one doc)

    def top(self, n: int) -> list[str]:
        return [d.doc for d in self.docs[:n]]


def route_doc(
    conn: psycopg.Connection,
    query: str,
    filters: dict | None = None,
    mode: Mode = ROUTE_MODE,
    query_embedding: list[float] | None = None,
    top_k: int = ROUTE_TOP_K,
    n_docs: int | None = ROUTE_TOP_DOCS,
    doc_prefix: str | None = ROUTE_DOC_PREFIX,
) -> RouteResult:
    """Top runbooks for `query`, ranked from its top-k chunks (defaults: dense, k=20, top 3 runbooks).

    doc_score = max chunk RRF score + 0.005 * (that doc's chunks in the top k - 1).
    filters: {"audience": "store"|"agent" (default "store"), "category": str | None, "bonus": float}.
    For audience='store', L2-only chunks never count toward a document.
    """
    f = filters or {}
    audience = f.get("audience", "store")
    hits = search_kb(conn, query, k=top_k, audience=audience, mode=mode,
                     query_embedding=query_embedding, category=f.get("category"))
    return rank_docs(hits, audience=audience, bonus=f.get("bonus", ROUTE_COUNT_BONUS),
                     doc_category=f.get("doc_category"), ticket_category=f.get("soft_category"),
                     category_boost=f.get("category_boost", 0.0), doc_prefix=doc_prefix, n_docs=n_docs)


def rank_docs(
    hits: list[Hit],
    audience: Audience = "store",
    bonus: float = ROUTE_COUNT_BONUS,
    doc_category: dict[str, str] | None = None,
    ticket_category: str | None = None,
    category_boost: float = 0.0,
    doc_prefix: str | None = None,
    n_docs: int | None = None,
) -> RouteResult:
    """Pure doc scoring over chunk hits (best-first).

    score = max chunk RRF + bonus * (doc's chunk count - 1)
            [+ category_boost if doc_category[doc] == ticket_category: soft, no filtering]
    doc_prefix keeps only matching docs (e.g. "runbooks/"); n_docs truncates. Margin is computed
    over the docs returned.
    """
    if doc_prefix:
        hits = [h for h in hits if h.doc_path.startswith(doc_prefix)]
    if audience == "store":                      # defence in depth: search_kb already hides them
        hits = [h for h in hits if h.audience != "L2"]
    by_doc: dict[str, list[Hit]] = {}
    for h in hits:
        by_doc.setdefault(h.doc_path, []).append(h)
    docs = []
    for doc, hs in by_doc.items():
        score = max(h.score for h in hs) + bonus * (len(hs) - 1)
        if ticket_category and doc_category and doc_category.get(doc) == ticket_category:
            score += category_boost
        docs.append(DocRoute(doc, round(score, 6), [h.breadcrumb for h in hs[:3]], len(hs)))
    docs.sort(key=lambda d: (-d.score, d.doc))
    if n_docs is not None:
        docs = docs[:n_docs]
    margin = docs[0].score - (docs[1].score if len(docs) > 1 else 0.0) if docs else 0.0
    return RouteResult(docs, round(margin, 6))


def ticket_query(subject: str | None, description: str | None, system_version: str | None = None) -> str:
    """Retrieval query from prompt-safe ticket fields only (never labels)."""
    return "\n".join(x for x in (subject, system_version, description) if x)
