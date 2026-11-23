"""SOP rules as config: config/sop_rules.yaml evaluated in pure code (no LLM, no I/O).

    result = evaluate(facts)
    result["decision"]     # escalate | l1 | clarify | non_it | verify
    result["priority"]     # P1..P4 after base priority + overrides
    result["fired_rules"]  # [{id, stage, effect, reason, source, applied}]
    result["notify"]       # extra handoff recipients (keys of handoff.notify) from applied rules
    result["reply_prefix"] # fixed text that opens the store reply (yaml `replies`), or None

Facts are a flat dict (see `FACTS`); missing facts count as false / 0. The decision is the first
stage (yaml `stages` order) that has a fired rule; rules that fired in later stages are reported
with applied=False ("superseded"). With no rule fired the decision is l1 (SOP Step 2).

    python -m ax15.trust.sop_rules        # writes outputs/sop_rules_table.md for review
"""
from __future__ import annotations

import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from ax15.settings import OUTPUTS_DIR, ROOT

RULES_PATH = ROOT / "config" / "sop_rules.yaml"
PRIORITIES = ["P1", "P2", "P3", "P4"]
DECISIONS = ("escalate", "l1", "clarify", "non_it", "verify")

# Fact names the rules use, with where they come from (documentation + unit-test coverage check).
FACTS = {
    "ticket_closed": "code: ticket status Resolved/Closed",
    "ticket_status": "code: ticket status",
    "ticket_priority": "code: the priority on the ticket",
    "is_it": "classify",
    "clarity_vague": "classify: clarity == vague",
    "dne_how_to": "classify", "dne_user_error": "classify", "dne_billing": "classify",
    "dne_maintenance": "classify", "dne_feature_request": "classify", "dne_third_party_only": "classify",
    "dne_resolved_after_reset": "classify",
    "safety_hazard": "classify: fire, smoke, burning smell, sparks, electric shock or exposed wiring (incl. suspected)",
    "power_instability": "classify: repeated loss of mains power at the store",
    "payment_failure": "classify", "pos_outage_all": "classify", "data_loss": "classify",
    "security_incident": "classify", "network_infrastructure_down": "classify", "multiple_locations": "classify",
    "integration_failure": "classify", "waited_over_30_min": "classify", "compliance_impact": "classify",
    "customer_requested_escalation": "classify", "expresses_dissatisfaction": "classify",
    "unknown_error_codes": "code: classify error codes not found in the KB text (list)",
    "unknown_system": "code: a named system is in neither the KB, the CMDB nor the version matrix",
    "unknown_systems": "code: the unknown names (list)",
    "asset_unverified": "code: asset missing / not in CMDB and no single matching asset at the store",
    "system_mismatch": "code: get_asset mismatch_kind == system (ticket names another system)",
    "kb_no_doc": "route: pick_doc returned none",
    "l2_only_needed": "resolve: the fix is an L2-only procedure (stub)",
    "all_steps_tried": "resolve: every step of the chosen section was already tried",
    "runbook_escalation_met": "resolve: a 'When to Escalate' criterion of the runbook is met (verified in code)",
    "llm_failed": "code: name of the graph node whose model call failed (classify / resolve)",
    "network_issue": "code: network/Wi-Fi symptom (device router, infra down, or network words in clean_symptom)",
    "pos_affected": "code: POS / payment terminal affected", "during_service": "code: customer-facing or peak hour",
    "intents": "classify: [{kind: it|non_it, summary}] from the description text",
    "store_wide_outage": "classify", "payments_unavailable": "classify", "partial_outage": "classify",
    "devices_affected": "classify (int)", "device_unusable": "classify", "function_degraded": "classify", "cosmetic_only": "classify",
    "opened_hour_ist": "code: hour of date_opened in IST (float)",
    "recurrence_count": "code: earlier same-issue reports at the store in 30 days (+ approved escalations, same asset)",
    "store_type": "code: get_store", "known_issue_workaround": "code+classify: matches a version-matrix known bug with a fix",
}


