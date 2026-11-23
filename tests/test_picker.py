"""L1-6c: top-3 picker — JSON validation, retry-then-"none" path, cache, prompt contents."""
from __future__ import annotations

from types import SimpleNamespace

from ax15.l1_knowledge import picker
from ax15.l1_knowledge.picker import DocCard, PickCache, parse_pick, pick_doc

IDS = ["runbooks/printer-offline.md", "runbooks/pos-printer-integration.md"]
CARDS = [DocCard(d, d, "overview", ["Overview", "  - Step 1"]) for d in IDS]


def test_parse_pick_accepts_valid_json():
    assert parse_pick('{"doc": "runbooks/printer-offline.md", "reason": "Printer shows offline."}', IDS) == \
        ("runbooks/printer-offline.md", "Printer shows offline.")
    assert parse_pick('```json\n{"doc": "none", "reason": "No fit."}\n```', IDS) == ("none", "No fit.")


def test_parse_pick_rejects_invalid():
    bad = [
        "not json",
        '{"doc": "runbooks/other.md", "reason": "x"}',                 # not a candidate
        '{"doc": "runbooks/printer-offline.md"}',                       # missing reason
        '{"doc": "runbooks/printer-offline.md", "reason": ""}',         # empty reason
        '{"doc": 1, "reason": "x"}',
        '["runbooks/printer-offline.md"]',
        '{"doc": "runbooks/printer-offline.md", "reason": "x", "extra": 1}',
    ]
    assert all(parse_pick(b, IDS) is None for b in bad)


class FakeClient:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.prompts.append(kw["messages"][0]["content"])
        text = self.replies.pop(0)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=100, output_tokens=20))


def test_invalid_twice_returns_none_after_one_retry():
    c = FakeClient(["garbage", '{"doc": "runbooks/elsewhere.md", "reason": "x"}'])
    p = pick_doc("Printer offline", IDS, client=c, cards=CARDS)
    assert p.doc is None and not p.valid and p.attempts == 2
    assert len(c.prompts) == 2 and picker.RETRY_NOTE in c.prompts[1]
    assert (p.input_tokens, p.output_tokens) == (200, 40)


def test_retry_recovers_and_model_none_is_valid():
    c = FakeClient(["oops", '{"doc": "runbooks/pos-printer-integration.md", "reason": "POS-side config."}'])
    p = pick_doc("t", IDS, client=c, cards=CARDS)
    assert p.doc == "runbooks/pos-printer-integration.md" and p.valid and p.attempts == 2
    n = pick_doc("t", IDS, client=FakeClient(['{"doc": "none", "reason": "Not a printer issue."}']), cards=CARDS)
    assert n.doc is None and n.valid and n.attempts == 1
    assert n.as_dict() == {"doc": None, "reason": "Not a printer issue."}
    assert pick_doc("t", [], client=FakeClient([])).doc is None


def test_cache_avoids_second_call(tmp_path):
    cache = PickCache(tmp_path / "c.json")
    reply = ['{"doc": "runbooks/printer-offline.md", "reason": "Offline."}']
    first = pick_doc("t", IDS, client=FakeClient(reply), cache=cache, cards=CARDS)
    cache.save()
    again = pick_doc("t", IDS, client=FakeClient([]), cache=PickCache(tmp_path / "c.json"), cards=CARDS)
    assert again.cached and again.doc == first.doc and again.input_tokens == 100


def test_prompt_has_cards_not_full_text():
    card = picker.doc_card("runbooks/pos-screen-frozen.md")
    assert card.title and card.overview and "Overview" in card.headings
    prompt = picker.build_prompt("Screen frozen", [card])
    assert "Sections:" in prompt and card.overview in prompt
    assert len(prompt) < 4000                                # headings + overview only
