"""L1 ingest: chunk the KB, store documents/chunks, embed new or changed chunks.

    python -m ax15.l1_knowledge.ingest

Re-runs are cheap: a chunk keeps its embedding while its content hash
(sha256 of context prefix + breadcrumb + text) is unchanged, so only new/changed chunks are embedded.
Every run writes one ingestion_runs row.
"""
from __future__ import annotations

import json
import sys
from collections import Counter

import psycopg
from openai import OpenAI

from ax15 import db
from ax15.config import embedding_cfg
from ax15.l1_knowledge.chunker import Document, chunk_kb, embed_input

UPSERT_CHUNK = """
INSERT INTO kb_chunks (doc_path, chunk_index, breadcrumb, text, metadata, content_hash, context_prefix)
VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s)
ON CONFLICT (doc_path, chunk_index) DO UPDATE SET
    breadcrumb = EXCLUDED.breadcrumb,
    text = EXCLUDED.text,
    metadata = EXCLUDED.metadata,
    content_hash = EXCLUDED.content_hash,
    context_prefix = EXCLUDED.context_prefix,
    updated_at = now(),
    -- keep the embedding only while the content is unchanged
    embedding = CASE WHEN kb_chunks.content_hash = EXCLUDED.content_hash THEN kb_chunks.embedding END,
    embedding_model = CASE WHEN kb_chunks.content_hash = EXCLUDED.content_hash THEN kb_chunks.embedding_model END,
    embedded_at = CASE WHEN kb_chunks.content_hash = EXCLUDED.content_hash THEN kb_chunks.embedded_at END
"""


def vector_literal(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.7f}" for x in v) + "]"


def store(conn: psycopg.Connection, docs: list[Document]) -> dict[str, int]:
    """Upsert documents and chunks; delete chunks/docs no longer in the KB."""
    paths = [d.doc_path for d in docs]
    with conn.transaction(), conn.cursor() as cur:
        before = dict(
            ((p, i), h) for p, i, h in cur.execute("SELECT doc_path, chunk_index, content_hash FROM kb_chunks")
        )
        cur.executemany(
            "INSERT INTO kb_documents (doc_path, doc_type, title, category, content_hash, summary)"
            " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (doc_path) DO UPDATE SET doc_type = EXCLUDED.doc_type,"
            " title = EXCLUDED.title, category = EXCLUDED.category, content_hash = EXCLUDED.content_hash,"
            " summary = EXCLUDED.summary, ingested_at = now()",
            [(d.doc_path, d.doc_type, d.title, d.category, d.content_hash, d.summary) for d in docs],
        )
        rows = [
            (c.doc_path, c.chunk_index, c.breadcrumb, c.text, json.dumps(c.metadata), c.content_hash, c.context_prefix)
            for d in docs for c in d.chunks
        ]
        cur.executemany(UPSERT_CHUNK, rows)
        deleted = 0
        for d in docs:
            cur.execute("DELETE FROM kb_chunks WHERE doc_path = %s AND chunk_index >= %s", (d.doc_path, len(d.chunks)))
            deleted += cur.rowcount
        cur.execute("DELETE FROM kb_documents WHERE NOT (doc_path = ANY(%s))", (paths,))
    unchanged = sum(1 for r in rows if before.get((r[0], r[1])) == r[5])
    return {"chunks_total": len(rows), "chunks_unchanged": unchanged, "chunks_deleted": deleted}


def embed_pending(conn: psycopg.Connection, client: OpenAI | None = None) -> int:
    """Embed chunks with no embedding (new/changed) or a different embedding model."""
    cfg = embedding_cfg()
    pending = conn.execute(
        "SELECT chunk_id, context_prefix, breadcrumb, text FROM kb_chunks"
        " WHERE embedding IS NULL OR embedding_model IS DISTINCT FROM %s ORDER BY chunk_id",
        (cfg["model"],),
    ).fetchall()
    if not pending:
        return 0
    client = client or OpenAI()
    done = 0
    for i in range(0, len(pending), cfg["batch_size"]):
        batch = pending[i: i + cfg["batch_size"]]
        resp = client.embeddings.create(
            model=cfg["model"], dimensions=cfg["dimensions"],
            input=[embed_input(prefix, bc, text) for _, prefix, bc, text in batch],
        )
        with conn.transaction(), conn.cursor() as cur:
            cur.executemany(
                "UPDATE kb_chunks SET embedding = %s::vector, embedding_model = %s, embedded_at = now()"
                " WHERE chunk_id = %s",
                [(vector_literal(e.embedding), cfg["model"], cid) for (cid, *_), e in zip(batch, resp.data, strict=True)],
            )
        done += len(batch)
        print(f"[ingest] embedded {done}/{len(pending)}")
    return done


def main() -> int:
    cfg = embedding_cfg()
    docs = chunk_kb()
    conn, var = db.connect()
    print(f"[ingest] connected via {var}; {len(docs)} docs, {sum(len(d.chunks) for d in docs)} chunks")
    with conn:
        run_id = conn.execute(
            "INSERT INTO ingestion_runs (embedding_model, embedding_dims, doc_hashes, docs_seen)"
            " VALUES (%s, %s, %s::jsonb, %s) RETURNING run_id",
            (cfg["model"], cfg["dimensions"], json.dumps({d.doc_path: d.content_hash for d in docs}), len(docs)),
        ).fetchone()[0]
        conn.commit()
        try:
            stats = store(conn, docs)
            stats["chunks_embedded"] = embed_pending(conn)
            status = "completed"
        except Exception:
            conn.rollback()
            conn.execute("UPDATE ingestion_runs SET status = 'failed', finished_at = now() WHERE run_id = %s", (run_id,))
            conn.commit()
            raise
        conn.execute(
            "UPDATE ingestion_runs SET status = %s, finished_at = now(), chunks_total = %s, chunks_embedded = %s,"
            " chunks_unchanged = %s, chunks_deleted = %s WHERE run_id = %s",
            (status, stats["chunks_total"], stats["chunks_embedded"], stats["chunks_unchanged"],
             stats["chunks_deleted"], run_id),
        )
        conn.commit()
    by_type = Counter(d.doc_type for d in docs for _ in d.chunks)
    print(f"[ingest] run {run_id}: " + ", ".join(f"{k}={v}" for k, v in stats.items()))
    print("[ingest] chunks by doc type: " + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
