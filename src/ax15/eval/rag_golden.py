"""RAG golden-set check: retrieval, section and answer quality on eval/rag_golden_set.json.

    python -m ax15.eval.rag_golden

Each question goes through the same steps as the retrieval bot: search_with_stubs (top 5,
store-staff audience, L2-only stubs) and then answer(). Only the question and the retrieved chunks
reach the answer model; expected facts are used afterwards, by the judge only.

Scores per question:
  retrieval  hit@5: any expected_docs path in the top-5 chunks.
             not_in_kb items: pass if the answer says the KB doesn't cover it.
  section    a top-5 breadcrumb contains expected_section (case-insensitive).
  facts      share of must_include facts the judge (base model) says are present.
  must_not   fail if the judge says any must_not item is present.
Writes outputs/rag_golden_report.md.
"""
from __future__ import annotations

import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import anthropic

from ax15 import db
from ax15.config import base_llm
from ax15.l1_knowledge.answer import NOT_COVERED, Answer, answer
from ax15.l1_knowledge.search import Hit, search_with_stubs
from ax15.settings import OUTPUTS_DIR, ROOT

K = 5
AUDIENCE = "store"
GOLDEN_PATH = ROOT / "eval" / "rag_golden_set.json"
REPORT_PATH = OUTPUTS_DIR / "rag_golden_report.md"
JUDGE_WORKERS = 8

# Judge(answer_text, statement, kind) -> True if the statement holds for the answer.
Judge = Callable[[str, str, str], bool]

JUDGE_SYSTEM = """You grade an IT-support answer against one statement.
Reply with exactly one word: yes or no."""

_JUDGE_QUESTION = {
    "include": "Does the answer state or clearly convey this fact (wording may differ)?",
    "exclude": "Does the answer contain, recommend or give this?",
}
_NOT_COVERED_RE = re.compile(r"(doesn't|does not|don't|do not) cover|not covered|isn't covered|is not covered", re.I)


def says_not_covered(text: str) -> bool:
    return NOT_COVERED.lower() in text.lower() or bool(_NOT_COVERED_RE.search(text))


