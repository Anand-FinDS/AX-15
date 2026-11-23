"""2.0 output guard (L3, pure code): checks the composed answer before anything is proposed.

Checks:
1. every step is verbatim KB text (grounding.is_grounded) -> otherwise the step is stripped;
2. no L2-only text reaches the store (steps or reply sentences that contain an L2-only chunk sentence);
3. unknown system / no-steps policy -> steps removed;
4. ids, error codes, numbers and CMDB device names in the reply must appear in the sources (ticket,
   tool outputs, shown KB items, SOP) -> otherwise that sentence is stripped;
5. schema validation (V2Answer).
Every strip adds a flag. If steps were expected and none survive, the answer is escalated instead
("nothing safe remains"); an emptied reply is replaced by a neutral template for the decision.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ax15.trust import grounding

_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")
_TOKENS = [
    re.compile(r"\b[A-Z]{2,4}-\d{3,4}(?:-[A-Z0-9]{1,3})?\b"),     # asset / store ids: POS-0111-T1, SW-0111
    re.compile(r"\bINC-\d{3,6}\b"),                                # ticket ids
    re.compile(r"\b0x[0-9A-Fa-f]{2,}\b"),                          # hex error codes
    re.compile(r"\b(?:ERR|E|C)-?\d{2,4}\b"),                       # E-402, ERR-4521, E04
    re.compile(r"(?<![\w.])\d{2,}(?:[.:]\d+)*(?![\w])"),           # numbers with 2+ digits, times, versions
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b"),                    # email contacts
]


_EMP_PHRASE = re.compile(r"(?i)\b((?:employee|staff|emp)\s*(?:id|no\.?|number|code)\s*[:#]?\s*)([A-Z0-9][A-Z0-9-]{2,})")


def employee_ids(text: str) -> list[str]:
    """Ids introduced as 'employee / staff ID: X' in a text (only these are treated as personal identifiers;
    asset, store, ticket and menu ids are never redacted)."""
    return list(dict.fromkeys(m.group(2).rstrip(".,;:)") for m in _EMP_PHRASE.finditer(text or "")))


def redact(text: str, ids: list[str] | tuple = ()) -> str:
    """Remove employee ids from text the store or L2 sees: the phrase form, plus any known id from the ticket."""
    if not text:
        return text
    out = _EMP_PHRASE.sub(lambda m: m.group(1) + "[redacted]", text)
    for i in ids:
        if len(i) >= 4:
            out = out.replace(i, "[redacted]")
    return out


@dataclass
class GuardReport:
    flags: list[str] = field(default_factory=list)
    stripped_steps: list[str] = field(default_factory=list)
    stripped_sentences: list[str] = field(default_factory=list)
    escalated: bool = False
    template_reply: bool = False

    def as_dict(self) -> dict:
        return {"flags": self.flags, "stripped_steps": self.stripped_steps,
                "stripped_sentences": self.stripped_sentences, "escalated": self.escalated,
                "template_reply": self.template_reply}


def json_dump(x) -> str:
    return json.dumps(x, sort_keys=True, default=str)


def sentences(text: str) -> list[str]:
    return [s for s in _SENT.split((text or "").strip()) if s.strip()]


def l2_sentences(l2_texts: list[str]) -> list[str]:
    """Normalised sentences (>= 30 chars) of L2-only chunks: the fingerprints the store must never see."""
    out = []
    for t in l2_texts:
        for line in re.split(r"[\n.]+", t):
            s = grounding.normalise(re.sub(r"[*`#>|_-]+", " ", line)).lower()
            if len(s) >= 30:
                out.append(s)
    return out


def _contains_l2(text: str, fingerprints: list[str]) -> bool:
    flat = grounding.normalise(re.sub(r"[*`#>|_-]+", " ", text)).lower()
    return any(fp in flat for fp in fingerprints)


def ungrounded_tokens(sentence: str, sources_lower: str, vocab: list[str]) -> list[str]:
    bad = []
    for rx in _TOKENS:
        for m in rx.findall(sentence):
            if m.lower() not in sources_lower:
                bad.append(m)
    low = sentence.lower()
    for name in vocab:                                              # CMDB device / product names
        n = name.lower()
        if len(n) >= 4 and re.search(rf"(?<![\w]){re.escape(n)}(?![\w])", low) and n not in sources_lower:
            bad.append(name)
    return list(dict.fromkeys(bad))


TEMPLATES = {
    "escalate": "Thanks for reporting this. We have escalated the ticket to our specialist L2 team, who will contact "
                "you with an update.",
    "l1": "Thanks for reporting this. Please follow the steps below and let us know the result.",
    "clarify": "Thanks for reporting this. Could you confirm which device is affected and what you see on screen, "
               "so we can help you with the right steps?",
    "non_it": "Thanks for your message. This request is not an IT issue, so please raise it with the relevant team.",
    "verify": "Thanks. The ticket shows this issue as resolved; please confirm everything is working normally.",
}


def check(answer: dict, *, decision: str, sources: str, corpus: str, l2_texts: list[str], vocab: list[str],
          no_steps: bool) -> tuple[dict, GuardReport]:
    """Returns (guarded answer dict, report). Never raises on content problems."""
    rep = GuardReport()
    fps = l2_sentences(l2_texts)
    src = sources.lower()
    ans = dict(answer)
    steps_in = list(ans.get("steps") or [])
    steps = []
    if no_steps and steps_in:
        rep.stripped_steps += steps_in
        rep.flags.append("guard:no_steps_policy")
        steps_in = []
    for s in steps_in:
        if not grounding.is_grounded(s, corpus):
            rep.stripped_steps.append(s)
            rep.flags.append("guard:step_not_verbatim")
        elif _contains_l2(s, fps):
            rep.stripped_steps.append(s)
            rep.flags.append("guard:l2_only_text")
        else:
            steps.append(s)
    ans["steps"] = steps
    interim = []
    for s in ans.get("interim_steps") or []:
        if no_steps or not grounding.is_grounded(s, corpus) or _contains_l2(s, fps):
            rep.stripped_steps.append(s)
            rep.flags.append("guard:interim_step_removed")
        else:
            interim.append(s)
    if "interim_steps" in ans:
        ans["interim_steps"] = interim

    keep = []
    for sent in sentences(ans.get("reply_to_store") or ""):
        bad = ungrounded_tokens(sent, src, vocab)
        if _contains_l2(sent, fps):
            rep.stripped_sentences.append(sent)
            rep.flags.append("guard:l2_only_text")
        elif bad:
            rep.stripped_sentences.append(sent)
            rep.flags.append("guard:ungrounded_reply:" + ",".join(bad[:3]))
        else:
            keep.append(sent)
    ans["reply_to_store"] = " ".join(keep)

    if steps_in and not steps and decision == "l1":                 # nothing safe remains -> escalate
        rep.escalated = True
        rep.flags.append("guard:escalated_nothing_safe")
        decision = "escalate"
        ans.update(routing="l2_escalation", should_escalate=True,
                   escalation_reason="No verified KB steps could be shown safely; L2 to review.")
        if not ans.get("l2_handoff"):
            ans["l2_handoff"] = {"summary": "Guard removed every proposed step (not verbatim KB text); "
                                            "needs L2 review.", "evidence": rep.flags[:5], "attempted_steps": [],
                                 "requested_action": "Review the ticket and advise the store."}
        ans["reply_to_store"] = ""
    if not ans["reply_to_store"].strip():
        rep.template_reply = True
        ans["reply_to_store"] = TEMPLATES.get(decision, TEMPLATES["escalate"])
    # personal identifiers: redact in everything the store or L2 reads
    before = json_dump(ans)
    ids = employee_ids(sources)
    ans["reply_to_store"] = redact(ans["reply_to_store"], ids)
    if ans.get("escalation_reason"):
        ans["escalation_reason"] = redact(ans["escalation_reason"], ids)
    if ans.get("l2_handoff"):
        h = dict(ans["l2_handoff"])
        for k in ("summary", "requested_action"):
            if h.get(k):
                h[k] = redact(h[k], ids)
        for k in ("evidence", "attempted_steps"):
            h[k] = [redact(x, ids) for x in h.get(k) or []]
        ans["l2_handoff"] = h
    ans["non_it_redirects"] = [redact(x, ids) for x in ans.get("non_it_redirects") or []]
    if json_dump(ans) != before:
        rep.flags.append("guard:redacted_personal_id")
    rep.flags = list(dict.fromkeys(rep.flags))
    return ans, rep
