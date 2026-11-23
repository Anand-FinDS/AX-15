"""Grounded answer from retrieved chunks, using the base model from config/models.yaml."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from html import escape

import anthropic

from ax15.config import base_llm
from ax15.l1_knowledge.search import Hit, L2Stub

NOT_COVERED = "The knowledge base doesn't cover this."

SYSTEM = f"""You are an IT support assistant for ServeWell restaurant staff.
Answer the question using only the numbered knowledge-base excerpts provided.
Cite every statement with the excerpt number in square brackets, e.g. [1] or [2][3].
An <l2_only> notice means a matching procedure exists but must be performed by L2 support, not store staff.
If a notice is relevant to the question, say that this is done by L2 and recommend raising it with L2;
do not try to describe the procedure. A relevant notice counts as an answer, not as "not covered".
If neither the excerpts nor a notice answer the question, reply with exactly: "{NOT_COVERED}" and then one sentence saying what is missing.
Do not use outside knowledge. Keep the answer short: at most 6 sentences or a short numbered list of steps."""


@dataclass(frozen=True)
class Answer:
    text: str
    covered: bool
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


def build_prompt(question: str, hits: list[Hit], stubs: Sequence[L2Stub] = ()) -> str:
    excerpts = "\n\n".join(
        f'<excerpt n="{i}" doc="{escape(h.doc_path)}" section="{escape(h.breadcrumb)}">\n{h.text}\n</excerpt>'
        for i, h in enumerate(hits, 1)
    )
    prompt = f"<excerpts>\n{excerpts}\n</excerpts>\n\n"
    if stubs:      # one line per withheld L2-only chunk; never its text
        prompt += "<l2_only>\n" + "\n".join(s.line for s in stubs) + "\n</l2_only>\n\n"
    return prompt + f"Question:\n{question}"


def answer(question: str, hits: list[Hit], client: anthropic.Anthropic | None = None,
           stubs: Sequence[L2Stub] = ()) -> Answer:
    cfg = base_llm()
    if not hits and not stubs:
        return Answer(f"{NOT_COVERED} No knowledge-base excerpts matched the question.", False, cfg["model"])
    client = client or anthropic.Anthropic()
    resp = client.messages.create(
        model=cfg["model"],
        max_tokens=1024,
        system=SYSTEM,
        output_config={"effort": cfg.get("effort", "low")},
        messages=[{"role": "user", "content": build_prompt(question, hits, stubs)}],
    )
    if resp.stop_reason == "refusal":
        return Answer("The assistant declined to answer this request.", False, cfg["model"])
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    return Answer(text, not text.startswith(NOT_COVERED), cfg["model"],
                  resp.usage.input_tokens, resp.usage.output_tokens)
