# AX-15 2.0 — SOP rules table

_Generated from `config/sop_rules.yaml` (hash 7022d544e9da) by `python -m ax15.trust.sop_rules`. Evaluated in code by `src/ax15/trust/sop_rules.py`; no LLM decides routing or priority._

**Decision order:** verify → safety → non_it → power → do_not_escalate → escalate → clarify → default `l1`. The first stage with a fired rule decides; rules that fired in later stages are kept in the trace as superseded.

## Routing rules

| Rule | Stage | Condition | Effect | Source |
|---|---|---|---|---|
| AX-VERIFY | verify | `ticket_closed` | decision=verify | AX-15 policy > Resolved/Closed tickets -> verify mode |
| AX-SAFETY | safety | `safety_hazard` | decision=escalate, priority=P1, no_steps=True, notify=['facilities'], reply_prefix=safety, verify_flag=safety_hazard_reported | AX-15 policy > Safety hazard -> L2 + facilities, P1, no steps, fixed safety reply |
| AX-NON-IT | non_it | NOT `is_it` | decision=non_it | sop/escalation-procedure.md > Purpose / Scope (POS, network, payment terminals and related IT services) |
| AX-POWER | power | `power_instability` | decision=escalate, no_steps=True, notify=['facilities'] | AX-15 policy > Power instability -> L2 + facilities, no POS troubleshooting steps |
| SOP-DNE-1 | do_not_escalate | `dne_how_to` | decision=l1, routing=l1_self_service | sop/escalation-procedure.md > Do-Not-Escalate List > 1. Basic How-To & Training Questions |
| SOP-DNE-2 | do_not_escalate | `dne_user_error` | decision=l1, routing=l1_self_service | sop/escalation-procedure.md > Do-Not-Escalate List > 2. User Error or Incorrect Configuration |
| SOP-DNE-3 | do_not_escalate | `dne_billing` | decision=non_it, redirect=Billing@servewell.in | sop/escalation-procedure.md > Do-Not-Escalate List > 3. Billing, Account, or Licensing Issues |
| SOP-DNE-4 | do_not_escalate | `dne_maintenance` | decision=l1 | sop/escalation-procedure.md > Do-Not-Escalate List > 4. Scheduled Maintenance or Known Downtime |
| SOP-DNE-5 | do_not_escalate | `dne_feature_request` | decision=l1 | sop/escalation-procedure.md > Do-Not-Escalate List > 5. Feature Requests or Enhancement Suggestions |
| SOP-DNE-6 | do_not_escalate | `dne_third_party_only` | decision=l1 | sop/escalation-procedure.md > Do-Not-Escalate List > 6. Third-Party Service Coordination |
| SOP-DNE-7 | do_not_escalate | `dne_resolved_after_reset` AND NOT `power_instability` | decision=l1, routing=l1_self_service | sop/escalation-procedure.md > Do-Not-Escalate List > 7. General Connectivity Troubleshooting (if resolved) |
| SOP-TRIG-PAYMENT | escalate | `payment_failure` | decision=escalate | sop/escalation-procedure.md > Common Escalation Triggers > Payment processing failure |
| SOP-TRIG-POS-OUTAGE | escalate | `pos_outage_all` | decision=escalate | sop/escalation-procedure.md > Common Escalation Triggers > Complete POS system outage |
| SOP-TRIG-DATA | escalate | `data_loss` | decision=escalate | sop/escalation-procedure.md > Common Escalation Triggers > Database corruption or data loss |
| SOP-TRIG-SECURITY | escalate | `security_incident` | decision=escalate | sop/escalation-procedure.md > Common Escalation Triggers > Security incident |
| SOP-TRIG-NETWORK | escalate | (`network_infrastructure_down` OR `multiple_locations`) | decision=escalate | sop/escalation-procedure.md > Common Escalation Triggers > Network infrastructure failure |
| SOP-TRIG-INTEGRATION | escalate | `integration_failure` | decision=escalate | sop/escalation-procedure.md > Common Escalation Triggers > Third-party integration failure |
| SOP-TRIG-ERROR-CODE | escalate | `unknown_error_codes` >= 1 | decision=escalate | sop/escalation-procedure.md > Common Escalation Triggers > Error code not in knowledge base |
| SOP-TRIG-WAITED | escalate | `waited_over_30_min` | decision=escalate | sop/escalation-procedure.md > Common Escalation Triggers > Customer has already waited >30 minutes |
| SOP-TRIG-COMPLIANCE | escalate | `compliance_impact` | decision=escalate | sop/escalation-procedure.md > Common Escalation Triggers > Issue affects compliance |
| SOP-TRIG-REQUESTED | escalate | (`customer_requested_escalation` OR `expresses_dissatisfaction`) | decision=escalate | sop/escalation-procedure.md > Common Escalation Triggers > Customer requests escalation |
| SOP-STEP2-EXHAUSTED | escalate | `all_steps_tried` | decision=escalate | sop/escalation-procedure.md > Procedure > Step 2: Attempt Basic Troubleshooting (4. issue persists -> Step 3) |
| RB-WHEN-TO-ESCALATE | escalate | `runbook_escalation_met` | decision=escalate | chosen runbook > When to Escalate |
| AX-KB-GAP-SYSTEM | escalate | `unknown_system` | decision=escalate, flag=kb_gap, no_steps=True | AX-15 policy > Unknown system -> L2 with kb_gap; never invent steps |
| AX-KB-GAP-NO-DOC | escalate | `kb_no_doc` | decision=escalate, flag=kb_gap, no_steps=True | AX-15 policy > No KB doc -> L2 with kb_gap |
| AX-L2-ONLY | escalate | `l2_only_needed` | decision=escalate | AX-15 policy > L2-only procedure -> L2 (store never sees L2-only text) |
| AX-LLM-FAILED | escalate | `llm_failed` | decision=escalate, flag=llm_failed, no_steps=True | AX-15 policy > Fail safe -> L2 |
| SOP-PROPOSED-NET (proposed, inactive) | escalate | `network_issue` AND `pos_affected` AND `during_service` | decision=escalate | AX-15 policy > Proposed SOP change (not in SOP-IT-L1-002; inactive, what-if only) |
| AX-ASSET-UNVERIFIED | clarify | `asset_unverified` | decision=clarify, no_steps=True | AX-15 policy > Asset not_found -> no steps; ask the store to confirm (or L2 if a trigger fires) |
| AX-SYSTEM-MISMATCH | clarify | `system_mismatch` | decision=clarify, no_steps=True | AX-15 policy > System mismatch -> no steps; ask the store to confirm (or L2 if a trigger fires) |
| AX-VAGUE | clarify | `clarity_vague` | decision=clarify | AX-15 policy > Vague ticket -> needs_clarification |

