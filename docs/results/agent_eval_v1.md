# AX-15 v1 — agent eval

_Generated 2026-11-22 16:18 UTC · model `claude-haiku-5-5` · scope: **dev split** (176 tickets run) · held-out: **44 (not used here; see heldout_variance.md)** · chaos: 36 tickets._

## Dev split

| Scope | n | Routing exact | Routing L1/L2 | Esc. precision | Esc. recall | kb_doc hit | Step fidelity | Invalid JSON | Incomplete | Error | Avg tools | Avg cost | Avg latency | Median latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| all dev | 176 | 68.2% | 68.2% | 85.3% | 69.8% | 67.0% | 99.3% | 0.0% | 0.6% | 0.0% | 7.3 | $0.0039 | 45.0s | 23.3s |
| pilot scope (pos-/printer- main runbook) | 52 | 65.4% | 65.4% | 81.2% | 48.1% | 98.1% | 99.5% | 0.0% | 1.9% | 0.0% | 7.7 | $0.0045 | 61.9s | 23.9s |

Routing exact compares with the train label (`l1_guided` / `l2_escalation`); L1/L2 collapses `l1_*` to L1 (train labels have no self-service / clarification / non-IT classes). kb_doc hit = the agent's `kb_doc` is in `relevant_kb_docs`. Step fidelity = share of all returned steps found verbatim (whitespace-normalised, list marker ignored) in `kb_chunks.text`.

### Routing: label → agent

| Label | Agent | n |
|---|---|---:|
| l2_escalation | l2_escalation | 80 |
| l1_guided | l1_guided | 40 |
| l2_escalation | l1_guided | 29 |
| l1_guided | l2_escalation | 15 |
| l1_guided | needs_clarification | 7 |
| l2_escalation | needs_clarification | 4 |
| l2_escalation | (incomplete) | 1 |

## Chaos set: 11 / 36 PASS (judge) · routing match 18 / 36

Routing match = the agent's routing is one of the expected classes (and verify mode when expected). The judge grades the whole expected behaviour, so the two counts differ.

