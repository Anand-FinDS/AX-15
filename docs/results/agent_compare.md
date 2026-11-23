# AX-15 1.0 vs 2.0 — side by side

_Generated 2026-11-23 02:52 UTC from the dev + chaos eval runs of `eval/agent_eval.py` (agent_eval_v1.md, agent_eval_v2_final.md) · dev split: 176 tickets in both · chaos: 36 · same judge, same answer fields shown to it._

## Dev split and pilot scope

| Metric | 1.0 all dev | 2.0 all dev | 1.0 pilot | 2.0 pilot |
|---|---:|---:|---:|---:|
| Routing L1/L2 | 68.2% | 74.4% (+6.2) | 65.4% | 80.8% (+15.4) |
| Escalation precision | 85.3% | 87.9% (+2.6) | 81.2% | 94.1% (+12.9) |
| Escalation recall | 69.8% | 69.0% (-0.9) | 48.1% | 59.3% (+11.1) |
| Escalation rate | 54.0% | 51.7% (-2.3) | 30.8% | 32.7% (+1.9) |
| kb_doc hit | 67.0% | 77.3% (+10.2) | 98.1% | 100.0% (+1.9) |
| Step fidelity (verbatim) | 99.3% | 100.0% (+0.7) | 99.5% | 100.0% (+0.5) |
| Answers with steps | 48.3% | 38.1% (-10.2) | 78.8% | 55.8% (-23.1) |
| Invalid JSON | 0.0% | 0.0% (+0.0) | 0.0% | 0.0% (+0.0) |
| Incomplete | 0.6% | 0.0% (-0.6) | 1.9% | 0.0% (-1.9) |
| Error | 0.0% | 0.0% (+0.0) | 0.0% | 0.0% (+0.0) |
| n | 176 | 176 | 52 | 52 |
| Avg tool calls | 7.3 | 10.5 | 7.7 | 10.6 |
| Avg cost / ticket | $0.0039 | $0.0022 | $0.0045 | $0.0022 |
| Avg latency | 45.0s | 15.8s | 61.9s | 16.0s |
| Median latency | 23.3s | 15.4s | 23.9s | 15.4s |

Points in brackets = 2.0 minus 1.0. Train labels only use `l1_guided` / `l2_escalation`, so `needs_clarification` / `non_it` answers count as routing misses on the dev split.

### 2.0 routing: label → agent

| Label | 2.0 | n |
|---|---|---:|
| l2_escalation | l2_escalation | 84 |
| l1_guided | l1_guided | 45 |
| l2_escalation | l1_guided | 30 |
| l1_guided | l2_escalation | 15 |
| l1_guided | l1_self_service | 2 |

### 2.0 applied SOP rules on dev (ticket counts)

| Rule | Tickets |
|---|---:|
| SOP-TRIG-INTEGRATION | 36 |
| AX-KB-GAP-NO-DOC | 27 |
| RB-WHEN-TO-ESCALATE | 26 |
| AX-VERIFY | 18 |
| SOP-TRIG-PAYMENT | 14 |
| SOP-TRIG-ERROR-CODE | 11 |
| SOP-TRIG-REQUESTED | 7 |
| SOP-TRIG-NETWORK | 5 |
| SOP-TRIG-POS-OUTAGE | 3 |
| AX-KB-GAP-SYSTEM | 2 |

## Chaos set (36 adversarial tickets)

| | 1.0 | 2.0 |
|---|---:|---:|
| Judge PASS | 11 / 36 | 18 / 36 |
| Routing match | 18 / 36 | 23 / 36 |
| Avg cost / ticket | $0.0038 | $0.0021 |

Routing match = routing is one of the expected classes (and verify mode where expected); the judge grades the whole expected behaviour against the authored rubric.

| Chaos type | 1.0 PASS | 2.0 PASS | 1.0 routing match | 2.0 routing match |
|---|---:|---:|---:|---:|
| contradictory | 1 / 4 | 3 / 4 | 3 | 3 |
| emotional | 2 / 5 | 2 / 5 | 3 | 4 |
| false_asset | 2 / 4 | 1 / 4 | 2 | 1 |
| miscategorized | 1 / 5 | 1 / 5 | 2 | 2 |
| misdirection | 1 / 5 | 4 / 5 | 1 | 3 |
| red_herring | 0 / 4 | 2 / 4 | 1 | 3 |
| unknown_system | 2 / 3 | 2 / 3 | 3 | 3 |
| vague | 2 / 6 | 3 / 6 | 3 | 4 |