## Base priority (first match; none → keep the ticket's priority)

| Rule | Condition | Priority | Source |
|---|---|---|---|
| SOP-PRI-P1 | (`store_wide_outage` OR `payments_unavailable` OR `multiple_locations` OR `pos_outage_all`) | P1 | sop/escalation-procedure.md > Step 4: Assess Priority > Critical (P1) |
| SOP-PRI-P2 | (`partial_outage` OR `devices_affected` >= 2) | P2 | sop/escalation-procedure.md > Step 4: Assess Priority > High (P2) |
| SOP-PRI-P3 | (`device_unusable` OR `function_degraded`) | P3 | sop/escalation-procedure.md > Step 4: Assess Priority > Medium (P3) |
| SOP-PRI-P4 | `cosmetic_only` | P4 | sop/escalation-procedure.md > Step 4: Assess Priority > Low (P4) |

## Priority overrides (applied in order; the +1 rows raise priority one level at most; bounded P1..P4)

| Rule | Condition | Change | Source |
|---|---|---|---|
| SOP-PRI-PEAK | `opened_hour_ist` hour_in [[11, 14], [18, 21]] | 1 | sop/escalation-procedure.md > Priority Override Rules > Time-sensitive context |
| SOP-PRI-RECUR | `recurrence_count` >= 2 | 1 | sop/escalation-procedure.md > Priority Override Rules > Recurring issue |
| SOP-PRI-VIP | `store_type` == corporate | 1 | sop/escalation-procedure.md > Priority Override Rules > VIP customer |
| SOP-PRI-EXTERNAL | `compliance_impact` | 1 | sop/escalation-procedure.md > Priority Override Rules > External dependency |
| SOP-PRI-CUST-ESC | `customer_requested_escalation` | at_least_P2 | sop/escalation-procedure.md > Priority Override Rules > Customer escalation |
| SOP-PRI-KNOWN | `known_issue_workaround` | -1 | sop/escalation-procedure.md > Priority Override Rules > Known issue with workaround |