| Ticket | Chaos type | Expected | Agent routing | Mode | Routing match | Verdict | Judge reason |
|---|---|---|---|---|---|---|---|
| INC-00035 | contradictory | l2_escalation | l2_escalation | handle | yes | **PASS** | The agent routed to l2_escalation in handle mode, did not accept the false INC-00015 'already fixed' claim, and escalated because the only runbook covers FoodTech versions, not OrbitPOS v2.1, though its store reply pushes back on the claim more directly than the expected 'note internally' guidance. |
| INC-00059 | contradictory | l1_guided | l1_guided | handle | yes | **FAIL** | The agent refuted the false INC-00015 link and routed to l1_guided/handle, but it never retargets the ticket from the POS asset to the actual printer asset PRN-0066-P1, which the expected behaviour calls out explicitly. |
| INC-00124 | contradictory | l2_escalation | l2_escalation | handle | yes | **FAIL** | The routing (l2_escalation, handle) and the rejection of the unverified INC-00015 claim are correct, but the agent kept priority at P4 when the expected behaviour explicitly says P4 is wrong for a payment double-charge, and it did not identify the payment/third-party SOP immediate trigger. |
| INC-00253 | contradictory | l2_escalation | l1_guided | handle | no | **FAIL** | The agent routed to l1_guided with should_escalate set to false and sent the store through another L1 test-order step, when the expected behaviour is an immediate l2_escalation because the L1 steps are exhausted and the integration team needs the webhook evidence. |
| INC-00045 | emotional | l2_escalation | l2_escalation | handle | yes | **PASS** | The agent routed to l2_escalation in handle mode, caught the router asset mismatch and the KB gap, avoided troubleshooting the router, and sent an empathetic reply asking for a photo without promising a same-day visit; keeping P3 instead of raising to P2 is a minor priority difference the rubric allows. |
| INC-00104 | emotional | l2_escalation | l2_escalation | handle | yes | **PASS** | The agent routed to l2_escalation in handle mode, avoided the emotional-tone trap by escalating on the real impact (payments, ordering, outage), gave a plain-language reply, advised no more reboots, and invented no v2.2 DHCP procedure; its P2 priority raise is a defensible variant of the expected priority-up. |
| INC-00155 | emotional | l2_escalation + needs_clarification | l2_escalation | handle | yes | **FAIL** | The agent escalated correctly to P2 and avoided the soft-serve trap, but it never asked the store which device is affected (POS T2 or a soft-serve machine), which the expected behaviour names as the key clarification, and it omitted needs_clarification from routing. |
| INC-00195 | emotional | l2_escalation | l1_guided | handle | no | **FAIL** | The expected routing is l2_escalation, but the agent routed to l1_guided with should_escalate set to false and deferred escalation until after another round of L1 troubleshooting, despite the chronic recurring issue, the submitter's escalation request, and the same-store recurrence with INC-00104. |
| INC-00198 | emotional | l1_self_service (verify) | l1_guided | verify | no | **FAIL** | The agent routed to l1_guided, but the expected routing is l1_self_service, and the expected text does not list l1_guided as an allowed equivalent, even though the verify mode and recurrence handling are otherwise correct. |
| INC-00011 | false_asset | l1_guided | l2_escalation | handle | no | **FAIL** | The agent routed to l2_escalation with no runbook steps, but the expected routing is l1_guided: it should flag the invalid asset, run the single-device network-devices-not-connecting steps, and escalate only if the device still gets no lease. |
| INC-00048 | false_asset | l2_escalation | l2_escalation | handle | yes | **PASS** | The agent flagged the false asset POS-8341-T2 as absent from the CMDB, noted the store only has POS-0114-T1 on v4.2.2 (version mismatch), and escalated to l2_escalation in handle mode rather than troubleshooting an unverified device or inventing steps. |
| INC-00180 | false_asset | l1_guided | l2_escalation | handle | no | **FAIL** | The expected routing is l1_guided (handle at L1 by resolving the version, asking which printer, then following pos-printer-integration.md), but the agent routed to l2_escalation and escalated without guiding the store through the runbook. |
| INC-00217 | false_asset | — (verify) | l2_escalation | verify | yes | **PASS** | The agent correctly verified the Resolved ticket without re-troubleshooting, refused to accept the unknown asset POS-9072-T5, and escalated the gateway-config fix to L2, which the expected text calls appropriate; it did not name the proposed correction to POS-0081-T1, but asked for the correct terminal ID instead. |
| INC-00010 | miscategorized | needs_clarification | l1_guided | handle | no | **FAIL** | The agent routed the ticket as l1_guided and applied the POS payment-error runbook instead of routing to needs_clarification and checking the store-wide network path via network-router-offline.md, though it did ask about T2. |
| INC-00054 | miscategorized | l1_guided | l2_escalation | handle | no | **FAIL** | The agent routed to l2_escalation with priority P2 and gave no router power-cycle steps, whereas the expected handling is l1_guided with an immediate router power cycle and re-prioritisation to P1 for a whole-store outage. |
| INC-00130 | miscategorized | l2_escalation | l2_escalation | handle | yes | **PASS** | The agent routed to l2_escalation in handle mode, corrected the recorded P4 to P1 for a store-wide outage during peak hours, and recognised the non-POS network cause, avoiding the POS-fault trap. It did miss the router-retarget and router power-cycle steps the expected text mentions, but those are secondary to the required escalation and priority correction. |
| INC-00242 | miscategorized | l1_guided | l1_guided | handle | yes | **FAIL** | Routing is correct and the agent spotted the router, but it followed the POS startup-failure runbook with terminal-only steps and never used network-router-offline.md or its router power-cycle steps, which the expected handling requires. |
| INC-00300 | miscategorized | l1_guided | l2_escalation | handle | no | **FAIL** | The agent routed to l2_escalation instead of the expected l1_guided router-power-cycle steps, and it did not recategorise to Wi-Fi/Network or use the network-router-offline runbook, instead relying on a low-score payment-error runbook for steps; it also told the store not to reset the router, withholding the expected immediate router steps. |
| INC-00005 | misdirection | non_it | needs_clarification | handle | no | **FAIL** | The agent routed the ticket as needs_clarification instead of the expected non_it, and it started device troubleshooting (asking which device rejected the card and whether it works on the POS) rather than redirecting the non-IT request to HR/payroll. |
| INC-00127 | misdirection | l1_self_service + non_it | l1_guided | handle | no | **FAIL** | The agent routed to 'l1_guided' instead of the expected 'l1_self_service', and set priority to P1 rather than the expected P3 down-grade, which appears driven by the 'ASAP' urgency in the ticket. |
| INC-00225 | misdirection | non_it + l1_self_service | l2_escalation | handle | no | **FAIL** | The agent routed the staff password/login access problem to l2_escalation, but the expected routing is non_it and l1_self_service, with password/access issues on the do-not-escalate list. The handoff also left the employee ID unredacted, and priority stayed at P2 instead of being lowered. |
| INC-00249 | misdirection | — (verify) | non_it | verify | yes | **PASS** | The agent chose verify mode, took no troubleshooting steps, did not escalate, and correctly confirmed the ticket was closed with the payroll issue belonging to HR under INC-00250, matching the expected no-action verification. |
| INC-00279 | misdirection | needs_clarification | l2_escalation | handle | no | **FAIL** | The expected routing is needs_clarification (asking the store to confirm whether the printer issue persists and redirecting the salary matter to HR), but the agent routed directly to l2_escalation without asking for confirmation, so it missed the required clarification step. |
| INC-00151 | red_herring | l2_escalation | l1_guided | handle | no | **FAIL** | The agent routed to l1_guided with should_escalate=false and sent L1 troubleshooting steps, but the expected routing is l2_escalation because the issue persisted after the cache clear and restarts, and the agent did not create a complete L2 packet. |
| INC-00219 | red_herring | — (verify) | l1_guided | verify | yes | **FAIL** | The agent correctly verified the recorded fix without re-troubleshooting, but it never proposed the expected KB update (adding a DNS/gateway check for 0x8004 to pos-startup-failure.md) for HITL review, which the expected behaviour explicitly calls out. |
| INC-00224 | red_herring | l1_guided | needs_clarification | handle | no | **FAIL** | The agent routed to needs_clarification with no steps, when the expected routing is l1_guided with safety-first guidance and viscosity checks, so it withheld the troubleshooting the ticket needed even though it correctly flagged the asset mismatch. |
| INC-00315 | red_herring | l1_guided | l2_escalation | handle | no | **FAIL** | The agent routed the ticket to l2_escalation with an empty steps list, when the expected routing is l1_guided: it should have guided the untried print-width setting (Resolution Step 3) before L2 involvement. |
| INC-00014 | unknown_system | l2_escalation | l2_escalation | handle | yes | **PASS** | The agent routed to l2_escalation in handle mode, refused to generate KitchenBridge steps since the system is absent from the knowledge base, flagged the kb_gap and asset/system mismatch, and collected vendor and integration details from the store as the expected behaviour requires. |
| INC-00089 | unknown_system | l2_escalation | l2_escalation | handle | yes | **FAIL** | The agent routed correctly to l2_escalation without generating KitchenBridge steps, but it gave no printer-side checks (steps is empty), never stated plainly that KitchenBridge is undocumented, and its flags omit a knowledge-gap flag. |
| INC-00298 | unknown_system | — (verify) | l2_escalation | verify | yes | **PASS** | The agent stayed in verify mode, refused to accept the unverifiable KitchenBridge v2.3 fix and v3.1/v4.8 claims, generated no KitchenBridge steps, escalated with knowledge-gap and unverified-claim flags, and asked the store for device and version details. |
| INC-00088 | vague | — (verify) | l1_guided | verify | yes | **PASS** | The agent correctly identified the ticket as already Resolved and used verify mode with no escalation, validating the recorded power-cycle fix and adding the recurrence-monitoring note, though its l1_guided routing does not match the empty expected routing list. |
| INC-00149 | vague | needs_clarification | l1_guided | handle | no | **FAIL** | The ticket is vague and the expected behaviour is needs_clarification (asking targeted questions first), but the agent routed to l1_guided and immediately prescribed a payment-terminal troubleshooting procedure with an unverified runbook and a P1 priority rather than asking clarifying questions about scope and symptoms. |
| INC-00189 | vague | needs_clarification | needs_clarification | handle | yes | **PASS** | The agent routed to needs_clarification in handle mode, asked for the error code photo and machine model/serial, and flagged the asset mismatch (router registered instead of a soft serve machine) as a system_mismatch/kb_gap, matching the expected behaviour. |
| INC-00258 | vague | needs_clarification | l1_guided | handle | no | **FAIL** | The agent routed the vague ticket as l1_guided instead of the expected needs_clarification, even though its reply asks for a receipt photo and the expected behaviour is to request clarification first. |
| INC-00296 | vague | l1_self_service | l1_guided | handle | no | **FAIL** | The agent routed to l1_guided instead of the expected l1_self_service and never checks paper roll orientation, the likely cause of blank thermal receipts that the expected behaviour says to guide first; it instead steers toward LED status and encoding fixes. |
| INC-00307 | vague | — (verify) | l1_guided | verify | yes | **FAIL** | The ticket is already Resolved and should be verified and closed with no routing, but the agent set routing to l1_guided, sent the store new troubleshooting steps, and raised priority to P1 from the urgent tone instead of keeping the recorded P3. |

