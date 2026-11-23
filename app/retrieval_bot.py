"""AX-15 retrieval bot — hybrid KB search + grounded answer.

    streamlit run app/retrieval_bot.py

Pick a train ticket (subject/description only) or type a question → top chunks with
breadcrumb, document and score → a short answer from the base model in
config/models.yaml, citing the chunks as [1], [2], ...

Evaluator view (sidebar): for labelled train tickets, shows the expected documents
from eval.eval_labels and marks each retrieved chunk HIT or MISS. Labels are loaded
only after the answer has been produced, so they can never reach the model prompt.
"""
from __future__ import annotations

import psycopg
import streamlit as st

from ax15 import db
from ax15.config import base_llm
from ax15.l1_knowledge import eval_view
from ax15.l1_knowledge.answer import answer
from ax15.l1_knowledge.search import search_with_stubs, ticket_query

st.set_page_config(page_title="AX-15 Retrieval Bot", layout="wide")


@st.cache_resource
def _conn_holder() -> dict:
    return {"conn": None}


def get_conn() -> psycopg.Connection:
    """Return a live connection, reconnecting if Neon closed the idle one (compute auto-suspend)."""
    holder = _conn_holder()
    conn = holder["conn"]
    if conn is not None and not conn.closed and not conn.broken:
        try:
            conn.execute("SELECT 1")
            return conn
        except psycopg.OperationalError:
            pass
    if conn is not None:
        conn.close()
    holder["conn"], _ = db.connect(autocommit=True)
    return holder["conn"]


@st.cache_data(ttl=600)
def train_tickets() -> list[tuple]:
    return get_conn().execute(
        "SELECT ticket_id, category, priority, subject, description, system_version, store_id, asset_id,"
        " ticket_uid FROM tickets WHERE source = 'train' AND batch_id = 'train' ORDER BY ticket_id"
    ).fetchall()


st.title("AX-15 Retrieval Bot")
st.caption(f"Hybrid search (dense + full-text, RRF) over the ServeWell KB · answers by `{base_llm()['model']}`")

with st.sidebar:
    audience_label = st.radio("Who is asking?", ["Store staff", "L2 agent"],
                              help="Store staff never see chunks tagged L2-only.")
    audience = "store" if audience_label == "Store staff" else "agent"
    k = st.slider("Chunks to retrieve", 3, 10, 5)
    st.divider()
    evaluator = st.toggle(
        "Evaluator view",
        help="For labelled train tickets: show the expected documents and mark each retrieved chunk "
             "hit or miss. Labels are shown here only and are never sent to the model.",
    )

source = st.radio("Input", ["Train ticket", "Free question"], horizontal=True)
ticket_uid = None
if source == "Train ticket":
    rows = train_tickets()
    by_id = {r[0]: r for r in rows}
    tid = st.selectbox("Ticket", list(by_id), format_func=lambda t: f"{t} · {by_id[t][1]} · {by_id[t][3]}")
    t = by_id[tid]
    ticket_uid = t[8]
    st.markdown(f"**{t[3]}**  \n`{t[2]}` · `{t[1]}` · store `{t[6]}` · asset `{t[7]}` · `{t[5]}`")
    with st.expander("Ticket description", expanded=False):
        st.write(t[4])
    query = ticket_query(t[3], t[4], t[5])
else:
    query = st.text_area("Question", placeholder="e.g. Receipt printer stopped printing after a driver update")

if st.button("Search", type="primary", disabled=not (query or "").strip()):
    conn = get_conn()
    with st.spinner("Searching the knowledge base…"):
        found = search_with_stubs(conn, query, k=k, audience=audience)
        hits, stubs = found.hits, found.stubs
    with st.spinner("Writing a grounded answer…"):
        a = answer(query, hits, stubs=stubs)

    # Evaluator data is fetched only now, after the model call has returned.
    score = None
    if evaluator and ticket_uid is not None:
        expected = eval_view.expected_docs(conn, ticket_uid)
        if expected is not None:
            score = eval_view.score_docs(expected, [h.doc_path for h in hits])

    if evaluator:
        st.subheader("Evaluator view")
        if source != "Train ticket":
            st.info("Expected documents exist only for labelled train tickets. Pick a train ticket to compare.")
        elif score is None:
            st.info("This ticket has no label (36 of the 256 train tickets are unlabelled), so there is nothing to compare.")
        else:
            c1, c2, c3 = st.columns(3)
            c1.metric("Expected docs found", f"{score.found} / {len(score.expected)}")
            c2.metric("Doc recall", f"{score.recall:.0%}")
            c3.metric("Main runbook", "—" if score.main_hit is None else ("Hit" if score.main_hit else "Miss"))
            st.markdown("**Expected documents** · from `eval.eval_labels`, shown here only, never sent to the model")
            for doc, rank in score.expected:
                main = " · main runbook" if doc == score.main_runbook else ""
                if rank is None:
                    st.markdown(f"❌ `{doc}`{main} · not in the top {len(hits)} chunks")
                else:
                    st.markdown(f"✅ `{doc}`{main} · first retrieved at chunk [{rank}]")

    expected_set = {d for d, _ in score.expected} if score else set()
    st.subheader("Retrieved chunks")
    if not hits:
        st.info("No chunks matched.")
    for i, h in enumerate(hits, 1):
        tag = " · 🔒 L2-only" if h.audience == "L2" else ""
        mark = ("✅ HIT · " if h.doc_path in expected_set else "❌ MISS · ") if score else ""
        with st.expander(f"{mark}[{i}] {h.breadcrumb}  —  score {h.score:.4f}{tag}"):
            st.caption(f"`{h.doc_path}` · section: {h.section_type} · dense rank {h.dense_rank} · "
                       f"full-text rank {h.fts_rank}")
            st.markdown(h.text)
    for s in stubs:
        st.info(f"🔒 {s.line}  \n`{s.doc_path}` · would have ranked [{s.rank}] · "
                "text withheld from store staff")

    st.subheader("Answer")
    (st.success if a.covered else st.warning)(a.text)
    st.caption(f"{a.model} · {a.input_tokens} in / {a.output_tokens} out tokens · "
               "uses only the chunks above; [n] refers to the chunk numbers")