## L2 handoff fields

- Mandatory (SOP Step 3): asset_id, error_codes, steps_tried, store_impact
- Documentation (SOP Step 5 / email template): summary, store, priority, sla, contact, attachments_requested
- Notify: it-l2@servewell.in (P1 also oncall@servewell.in; rules with `notify: [facilities]` also facilities-logistics@servewell.in)
- Source: sop/escalation-procedure.md > Step 3: Collect Mandatory Escalation Information; Step 5; Escalation Email Template

## Facts

| Fact | From |
|---|---|
| `ticket_closed` | code: ticket status Resolved/Closed |
| `ticket_status` | code: ticket status |
| `ticket_priority` | code: the priority on the ticket |
| `is_it` | classify |
| `clarity_vague` | classify: clarity == vague |
| `dne_how_to` | classify |
| `dne_user_error` | classify |
| `dne_billing` | classify |
| `dne_maintenance` | classify |
| `dne_feature_request` | classify |
| `dne_third_party_only` | classify |
| `dne_resolved_after_reset` | classify |
| `safety_hazard` | classify: fire, smoke, burning smell, sparks, electric shock or exposed wiring (incl. suspected) |
| `power_instability` | classify: repeated loss of mains power at the store |
| `payment_failure` | classify |
| `pos_outage_all` | classify |
| `data_loss` | classify |
| `security_incident` | classify |
| `network_infrastructure_down` | classify |
| `multiple_locations` | classify |
| `integration_failure` | classify |
| `waited_over_30_min` | classify |
| `compliance_impact` | classify |
| `customer_requested_escalation` | classify |
| `expresses_dissatisfaction` | classify |
| `unknown_error_codes` | code: classify error codes not found in the KB text (list) |
| `unknown_system` | code: a named system is in neither the KB, the CMDB nor the version matrix |
| `unknown_systems` | code: the unknown names (list) |
| `asset_unverified` | code: asset missing / not in CMDB and no single matching asset at the store |
| `system_mismatch` | code: get_asset mismatch_kind == system (ticket names another system) |
| `kb_no_doc` | route: pick_doc returned none |
| `l2_only_needed` | resolve: the fix is an L2-only procedure (stub) |
| `all_steps_tried` | resolve: every step of the chosen section was already tried |
| `runbook_escalation_met` | resolve: a 'When to Escalate' criterion of the runbook is met (verified in code) |
| `llm_failed` | code: name of the graph node whose model call failed (classify / resolve) |
| `network_issue` | code: network/Wi-Fi symptom (device router, infra down, or network words in clean_symptom) |
| `pos_affected` | code: POS / payment terminal affected |
| `during_service` | code: customer-facing or peak hour |
| `intents` | classify: [{kind: it|non_it, summary}] from the description text |
| `store_wide_outage` | classify |
| `payments_unavailable` | classify |
| `partial_outage` | classify |
| `devices_affected` | classify (int) |
| `device_unusable` | classify |
| `function_degraded` | classify |
| `cosmetic_only` | classify |
| `opened_hour_ist` | code: hour of date_opened in IST (float) |
| `recurrence_count` | code: earlier same-issue reports at the store in 30 days (+ approved escalations, same asset) |
| `store_type` | code: get_store |
| `known_issue_workaround` | code+classify: matches a version-matrix known bug with a fix |

## Notes on SOP interpretation

- **Do-not-escalate before triggers.** SOP Step 1 checks the do-not-escalate list first. The list's item 6 (third-party coordination) and the trigger *third-party integration failure* overlap; AX-15 separates them by fact: `dne_third_party_only` = the provider's own service is at fault (ISP outage, card processor down) and no ServeWell system is failing; `integration_failure` = a ServeWell integration (aggregator, KDS, payment gateway link) is failing.
- **Increase overrides don't stack** (`increases_stack: false`): the +1 rows together raise the priority by one level at most; customer escalation (at least P2) and known-issue (-1) still apply. Bounded P1..P4.
- **Recurrence** follows the SOP wording: the same issue reported *more than twice* in 30 days, i.e. at least 2 earlier reports plus this one. Human-approved escalations for the same asset (action_log) count as earlier reports.
- **Peak hours** use the ticket's open time in IST; the window end is exclusive (14:00 is not peak).