@lru_cache(maxsize=4)
def load_rules(path: Path = RULES_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def rules_version(path: Path = RULES_PATH) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


# --- condition language ---------------------------------------------------------------------------

def _truthy(v: Any) -> bool:
    if isinstance(v, (list, tuple, set, dict)):
        return len(v) > 0
    return bool(v)


def _num(v: Any) -> float:
    if isinstance(v, (list, tuple, set)):
        return float(len(v))
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def check(cond: dict, facts: dict) -> bool:
    """Evaluate one `when` condition against the facts."""
    if "all" in cond:
        return all(check(c, facts) for c in cond["all"])
    if "any" in cond:
        return any(check(c, facts) for c in cond["any"])
    if "not" in cond:
        return not check(cond["not"], facts)
    v = facts.get(cond["fact"])
    op = cond.get("op")
    if op is None:
        return _truthy(v)
    target = cond.get("value")
    if op == "==":
        return v == target
    if op == "!=":
        return v != target
    if op == "in":
        return v in target
    if op == "hour_in":                       # [[start, end), ...] in hours, e.g. 11 <= h < 14
        return v is not None and any(lo <= float(v) < hi for lo, hi in target)
    n, t = _num(v), float(target)
    return {">=": n >= t, ">": n > t, "<=": n <= t, "<": n < t}[op]


class _Blank(dict):
    def __missing__(self, key):
        return "?"


def _text(template: str, facts: dict) -> str:
    vals = _Blank({k: (", ".join(map(str, v)) if isinstance(v, (list, tuple)) else v) for k, v in facts.items()})
    for k in list(facts):                     # "{x_text}" placeholders render list/str facts
        vals.setdefault(f"{k}_text", vals[k])
    try:
        return template.format_map(vals)
    except (ValueError, IndexError):
        return template


def _cite(src: dict, docs: dict) -> str:
    return f"{docs.get(src.get('doc'), src.get('doc'))} > {src.get('section')}"


# --- evaluation -----------------------------------------------------------------------------------

def shift(priority: str, change: str | int) -> str:
    """Apply a priority change: +1 = one level more urgent (P3 -> P2), max P1, min P4."""
    i = PRIORITIES.index(priority) if priority in PRIORITIES else 2
    if change == "at_least_P2":
        return PRIORITIES[min(i, 1)]
    step = int(str(change))
    return PRIORITIES[max(0, min(3, i - step))]


def evaluate(facts: dict, rules: dict | None = None, include_proposed: bool = False) -> dict:
    """facts -> {decision, routing_hint, priority, base_priority, mode, fired_rules, priority_rules, no_steps, flags}."""
    cfg = rules or load_rules()
    docs = cfg.get("docs", {})
    stages = cfg["stages"]
    fired = []
    for r in cfg["rules"]:
        if r.get("active", True) is False and not include_proposed:     # proposed rules: what-if only
            continue
        if check(r["when"], facts):
            fired.append({"id": r["id"], "stage": r["stage"], "effect": dict(r["effect"]),
                          "reason": _text(r["reason"], facts), "source": _cite(r["source"], docs)})
    stage = next((s for s in stages if any(f["stage"] == s for f in fired)), None)
    applied = [f for f in fired if f["stage"] == stage]
    for f in fired:
        f["applied"] = f["stage"] == stage
    decision = applied[0]["effect"]["decision"] if applied else "l1"
    routing_hint = next((f["effect"]["routing"] for f in applied if "routing" in f["effect"]), None)
    # Verify mode takes no troubleshooting action, but the ticket's routing class still follows the SOP:
    # verify_decision = what the remaining stages decide (the rules stay marked superseded).
    verify_decision = None
    if decision == "verify":
        nxt = next((s for s in stages if s != "verify" and any(f["stage"] == s for f in fired)), None)
        nxt_rules = [f for f in fired if f["stage"] == nxt]
        verify_decision = nxt_rules[0]["effect"]["decision"] if nxt_rules else "l1"
        routing_hint = next((f["effect"]["routing"] for f in nxt_rules if "routing" in f["effect"]), None)
    # no_steps / flags come from every fired rule (a superseded unknown-system rule still means no steps)
    no_steps = any(f["effect"].get("no_steps") for f in fired)
    flags = list(dict.fromkeys(f["effect"]["flag"] for f in fired if f["effect"].get("flag")))
    if decision == "verify":                    # closed ticket: report, never act (e.g. safety_hazard_reported)
        flags += [f["effect"]["verify_flag"] for f in fired if f["effect"].get("verify_flag")
                  and f["effect"]["verify_flag"] not in flags]
    redirect = next((f["effect"]["redirect"] for f in applied if f["effect"].get("redirect")), None)
    notify = list(dict.fromkeys(n for f in applied for n in f["effect"].get("notify", [])))
    prefix_key = next((f["effect"]["reply_prefix"] for f in applied if f["effect"].get("reply_prefix")), None)
    reply_prefix = " ".join(cfg.get("replies", {})[prefix_key].split()) if prefix_key else None

    # priority: base (first match) then overrides in order
    prio_rules = []
    base = facts.get("ticket_priority") if facts.get("ticket_priority") in PRIORITIES else "P3"
    base_rule = next((p for p in cfg.get("priority_base", []) if check(p["when"], facts)), None)
    if base_rule:
        base = base_rule["priority"]
        prio_rules.append({"id": base_rule["id"], "change": f"base {base}", "reason": _text(base_rule["reason"], facts),
                           "source": _cite(base_rule["source"], docs)})
    else:
        prio_rules.append({"id": "AX-PRI-KEEP", "change": f"base {base}",
                           "reason": "No impact signal: keep the ticket's priority as the base",
                           "source": "AX-15 policy > base priority"})
    if decision == "non_it":                    # non-IT: redirect only, never an IT priority uplift
        keep = facts.get("ticket_priority") if facts.get("ticket_priority") in PRIORITIES else "P4"
        prio_rules = [{"id": "AX-PRI-NON-IT", "change": f"keep {keep}", "source": "AX-15 policy > non-IT redirect",
                       "reason": "Non-IT request: redirected, no IT priority assessment or uplift"}]
        return {"decision": decision, "verify_decision": verify_decision, "routing_hint": routing_hint,
                "priority": keep, "base_priority": keep, "mode": "handle", "no_steps": True, "flags": flags,
                "redirect": redirect, "notify": notify, "reply_prefix": reply_prefix,
                "fired_rules": fired, "priority_rules": prio_rules}
    prio = base
    stack = cfg.get("increases_stack", True)
    bumped = False
    for o in cfg.get("priority_overrides", []):
        if not check(o["when"], facts):
            continue
        inc = o["change"] != "at_least_P2" and int(str(o["change"])) > 0      # YAML reads +1 as int 1
        if inc and bumped and not stack:
            prio_rules.append({"id": o["id"], "change": "no change (one +1 level max)",
                               "reason": _text(o["reason"], facts), "source": _cite(o["source"], docs)})
            continue
        if shift(prio, o["change"]) != prio:
            new = shift(prio, o["change"])
            bumped = bumped or inc
            prio_rules.append({"id": o["id"], "change": f"{prio} -> {new}", "reason": _text(o["reason"], facts),
                               "source": _cite(o["source"], docs)})
            prio = new
    forced = next((f for f in applied if f["effect"].get("priority")), None)     # e.g. AX-SAFETY -> P1
    if forced and forced["effect"]["priority"] != prio:
        prio_rules.append({"id": forced["id"], "change": f"{prio} -> {forced['effect']['priority']}",
                           "reason": forced["reason"], "source": forced["source"]})
        prio = forced["effect"]["priority"]
    return {"decision": decision, "verify_decision": verify_decision, "routing_hint": routing_hint,
            "priority": prio, "base_priority": base,
            "mode": "verify" if decision == "verify" else "handle", "no_steps": no_steps, "flags": flags,
            "redirect": redirect, "notify": notify, "reply_prefix": reply_prefix,
            "fired_rules": fired, "priority_rules": prio_rules}


def handoff_spec(rules: dict | None = None) -> dict:
    return (rules or load_rules())["handoff"]


# --- review table -----------------------------------------------------------------------------------

def _cond_text(c: dict) -> str:
    if "all" in c:
        return " AND ".join(_cond_text(x) for x in c["all"])
    if "any" in c:
        return "(" + " OR ".join(_cond_text(x) for x in c["any"]) + ")"
    if "not" in c:
        return f"NOT {_cond_text(c['not'])}"
    if "op" not in c:
        return f"`{c['fact']}`"
    return f"`{c['fact']}` {c['op']} {c['value']}"


def render_table(rules: dict | None = None) -> str:
    cfg = rules or load_rules()
    docs = cfg.get("docs", {})
    L = ["# AX-15 2.0 — SOP rules table", "",
         f"_Generated from `config/sop_rules.yaml` (hash {rules_version()}) by `python -m ax15.trust.sop_rules`. "
         "Evaluated in code by `src/ax15/trust/sop_rules.py`; no LLM decides routing or priority._", "",
         "**Decision order:** " + " → ".join(cfg["stages"]) + " → default `l1`. The first stage with a fired "
         "rule decides; rules that fired in later stages are kept in the trace as superseded.", "",
         "## Routing rules", "", "| Rule | Stage | Condition | Effect | Source |", "|---|---|---|---|---|"]
    for r in cfg["rules"]:
        eff = ", ".join(f"{k}={v}" for k, v in r["effect"].items())
        tag = " (proposed, inactive)" if r.get("active", True) is False else ""
        L.append(f"| {r['id']}{tag} | {r['stage']} | {_cond_text(r['when'])} | {eff} | {_cite(r['source'], docs)} |")
    L += ["", "## Base priority (first match; none → keep the ticket's priority)", "",
          "| Rule | Condition | Priority | Source |", "|---|---|---|---|"]
    for p in cfg["priority_base"]:
        L.append(f"| {p['id']} | {_cond_text(p['when'])} | {p['priority']} | {_cite(p['source'], docs)} |")
    L += ["", "## Priority overrides (applied in order; " + ("increases stack" if cfg.get("increases_stack", True)
                                                            else "the +1 rows raise priority one level at most")
          + "; bounded P1..P4)", "",
          "| Rule | Condition | Change | Source |", "|---|---|---|---|"]
    for o in cfg["priority_overrides"]:
        L.append(f"| {o['id']} | {_cond_text(o['when'])} | {o['change']} | {_cite(o['source'], docs)} |")
    h = cfg["handoff"]
    L += ["", "## L2 handoff fields", "",
          f"- Mandatory (SOP Step 3): {', '.join(h['mandatory'])}",
          f"- Documentation (SOP Step 5 / email template): {', '.join(h['documentation'])}",
          f"- Notify: {h['notify']['l2']} (P1 also {h['notify']['p1_also']}; rules with `notify: [facilities]` "
          f"also {h['notify'].get('facilities', '?')})",
          f"- Source: {_cite(h['source'], docs)}", "",
          "## Facts", "", "| Fact | From |", "|---|---|"]
    L += [f"| `{k}` | {v} |" for k, v in FACTS.items()]
    L += ["", "## Notes on SOP interpretation", "",
          "- **Do-not-escalate before triggers.** SOP Step 1 checks the do-not-escalate list first. The list's item 6 "
          "(third-party coordination) and the trigger *third-party integration failure* overlap; AX-15 separates them "
          "by fact: `dne_third_party_only` = the provider's own service is at fault (ISP outage, card processor down) "
          "and no ServeWell system is failing; `integration_failure` = a ServeWell integration (aggregator, KDS, "
          "payment gateway link) is failing.",
          "- **Increase overrides don't stack** (`increases_stack: false`): the +1 rows together raise the priority by "
          "one level at most; customer escalation (at least P2) and known-issue (-1) still apply. Bounded P1..P4.",
          "- **Recurrence** follows the SOP wording: the same issue reported *more than twice* in 30 days, i.e. at "
          "least 2 earlier reports plus this one. Human-approved escalations for the same asset (action_log) count "
          "as earlier reports.",
          "- **Peak hours** use the ticket's open time in IST; the window end is exclusive (14:00 is not peak).", ""]
    return "\n".join(L)


def main() -> int:
    out = OUTPUTS_DIR / "sop_rules_table.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_table(), encoding="utf-8")
    cfg = load_rules()
    print(f"[sop_rules] {len(cfg['rules'])} routing rules, {len(cfg['priority_base'])} base priority rules, "
          f"{len(cfg['priority_overrides'])} overrides -> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