| Chaos type | PASS |
|---|---:|
| contradictory | 1 / 4 |
| emotional | 2 / 5 |
| false_asset | 2 / 4 |
| miscategorized | 1 / 5 |
| misdirection | 1 / 5 |
| red_herring | 0 / 4 |
| unknown_system | 2 / 3 |
| vague | 2 / 6 |

## 5 worst failures

| # | Ticket | Set | Severity | What went wrong |
|---:|---|---|---:|---|
| 1 | INC-00099 | dev | 6 | missed escalation; routing l1_guided vs l2_escalation; kb_doc runbooks/network-slow.md not relevant |
| 2 | INC-00245 | dev | 6 | missed escalation; routing l1_guided vs l2_escalation; kb_doc runbooks/pos-payment-error.md not relevant |
| 3 | INC-00122 | dev | 5 | missed escalation; routing l1_guided vs l2_escalation |
| 4 | INC-00082 | dev | 5 | missed escalation; routing l1_guided vs l2_escalation |
| 5 | INC-00087 | dev | 5 | missed escalation; routing l1_guided vs l2_escalation |

## Cost

- Agent runs: **$0.8319** total for 212 tickets ($0.0000 new on this run; the rest from cache) · chaos judge: $0.0175
- Session spend ledger (all API calls today): $0.0465 of the $3.00 cap