def llm_judge(client: anthropic.Anthropic | None = None) -> Judge:
    cfg = base_llm()
    client = client or anthropic.Anthropic()

    def judge(answer_text: str, statement: str, kind: str) -> bool:
        prompt = (f"<answer>\n{answer_text}\n</answer>\n\n<statement>\n{statement}\n</statement>\n\n"
                  f"{_JUDGE_QUESTION[kind]} Reply yes or no.")
        resp = client.messages.create(
            model=cfg["model"], max_tokens=1024, system=JUDGE_SYSTEM,
            output_config={"effort": cfg.get("effort", "low")},
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(b.text for b in resp.content if b.type == "text").strip().lower()
        return text.startswith("yes")

    return judge


@dataclass
class ItemResult:
    id: str
    answer_type: str
    question: str
    retrieved_docs: list[str]
    retrieval_pass: bool
    section_hit: bool | None                     # None when there is no expected_section
    facts: list[tuple[str, bool]] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    answer_text: str = ""
    stubs: list[str] = field(default_factory=list)   # L2-only stubs given to the answer step

    @property
    def fact_share(self) -> float | None:
        return sum(ok for _, ok in self.facts) / len(self.facts) if self.facts else None

    @property
    def must_not_pass(self) -> bool:
        return not self.violations


def score_item(item: dict, hits: list[Hit], ans: Answer, judge: Judge,
               pool: ThreadPoolExecutor | None = None) -> ItemResult:
    top = hits[:K]
    docs = [h.doc_path for h in top]
    if item["answer_type"] == "not_in_kb":
        retrieval_pass = says_not_covered(ans.text)
    else:
        retrieval_pass = any(d in docs for d in item["expected_docs"])
    section = (item.get("expected_section") or "").strip().lower()
    section_hit = any(section in h.breadcrumb.lower() for h in top) if section else None

    jobs = [(s, "include") for s in item.get("must_include", [])] + \
           [(s, "exclude") for s in item.get("must_not", [])]
    run = (lambda f, xs: list(pool.map(f, xs))) if pool else (lambda f, xs: [f(x) for x in xs])
    verdicts = run(lambda j: judge(ans.text, j[0], j[1]), jobs)
    facts = [(s, v) for (s, kind), v in zip(jobs, verdicts) if kind == "include"]
    violations = [s for (s, kind), v in zip(jobs, verdicts) if kind == "exclude" and v]
    return ItemResult(item["id"], item["answer_type"], item["question"], docs, retrieval_pass,
                      section_hit, facts, violations, ans.text)


def load_golden(path=GOLDEN_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["items"]


def _yn(v: bool | None) -> str:
    return "—" if v is None else ("✅" if v else "❌")


def _cell(s: str) -> str:
    return s.replace("|", r"\|").replace("\n", " ")


def render(results: list[ItemResult], model: str) -> str:
    n = len(results)
    sec = [r.section_hit for r in results if r.section_hit is not None]
    shares = [r.fact_share for r in results if r.fact_share is not None]
    all_facts = [ok for r in results for _, ok in r.facts]
    lines = [
        "# RAG golden-set report", "",
        f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC · {n} questions · top {K} chunks · "
        f"audience `{AUDIENCE}` · answer + judge model `{model}`", "",
        "## Totals", "",
        "| Metric | Result |", "|---|---|",
        f"| Retrieval hit@{K} (not_in_kb: says not covered) | {sum(r.retrieval_pass for r in results)} / {n} |",
        f"| Section hit | {sum(sec)} / {len(sec)} |",
        f"| must_include facts present (all facts) | {sum(all_facts)} / {len(all_facts)} "
        f"({(sum(all_facts) / len(all_facts) if all_facts else 0):.0%}) |",
        f"| must_include share (mean per question) | {(sum(shares) / len(shares) if shares else 0):.0%} |",
        f"| must_not clean | {sum(r.must_not_pass for r in results)} / {n} |", "",
        "## Per question", "",
        "| ID | Type | Retrieval | Section | Facts | must_not | Top docs |", "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        facts = f"{sum(ok for _, ok in r.facts)}/{len(r.facts)}" if r.facts else "—"
        top = ", ".join(f"`{d}`" for d in dict.fromkeys(r.retrieved_docs))
        lines.append(f"| {r.id} | {r.answer_type} | {_yn(r.retrieval_pass)} | {_yn(r.section_hit)} | "
                     f"{facts} | {'✅' if r.must_not_pass else '❌ FAIL'} | {top} |")
    lines += ["", "## Details", ""]
    for r in results:
        lines += [f"### {r.id} · {r.answer_type}", "", f"**Q:** {_cell(r.question)}", ""]
        lines += [f"- {'✅' if ok else '❌'} {_cell(s)}" for s, ok in r.facts]
        lines += [f"- ⛔ must_not present: {_cell(s)}" for s in r.violations]
        lines += [f"- 🔒 L2-only stub passed: {_cell(s)}" for s in r.stubs]
        lines += ["", "**Answer:**", "", *[f"> {ln}" for ln in r.answer_text.splitlines() or [""]], ""]
    return "\n".join(lines) + "\n"


def main() -> int:
    items = load_golden()
    conn, _ = db.connect(autocommit=True)
    judge = llm_judge()
    results = []
    with conn, ThreadPoolExecutor(JUDGE_WORKERS) as pool:
        for item in items:
            found = search_with_stubs(conn, item["question"], k=K, audience=AUDIENCE)
            ans = answer(item["question"], found.hits, stubs=found.stubs)   # question + chunks + stubs only
            r = score_item(item, found.hits, ans, judge, pool)
            r.stubs = [s.breadcrumb for s in found.stubs]
            results.append(r)
            print(f"{r.id}: retrieval {_yn(r.retrieval_pass)} section {_yn(r.section_hit)} "
                  f"facts {sum(ok for _, ok in r.facts)}/{len(r.facts)} "
                  f"must_not {'ok' if r.must_not_pass else 'FAIL'}")
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(render(results, base_llm()["model"]), encoding="utf-8")
    print(f"Wrote {REPORT_PATH.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