| Ticket | Type | Expected | 1.0 routing | 1.0 | 2.0 routing | 2.0 | 2.0 judge reason |
|---|---|---|---|---|---|---|---|
| INC-00035 | contradictory | l2_escalation | l2_escalation | PASS | l2_escalation (handle) | **PASS** | The agent routed to l2_escalation in handle mode, did not accept the INC-00015 'already fixed' claim (it flagged it as unverified and at a different store), and escalated the recurring EOD settlement failure. Its priority of P3 and its customer-dissatisfaction rationale are weaker than the expected P2 and blocked-register-close reasoning, but the essentials are met. |
| INC-00059 | contradictory | l1_guided | l1_guided | FAIL | l2_escalation (handle) | **FAIL** | The agent routed to l2_escalation at P2 instead of the expected l1_guided handling, escalating before working the printer-offline USB runbook path that the expected behaviour requires first (escalate only if the device isn't detected after the runbook). |
| INC-00124 | contradictory | l2_escalation | l2_escalation | FAIL | l2_escalation (handle) | **PASS** | The agent routed to l2_escalation in handle mode, raised priority from P4 to P2, and did not accept the unverified INC-00015 'already fixed' claim, though it missed the expected online-orders-sync-failure.md KB context. |
| INC-00253 | contradictory | l2_escalation | l1_guided | FAIL | l2_escalation (handle) | **PASS** | The agent routed to l2_escalation in handle mode, refused to accept the INC-00015 'already fixed' claim, and handed off to L2 with webhook evidence and the 15–20 orders/hour impact, which matches the expected behaviour. Its claim that INC-00015 is open at SW-0102 is not in the ticket, but it does not change the routing. |
| INC-00045 | emotional | l2_escalation | l2_escalation | PASS | l2_escalation (handle) | **PASS** | The agent routed to l2_escalation in handle mode at P2, flagged the CMDB gap and the router asset mismatch, avoided troubleshooting the router, and made no same-day visit promise, which matches the expected essentials, though it omitted the requested display photo and its reply references checks it never listed. |
| INC-00104 | emotional | l2_escalation | l2_escalation | PASS | l2_escalation (handle) | **PASS** | The agent routed to l2_escalation in handle mode and grounded the escalation in payment impact and recurrence rather than the customer's tone, which matches the expected behaviour; the flaws (a reply that cites nonexistent checks, a v2.1 KB path, and a timestamp inconsistency) are secondary to the correct routing. |
| INC-00155 | emotional | l2_escalation + needs_clarification | l2_escalation | FAIL | l2_escalation (handle) | **FAIL** | The agent routed to L2 and set P2, but it never flagged the soft-serve vs. POS-terminal mismatch or asked the one targeted device question, instead requesting the error code and T1/T2 with an empty steps list, and it omitted the needs_clarification routing and manager notification. |
| INC-00195 | emotional | l2_escalation | l1_guided | FAIL | l2_escalation (handle) | **FAIL** | The agent set P1 and partly justified it by the customer's dissatisfaction and framing of a 'partial outage', when the expected SOP-based priority is P2 (peak, recurring, customer escalation) and P1 is reserved for full outages, so the tone-driven priority trap was not avoided. |
| INC-00198 | emotional | l1_self_service (verify) | l1_guided | FAIL | l2_escalation (verify) | **FAIL** | The agent routed to l1_guided instead of the expected l1_self_service, did not flag the third-occurrence recurrence for trend reporting (flags is empty), and did not close the already-Resolved ticket with a summary, leaving it open for store confirmation. |
| INC-00011 | false_asset | l1_guided | l2_escalation | FAIL | needs_clarification (handle) | **FAIL** | The agent flagged the invalid asset and asked for clarification, but it routed to needs_clarification instead of the expected l1_guided and gave no single-device DHCP steps, which the expected behaviour requires. |
| INC-00048 | false_asset | l2_escalation | l2_escalation | PASS | l1_guided (handle) | **FAIL** | The agent routed to l1_guided and did not escalate to L2, although the expected routing is l2_escalation after asset reconciliation: duplicate aggregator confirmations are an integration issue requiring escalation with timestamps. It also retargeted the asset and raised the priority to P2 without that escalation. |
| INC-00180 | false_asset | l1_guided | l2_escalation | FAIL | needs_clarification (handle) | **FAIL** | The agent routed to needs_clarification instead of the expected l1_guided, and it never flagged the v4.2.1 vs v5.1.1 version mismatch that the expected behaviour requires resolving before any guided steps. |
| INC-00217 | false_asset | — (verify) | l2_escalation | PASS | l2_escalation (verify) | **PASS** | The agent used verify mode, did not treat the recorded gateway fix as confirmed, and flagged the POS-9072-T5 asset as wrong and proposed POS-0081-T1 for correction, which matches the expected false-asset handling, though its l1_guided routing is debatable given the note calls the fix L2-type. |
| INC-00010 | miscategorized | needs_clarification | l1_guided | FAIL | l2_escalation (handle) | **FAIL** | The expected routing is needs_clarification (asking whether other devices are online and checking store-wide network signals), but the agent escalated straight to L2 with no clarification request and no store-wide network check, so it missed the required first step. |
| INC-00054 | miscategorized | l1_guided | l2_escalation | FAIL | l2_escalation (handle) | **FAIL** | The agent routed straight to l2_escalation and gave no steps, skipping the expected l1_guided immediate router power cycle from network-router-offline.md, and wrongly claimed no runbook exists, even though it correctly retargeted to the router and raised priority to P1. |
| INC-00130 | miscategorized | l2_escalation | l2_escalation | PASS | l2_escalation (handle) | **PASS** | The agent correctly corrected the P4 priority to P1, routed to l2_escalation in handle mode, retargeted the asset from POS-0029-T1 to the store router RTR-0029-01, and built an L2 handoff packet, avoiding the POS-fault trap, though it omitted the router power-cycle immediate step from the expected runbook. |
| INC-00242 | miscategorized | l1_guided | l1_guided | FAIL | l1_guided (handle) | **FAIL** | The agent trusted the POS category and followed a terminal startup runbook, missing the router Wi-Fi light-off signal and the network-router-offline.md steps, and it lowered priority to P3 instead of re-assessing up for a whole-store outage. |
| INC-00300 | miscategorized | l1_guided | l2_escalation | FAIL | l2_escalation (handle) | **FAIL** | The agent routed to l2_escalation and claimed no runbook fits, missing the expected l1_guided routing and the network-router-offline.md immediate steps (router power cycle) that the ticket called for. |
| INC-00005 | misdirection | non_it | needs_clarification | FAIL | non_it (handle) | **PASS** | The agent routed the ticket as non_it in handle mode, avoided troubleshooting the payment reader hardware, and politely redirected the personal card and salary issues to HR/payroll without discussing salary details. |
| INC-00127 | misdirection | l1_self_service + non_it | l1_guided | FAIL | l1_guided (handle) | **PASS** | The agent kept the paper jam as a self-service L1 handle without escalating, removed the escalation flag, redirected the salary question to HR in the store reply, and only missed the P3 downgrade, which the rubric treats as a minor priority difference. |
| INC-00225 | misdirection | non_it + l1_self_service | l2_escalation | FAIL | l1_self_service (handle) | **PASS** | The agent routed to l1_self_service with handle mode and no escalation, correctly sent the salary and hours issue to HR/payroll, kept the employee ID out of the reply, and flagged the missing KB doc rather than inventing steps, matching the expected behaviour's essentials. |
| INC-00249 | misdirection | — (verify) | non_it | PASS | non_it (verify) | **PASS** | The agent correctly chose verify mode for the already-Closed ticket, took no troubleshooting action, did not escalate, and recognized the payroll/salary issue as HR's rather than IT's, matching the expected behaviour. |
| INC-00279 | misdirection | needs_clarification | l2_escalation | FAIL | l2_escalation (handle) | **FAIL** | The agent routed straight to l2_escalation instead of needs_clarification, never asking the store to confirm whether the printer issue persists or redirecting the salary/payroll matter to HR, so it acted on the conflicting history without resolving it. |
| INC-00151 | red_herring | l2_escalation | l1_guided | FAIL | l2_escalation (handle) | **FAIL** | The agent routed correctly to L2 but escalated without collecting the runbook diagnostics (disk %, uptime, latency) the expected behaviour requires, didn't cite the prior network outage INC-00130 despite peak-only slowness, and asserted an unsupported '1 recurrence in 30 days' fact when the ticket has no related tickets or history. |
| INC-00219 | red_herring | — (verify) | l1_guided | FAIL | l2_escalation (verify) | **PASS** | The agent correctly chose verify mode, did not re-troubleshoot or escalate a resolved ticket, avoided accepting the unverified DNS root cause, and flagged the missing 0x8004 KB coverage, though it did not explicitly propose the DNS/gateway KB addition. |
| INC-00224 | red_herring | l1_guided | needs_clarification | FAIL | l2_escalation (handle) | **FAIL** | The agent routed to l2_escalation and claimed the runbook escalation criterion was met, but the viscosity and grinding-noise safety checks were never done and the expected routing is l1_guided, so it escalated prematurely and gave no steps. |
| INC-00315 | red_herring | l1_guided | l2_escalation | FAIL | l1_guided (handle) | **PASS** | The agent routed to l1_guided in handle mode, did not escalate, and guided the receipt width setting check (80mm) that the ticket hadn't tried, without relying on the ribbon red herring or repeating the completed queue clear, power cycle, and driver steps. |
| INC-00014 | unknown_system | l2_escalation | l2_escalation | PASS | l2_escalation (handle) | **PASS** | The agent routed to l2_escalation in handle mode, generated no KitchenBridge steps, flagged the knowledge-base gap and the asset/CMDB mismatch, and avoided acting on the false soft-serve asset, which matches the expected behaviour. |
| INC-00089 | unknown_system | l2_escalation | l2_escalation | FAIL | l2_escalation (handle) | **PASS** | The agent routed to l2_escalation in handle mode, stated that KitchenBridge is not in the knowledge base or CMDB, generated no KitchenBridge steps, and raised a kb_gap flag, which matches the expected behaviour; the omitted printer checks and the P2 priority are minor deviations. |
| INC-00298 | unknown_system | — (verify) | l2_escalation | PASS | l2_escalation (verify) | **FAIL** | The agent set should_escalate to false and did not clearly state that KitchenBridge v2.3 is undocumented, even though the expected behaviour requires escalating an undocumented system with a knowledge-gap flag, and it cited an unrelated runbook as kb_doc. |
| INC-00088 | vague | — (verify) | l1_guided | PASS | l1_self_service (verify) | **PASS** | The agent correctly chose verify mode for the already-Resolved ticket, validated the recorded power-cycle fix without re-troubleshooting, kept the monitoring note, and did not escalate or act on the vague description. |
| INC-00149 | vague | needs_clarification | l1_guided | FAIL | l1_guided (handle) | **FAIL** | The ticket is vague ('network down or something') and expected routing is needs_clarification with targeted questions, but the agent routed to l1_guided, assumed a payment-terminal connection fault, and gave troubleshooting steps instead of asking clarifying questions. |
| INC-00189 | vague | needs_clarification | needs_clarification | PASS | needs_clarification (handle) | **PASS** | The agent routed to needs_clarification in handle mode at P3, asked for the exact error code and timing, and flagged the CMDB gap for the missing soft-serve asset, matching the expected behaviour (the expected text also asks for a photo and machine model, which the agent's reply omits, but the essentials are met). |
| INC-00258 | vague | needs_clarification | l1_guided | FAIL | needs_clarification (handle) | **PASS** | The agent correctly routed the vague ticket to needs_clarification in handle mode without guessing a cause or escalating, and asked for the recent-changes and output details; the P2 priority and missing KB first steps are minor deviations from the expected P3 and offered steps. |
| INC-00296 | vague | l1_self_service | l1_guided | FAIL | l1_guided (handle) | **FAIL** | The agent routed to 'l1_guided' rather than the expected 'l1_self_service', which the expected text does not allow as an equivalent, and its store reply never clearly names the wrong-side-up paper loading as the likely cause. |
| INC-00307 | vague | — (verify) | l1_guided | FAIL | l1_self_service (verify) | **FAIL** | The agent raised the ticket from P3 to P1 on the basis of the store's urgent tone, which is the tone-driven priority trap, and it did not verify and close the already-resolved ticket as expected. |

## Regressions: dev tickets 1.0 routed right and 2.0 routed wrong (21; 32 the other way)

| Ticket | Pilot | Label | 1.0 | 2.0 — why |
|---|---|---|---|---|
| INC-00006 |  | l2_escalation | l2_escalation | l1_guided via no rule fired (default L1) |
| INC-00013 |  | l2_escalation | l2_escalation | l1_guided via no rule fired (default L1) |
| INC-00018 |  | l2_escalation | l2_escalation | l1_guided via no rule fired (default L1) |
| INC-00034 |  | l2_escalation | l2_escalation | l1_guided via no rule fired (default L1) |
| INC-00040 |  | l1_guided | l1_guided | l2_escalation via SOP-TRIG-INTEGRATION |
| INC-00085 | yes | l2_escalation | l2_escalation | l1_guided via no rule fired (default L1) |
| INC-00109 |  | l2_escalation | l2_escalation | l1_guided via no rule fired (default L1) |
| INC-00117 |  | l1_guided | l1_guided | l2_escalation via SOP-TRIG-INTEGRATION; flags steps_truncated:7->6 |
| INC-00154 | yes | l2_escalation | l2_escalation | l1_guided via no rule fired (default L1) |
| INC-00169 |  | l2_escalation | l2_escalation | l1_guided via no rule fired (default L1) |
| INC-00173 |  | l1_guided | l1_guided | l2_escalation via SOP-TRIG-INTEGRATION; flags steps_truncated:7->6 |
| INC-00174 |  | l2_escalation | l2_escalation | l1_guided via no rule fired (default L1) |
| INC-00176 |  | l1_guided | l1_guided | l2_escalation via AX-VERIFY |
| INC-00188 |  | l1_guided | l1_guided | l2_escalation via SOP-TRIG-INTEGRATION |
| INC-00194 |  | l2_escalation | l2_escalation | l1_guided via no rule fired (default L1) |
| INC-00214 |  | l2_escalation | l2_escalation | l1_guided via no rule fired (default L1); flags recurrence:1 in 30d |
| INC-00228 |  | l1_guided | l1_guided | l2_escalation via SOP-TRIG-INTEGRATION |
| INC-00247 |  | l2_escalation | l2_escalation | l1_guided via no rule fired (default L1); flags cmdb_gap:no Soft Serve Machine registered at store |
| INC-00282 | yes | l2_escalation | l2_escalation | l1_guided via no rule fired (default L1) |
| INC-00302 |  | l1_guided | l1_guided | l2_escalation via AX-VERIFY |
| INC-00305 | yes | l2_escalation | l2_escalation | l1_guided via no rule fired (default L1) |

### Chaos regressions: 1.0 PASS → 2.0 FAIL (2; 9 FAIL → PASS)

| Ticket | Type | 2.0 | Judge reason |
|---|---|---|---|
| INC-00048 | false_asset | l1_guided via no rule fired (default L1); flags asset_retargeted:POS-8341-T2->POS-0114-T1, version_mismatch:ticket says v4.2.1, CMDB has v4.2.2 | The agent routed to l1_guided and did not escalate to L2, although the expected routing is l2_escalation after asset reconciliation: duplicate aggregator confirmations are an integration issue requiring escalation with timestamps. It also retargeted the asset and raised the priority to P2 without that escalation. |
| INC-00298 | unknown_system | l2_escalation via AX-VERIFY; flags kb_gap | The agent set should_escalate to false and did not clearly state that KitchenBridge v2.3 is undocumented, even though the expected behaviour requires escalating an undocumented system with a knowledge-gap flag, and it cited an unrelated runbook as kb_doc. |

### kb_doc regressions (1.0 cited a relevant runbook, 2.0 did not): 1

- INC-00152: 2.0 l2_escalation via AX-KB-GAP-NO-DOC; flags kb_gap; kb_doc —

