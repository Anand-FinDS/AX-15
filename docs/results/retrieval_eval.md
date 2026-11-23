# AX-15 — Doc-level routing eval

_Generated 2026-11-22 16:30 UTC · scope: **dev split** · **176 tickets scored** · held-out: **44 tickets (not scored)** · audience `store` · docs ranked from top 20 chunks._

**Locked routing:** `route_doc` defaults: dense search, top 20 chunks, doc_score = max chunk RRF + 0.005 × (extra chunks), top 3 `runbooks/` docs; then `pick_doc` picks one or none. Query = ticket subject + description only. Primary runbook = `relevant_kb_docs` entries under `runbooks/`; 1 ticket(s) with no runbook label excluded. Pilot scope = main runbook `pos-*` / `printer-*` (n = 52).

## Locked pipeline: three outcomes (route_doc → pick_doc)

| Scope | n | Right | Escalated (none) | Wrong | Routing rank 1 alone: right / wrong |
|---|---:|---:|---:|---:|---:|
| overall | 175 | 133 (76.0%) | 32 (18.3%) | 10 (5.7%) | 82.9% / 17.1% |
| pilot | 52 | 52 (100.0%) | 0 (0.0%) | 0 (0.0%) | 98.1% / 1.9% |

- **Right** = picked a primary runbook · **Escalated** = picked none (no candidate fits, hand to L2) · **Wrong** = picked another runbook (the costly case).
- Picker `claude-haiku-5-5`: 308,977 in / 37,450 out tokens, **$0.0496**; 175 of 175 from the picker cache; 0 invalid after retry; 0 retried.

### Wrong picks (10)

| Ticket | Pilot | Expected runbook(s) | Candidates | Picked | Reason |
|---|---|---|---|---|---|
| INC-00013 |  | network-devices-not-connecting, network-router-offline | network-devices-not-connecting, network-slow, network-guest-wifi | network-slow | The ticket describes a site-wide DHCP pool exhaustion affecting many devices, which matches the runbook's DHCP Pool Exhaustion resolution path and its DHCP pool status check, whereas the other candidates address single-device connectivity or the guest SSID only. |
| INC-00018 |  | network-devices-not-connecting, network-slow, pos-software-update | pos-slow-performance, pos-payment-error, network-slow | pos-payment-error | The ticket describes a terminal losing network connectivity mid-checkout and queuing transactions, which the payment runbook covers through its network reconnection resolution and transaction-timeout guidance, while the other two cover performance degradation and bandwidth issues rather than disconnections. |
| INC-00063 |  | network-devices-not-connecting, network-slow, pos-slow-performance | pos-payment-error, pos-slow-performance, pos-startup-failure | pos-payment-error | The ticket's intermittent connection drops causing payment processing issues match the payment runbook's Step 3 network connectivity check and Resolution 1 for network/cable reconnection (E409), which addresses the underlying connectivity problem rather than performance or startup issues. |
| INC-00099 |  | network-devices-not-connecting | network-guest-wifi, network-devices-not-connecting, network-slow | network-slow | The ticket's underlying cause is DHCP pool exhaustion, and this runbook has a dedicated DHCP pool check (Step 3) and a resolution path for that exact cause, though its symptoms are slowness rather than outright connection failure. |
| INC-00107 |  | network-devices-not-connecting, network-router-offline, network-slow | pos-payment-error, pos-slow-performance, pos-startup-failure | pos-payment-error | Its Resolution 1 directly addresses network/cable reconnection for terminal disconnects, and it covers transaction timeouts, which matches the stuck orders in the ticket. |
| INC-00121 |  | network-router-offline, network-devices-not-connecting, network-slow | pos-slow-performance, pos-payment-error, network-slow | pos-payment-error | Its Step 3 network connectivity check and Resolution 1 (network/cable reconnection) address intermittent terminal disconnects that block transactions, whereas the other candidates target lag and degraded-but-connected bandwidth rather than dropped connections. |
| INC-00194 |  | network-devices-not-connecting | network-devices-not-connecting, network-slow, network-guest-wifi | network-slow | The ticket's root cause is DHCP pool exhaustion on the NL-3000, and this runbook has a dedicated DHCP pool check and a 'DHCP Pool Exhaustion' resolution path, even though its overview is framed around slowness. |
| INC-00214 |  | network-devices-not-connecting, network-router-offline | network-devices-not-connecting, network-guest-wifi, network-slow | network-slow | Its Resolution Path A directly addresses DHCP pool exhaustion, the root cause here, whereas the other two runbooks cover isolated single-device failures or guest-only SSID outages, which don't match a location-wide DHCP pool problem. |
| INC-00245 |  | network-devices-not-connecting, network-slow, pos-slow-performance | pos-slow-performance, pos-payment-error, network-slow | pos-payment-error | The ticket describes a single POS terminal dropping its network connection during checkout so orders fail, which matches the payment runbook's network/cable reconnection resolution (E409) and its terminal connectivity checks, whereas the other candidates address general slowness or site-wide bandwidth problems. |
| INC-00290 |  | network-devices-not-connecting, network-router-offline, network-slow | pos-payment-error, pos-slow-performance, pos-startup-failure | pos-payment-error | The ticket's intermittent network disconnects affecting order processing match the payment runbook's Step 3 connectivity check and Resolution 1 for network/cable reconnection (Error E409), which addresses the underlying connectivity fault rather than the slow-performance or startup issues. |

### Escalated (none) (32)

| Ticket | Pilot | Expected runbook(s) | Candidates | Picked | Reason |
|---|---|---|---|---|---|
| INC-00007 |  | online-orders-sync-failure, network-router-offline, network-slow | online-orders-sync-failure, pos-payment-error, pos-end-of-day | none | The ticket concerns loyalty points failing to credit to customer accounts after POS checkout, while none of the candidates cover loyalty sync; the online orders runbook covers portal-to-POS order sync, the payment runbook covers payment declines and terminal errors, and the EOD runbook covers settlement, so none address the loyalty integration problem. |
| INC-00020 |  | online-orders-not-on-kds, online-orders-sync-failure, online-orders-aggregator-disconnect | online-orders-aggregator-disconnect, online-orders-sync-failure, online-orders-not-on-kds | none | The ticket's orders are arriving and visible in the POS queue, but auto-accept has stopped and they wait in 'pending acceptance'. None of the candidates covers an auto-accept setting or failure; the KDS and sync runbooks match only on keywords like 'kitchen display' and 'orders'. |
| INC-00022 |  | online-orders-sync-failure, online-orders-not-on-kds, online-orders-aggregator-disconnect | online-orders-not-on-kds, online-orders-sync-failure, online-orders-menu-mismatch | none | The ticket describes duplicate orders appearing on the KDS, but none of the candidates cover duplication: the first covers orders missing from the KDS, the second covers orders missing from the POS, and the third covers menu data mismatches. |
| INC-00023 |  | online-orders-sync-failure, online-orders-not-on-kds, online-orders-aggregator-disconnect, network-router-offline, network-slow | online-orders-sync-failure, online-orders-not-on-kds, online-orders-aggregator-disconnect | none | The ticket is about OrbitPOS v2.0's auto-accept setting failing while orders still arrive in pending status, and none of the candidates cover auto-accept; they address orders not reaching the POS, not displaying on the KDS, or aggregator disconnects. |
| INC-00026 |  | soft-serve-error-codes | soft-serve-not-cooling, soft-serve-overcooling, soft-serve-motor-alarm | none | The ticket describes inconsistent portion sizes from dispensing (some cones small, others correct) with the machine running, which is not a cooling, frozen-product, or motor overload problem, and none of the candidates address dispensing volume or portion calibration. |
| INC-00033 |  | online-orders-sync-failure, online-orders-aggregator-disconnect, network-router-offline, network-slow | online-orders-sync-failure, online-orders-menu-mismatch, online-orders-not-on-kds | none | The ticket describes duplicate orders and confirmations, but none of the candidates cover duplication; the sync failure runbook is closest since webhook retries could cause it, yet its procedures target orders missing from POS, not duplicates. |
| INC-00037 |  | soft-serve-error-codes | soft-serve-motor-alarm | none | The ticket is a low mix level alert (a refill/supply question on a NetLink NL-3000 still dispensing), whereas the only candidate covers a motor overload alarm caused by viscosity, friction, or hardware failure, so it does not address the actual problem. |
| INC-00042 |  | online-orders-aggregator-disconnect, online-orders-sync-failure, network-router-offline, network-slow | online-orders-sync-failure, online-orders-not-on-kds, network-slow | none | The ticket describes the online ordering portal being fully unreachable with connection timeouts while the router is healthy, but none of the candidates cover a portal outage: the sync-failure and KDS runbooks concern orders not reaching POS or KDS, and the slow-network runbook covers degraded bandwidth rather than a complete outage. |
| INC-00051 |  | network-vpn, network-router-offline, online-orders-sync-failure | network-vpn, network-router-offline, network-devices-not-connecting | none | The ticket describes intermittent IPSec tunnel drops with Phase 1 authentication failures on a Cisco SG350 across multiple devices, while the candidates cover generic VPN service restarts for NetLink devices, a fully offline router, and single-device connectivity issues, none of which address recurring IPSec negotiation or authentication failure. |
| INC-00057 |  | online-orders-sync-failure, network-router-offline | online-orders-sync-failure, pos-payment-error, pos-startup-failure | none | The ticket is a loyalty points sync failure (error LSY-4002) on OrbitPOS v2.0, which none of the candidates address: the online orders runbook covers portal-to-POS order sync, the payment runbook covers card payment errors, and the startup runbook covers boot failures. |
| INC-00069 |  | online-orders-sync-failure, online-orders-aggregator-disconnect, network-router-offline, network-slow | online-orders-sync-failure, online-orders-aggregator-disconnect, online-orders-menu-mismatch | none | The ticket is a full portal outage where customers cannot load or place orders, while the closest candidate (Online Orders Sync Failure) covers orders not reaching the POS, and its only portal-access step is a browser cache clear that doesn't address an unreachable portal. |
| INC-00079 |  | soft-serve-not-cooling, soft-serve-error-codes, soft-serve-motor-alarm | soft-serve-not-cooling, soft-serve-motor-alarm, soft-serve-overcooling | none | The ticket describes a machine with no power at all (no lights, sounds, or response), but all three candidates assume the unit is powered and address cooling, motor, or freezing symptoms, so none addresses the underlying power failure. |
| INC-00112 |  | soft-serve-not-cooling, soft-serve-overcooling, soft-serve-error-codes, soft-serve-motor-alarm | soft-serve-not-cooling, soft-serve-overcooling, soft-serve-motor-alarm | none | The ticket describes inconsistent portion volumes with no error codes and no reported soft or frozen product, while the candidates cover product too soft, product frozen solid, and motor overload alarms, none of which match portion-size variation. |
| INC-00115 |  | soft-serve-error-codes | soft-serve-not-cooling, soft-serve-motor-alarm, soft-serve-error-codes | none | The ticket describes a unit that won't power on at all, with no lights, no error codes, and no motor or cooling activity, and none of the candidates covers a total power failure: the cooling and motor runbooks assume the machine is running, and the error-code runbook is keyed to displayed codes. |
| INC-00119 |  | online-orders-sync-failure, network-router-offline, network-slow | online-orders-sync-failure, pos-payment-error, pos-end-of-day | none | The ticket concerns POS-to-loyalty-backend sync of points, and none of the candidates cover loyalty program sync; the online orders runbook addresses portal-to-POS order webhooks, which is a different data flow. |
| INC-00136 |  | soft-serve-error-codes | soft-serve-motor-alarm, soft-serve-not-cooling | none | The ticket concerns a low mix cartridge level triggering a kiosk alert, which neither the motor overload runbook (current draw/viscosity/grinding) nor the not-cooling runbook (temperature/compressor) addresses. |
| INC-00143 |  | soft-serve-not-cooling, soft-serve-error-codes, soft-serve-motor-alarm | soft-serve-not-cooling, soft-serve-overcooling, soft-serve-motor-alarm | none | The ticket describes a complete power failure (no display, no compressor, no response, no error codes), which none of the candidates address: the cooling runbook covers product that is too soft, the overcooling runbook covers frozen product, and the motor alarm runbook covers an overload error code. |
| INC-00150 |  | soft-serve-error-codes | soft-serve-motor-alarm, soft-serve-cleaning-stuck, soft-serve-not-cooling | none | The ticket concerns a persistent mix level low alert and sensor/level-detection fault, which none of the candidates address: the motor overload, cleaning cycle stuck, and not-cooling runbooks cover different failure modes. |
| INC-00152 |  | online-orders-sync-failure, online-orders-aggregator-disconnect, network-router-offline, network-slow | online-orders-sync-failure, online-orders-aggregator-disconnect, online-orders-menu-mismatch | none | The ticket is a web-portal front-end outage (blank screen, mobile and POS working, cache already cleared), while the closest candidate, the sync-failure runbook, targets orders not reaching the POS, and the others cover aggregator disconnects and menu data. |
| INC-00157 |  | online-orders-sync-failure, online-orders-aggregator-disconnect, network-router-offline, network-devices-not-connecting | online-orders-sync-failure, online-orders-aggregator-disconnect, online-orders-menu-mismatch | none | The ticket is a full outage of the online ordering portal itself (customers cannot reach it and get errors), which none of the candidates address; the sync, aggregator, and menu runbooks cover orders not reaching POS, aggregator disconnects, and menu data, and the sync runbook's cache-clearing step is a staff-side workaround that doesn't fix a customer-facing outage. |
| INC-00159 |  | online-orders-sync-failure, online-orders-aggregator-disconnect, network-router-offline | online-orders-sync-failure, online-orders-aggregator-disconnect, online-orders-menu-mismatch | none | The ticket describes a total customer-facing portal outage with HTTP 503 errors and a suspected backend problem, whereas all three candidates cover order-sync failures, aggregator disconnects, or menu mismatches, none of which address a complete portal unavailability. |
| INC-00160 |  | online-orders-sync-failure, online-orders-not-on-kds, online-orders-aggregator-disconnect, network-router-offline, network-slow | online-orders-sync-failure, online-orders-menu-mismatch, online-orders-not-on-kds | none | The ticket is about orders arriving but staying in pending instead of being auto-accepted, and none of the candidates cover auto-accept behavior; the sync, menu, and KDS runbooks address orders not reaching the POS, stale menu data, and orders missing from the KDS. |
| INC-00167 |  | online-orders-sync-failure, online-orders-aggregator-disconnect, network-router-offline | online-orders-sync-failure, online-orders-aggregator-disconnect, online-orders-menu-mismatch | none | The ticket is a complete portal outage with 503 errors on all devices while the POS works, but none of the candidates cover a portal being unreachable: the sync-failure runbook covers orders not reaching the POS, the aggregator runbook covers Zomato/Swiggy disconnects, and the menu runbook covers stale item data. |
| INC-00233 |  | online-orders-not-on-kds, online-orders-sync-failure, pos-software-update | online-orders-sync-failure, online-orders-aggregator-disconnect, online-orders-not-on-kds | none | The ticket is about the auto-accept function failing while orders already reach the pending queue, but none of the candidates cover auto-accept; they address orders not syncing, aggregator disconnects, or orders missing from the KDS. |
| INC-00236 |  | online-orders-sync-failure, pos-printer-integration, online-orders-not-on-kds | online-orders-sync-failure, online-orders-not-on-kds, online-orders-menu-mismatch | none | The ticket is about duplicate orders being created in the POS, KDS, and printer from a single online submission, but all three candidates cover missing orders, sync failures, or menu data, and none addresses duplication. |
| INC-00256 |  | online-orders-not-on-kds, online-orders-sync-failure, online-orders-aggregator-disconnect | online-orders-sync-failure, online-orders-not-on-kds, online-orders-aggregator-disconnect | none | The ticket is about the auto-accept setting failing while orders still arrive as pending, and none of the candidates cover auto-accept configuration; the closest (KDS not showing orders) addresses a different failure where orders never reach the kitchen display. |
| INC-00261 |  | network-router-offline, network-slow, online-orders-aggregator-disconnect | online-orders-sync-failure, online-orders-aggregator-disconnect, network-slow | none | The ticket is a full outage of the ServeWell portal itself (unreachable from every device and network, with POS and bandwidth fine), which none of the candidates address: sync failure covers orders not reaching POS, aggregator disconnect covers Zomato/Swiggy integration status, and slow network covers degraded bandwidth that the ticket rules out. |
| INC-00265 |  | kiosk-touchscreen, kiosk-frozen | kiosk-frozen, kiosk-software-crash, kiosk-touchscreen | none | The ticket describes the display blanking after 2 minutes of inactivity and recovering on touch, which points to a screen timeout or power-saving setting; none of the candidates cover that, and the frozen-screen runbook addresses unresponsive screens, not ones that wake on touch. |
| INC-00286 |  | online-orders-sync-failure, network-router-offline, network-slow | online-orders-sync-failure, pos-payment-error, pos-end-of-day | none | The ticket concerns the loyalty sync module failing silently to post points to customer accounts, while none of the candidates cover loyalty program synchronization: the online orders runbook covers portal-to-POS order sync, the payment runbook covers card processing errors, and the end-of-day runbook covers settlement. |
| INC-00289 |  | soft-serve-error-codes, soft-serve-motor-alarm | soft-serve-not-cooling, soft-serve-error-codes, soft-serve-motor-alarm | none | The ticket describes a total power failure with no lights, sounds, display, or error codes, and none of the candidates covers a dead unit that won't power on; the error-code runbook depends on a displayed code and the other two address cooling and motor faults. |
| INC-00304 |  | soft-serve-error-codes | soft-serve-not-cooling, soft-serve-error-codes, soft-serve-overcooling | none | The ticket describes a complete loss of power (no lights, sounds, or compressor activation) with no error codes, and none of the candidates address a machine that won't power on; the cooling, error code, and overcooling runbooks target different symptoms. |
| INC-00319 |  | soft-serve-error-codes, soft-serve-motor-alarm | soft-serve-not-cooling, soft-serve-motor-alarm, soft-serve-overcooling | none | The ticket describes a complete power failure (no lights, sound, or display, unaffected by outlet or breaker reset), whereas none of the candidates address a machine that won't power on; the cooling, motor-alarm, and overcooling runbooks all assume a powered machine showing a specific fault. |

## 2.0 dual-query routing vs locked baseline

2.0 runs `route_doc` on the raw subject + description **and** on classify's `clean_symptom`, merges the candidates (dedupe, best score, max 5) and gives them all to the same `pick_doc`. Symptoms come from the 2.0 agent-eval cache (prompt hash `9376004831248d49, e604a9e5ec6531c9`): 175 of 175 tickets have one (the rest route on the raw text only, same as the baseline).

| Scope | Pipeline | n | Right | Escalated (none) | Wrong |
|---|---|---:|---:|---:|---:|
| overall | locked (raw only, top 3) | 175 | 133 (76.0%) | 32 (18.3%) | 10 (5.7%) |
| overall | **2.0 dual query (top 5)** | 175 | 135 (77.1%) | 28 (16.0%) | 12 (6.9%) |
| pilot | locked (raw only, top 3) | 52 | 52 (100.0%) | 0 (0.0%) | 0 (0.0%) |
| pilot | **2.0 dual query (top 5)** | 52 | 52 (100.0%) | 0 (0.0%) | 0 (0.0%) |

Which query found the chosen runbook (right picks): raw only 4 · symptom only 3 · both 128.

### Outcome changed vs locked (9: 5 better, 4 worse)

| Ticket | Pilot | Expected | Locked pick → outcome | Dual pick → outcome | Found by | Symptom |
|---|---|---|---|---|---|---|
| INC-00020 |  | online-orders-not-on-kds, online-orders-sync-failure, online-orders-aggregator-disconnect | none → escalated | online-orders-not-on-kds → right | both | Online orders from delivery partners stay pending and are not auto-routed to the kitchen display, requiring manual acceptance |
| INC-00033 |  | online-orders-sync-failure, online-orders-aggregator-disconnect, network-router-offline, network-slow | none → escalated | online-orders-sync-failure → right | both | Online app orders are generating duplicate order confirmations at the store |
| INC-00152 |  | online-orders-sync-failure, online-orders-aggregator-disconnect, network-router-offline, network-slow | none → escalated | online-orders-sync-failure → right | both | Web online ordering portal shows a blank white screen and cannot accept orders, while mobile app orders and in-store POS work |
| INC-00157 |  | online-orders-sync-failure, online-orders-aggregator-disconnect, network-router-offline, network-devices-not-connecting | none → escalated | online-orders-sync-failure → right | both | Online ordering portal returns an error and customers cannot place orders |
| INC-00222 |  | online-orders-sync-failure, pos-printer-integration, pos-payment-error | pos-printer-integration → right | none → escalated | — | Kitchen receipt printer prints duplicate orders, and customers are being charged twice for online orders |
| INC-00236 |  | online-orders-sync-failure, pos-printer-integration, online-orders-not-on-kds | none → escalated | online-orders-sync-failure → right | raw | Single online orders are duplicated in the POS, KDS and receipt printer, causing duplicate receipts and duplicate kitchen tickets. |
| INC-00250 |  | network-devices-not-connecting, network-router-offline | network-devices-not-connecting → right | none → escalated | — | Kitchen POS terminal cannot authenticate to the NetLink router Wi-Fi after a power outage |
| INC-00261 |  | network-router-offline, network-slow, online-orders-aggregator-disconnect | none → escalated | online-orders-sync-failure → wrong | both | Online ordering portal is unreachable for customers at the store, with connection timeouts on both app and web, while in-store POS remains operational |
| INC-00269 |  | online-orders-sync-failure, online-orders-aggregator-disconnect, network-slow, network-router-offline | network-slow → right | pos-payment-error → wrong | both | Online payment gateway times out during online order checkout, so customers cannot complete orders |

- Picker calls not served from cache: 102 (229,692 input tokens).

## Routing variants (no LLM) — all tickets in scope

| Variant | n | doc@1 | doc@3 | doc@5 | MRR | SOP hit@5 | FAQ hit@5 |
|---|---:|---:|---:|---:|---:|---:|---:|
| **locked: dense_b005 top-3 runbooks** | 175 | 82.9% | 96.6% | 96.6% | 0.894 | 0/79 | 0/141 |
| dense | 175 | 81.7% | 96.6% | 96.6% | 0.887 | 2/79 | 75/141 |
| fts | 175 | 61.1% | 90.3% | 96.0% | 0.756 | 3/79 | 33/141 |
| hybrid | 175 | 80.0% | 96.0% | 97.7% | 0.884 | 3/79 | 52/141 |
| hybrid+category | 175 | 81.1% | 93.1% | 94.3% | 0.873 | 0/79 | 72/141 |
| hybrid_b005 | 175 | 80.0% | 96.0% | 97.7% | 0.884 | 3/79 | 52/141 |
| dense_b005 | 175 | 81.7% | 96.6% | 96.6% | 0.887 | 2/79 | 75/141 |
| hybrid_b005_softcat | 175 | 80.0% | 96.0% | 97.7% | 0.884 | 3/79 | 54/141 |
| hybrid_b0 | 175 | 82.3% | 96.0% | 97.7% | 0.894 | 3/79 | 50/141 |
| dense_b0 | 175 | 76.6% | 96.0% | 97.1% | 0.855 | 2/79 | 77/141 |

## Routing variants — pilot scope (n = 52)

| Variant | n | doc@1 | doc@3 | doc@5 | MRR | SOP hit@5 | FAQ hit@5 |
|---|---:|---:|---:|---:|---:|---:|---:|
| **locked: dense_b005 top-3 runbooks** | 52 | 98.1% | 100.0% | 100.0% | 0.990 | 0/23 | 0/36 |
| dense | 52 | 94.2% | 100.0% | 100.0% | 0.971 | 2/23 | 15/36 |
| fts | 52 | 73.1% | 96.2% | 100.0% | 0.849 | 3/23 | 4/36 |
| hybrid | 52 | 94.2% | 100.0% | 100.0% | 0.971 | 3/23 | 11/36 |
| hybrid+category | 52 | 100.0% | 100.0% | 100.0% | 1.000 | 0/23 | 11/36 |
| hybrid_b005 | 52 | 94.2% | 100.0% | 100.0% | 0.971 | 3/23 | 11/36 |
| dense_b005 | 52 | 94.2% | 100.0% | 100.0% | 0.971 | 2/23 | 15/36 |
| hybrid_b005_softcat | 52 | 94.2% | 100.0% | 100.0% | 0.971 | 3/23 | 11/36 |
| hybrid_b0 | 52 | 94.2% | 100.0% | 100.0% | 0.968 | 3/23 | 11/36 |
| dense_b0 | 52 | 92.3% | 100.0% | 100.0% | 0.962 | 2/23 | 15/36 |

Variants other than the locked one rank all doc types (runbooks, FAQs, specs, SOPs); the locked one ranks runbooks only and returns 3, so its doc@5 equals doc@3.

## Routing misses: locked: dense_b005 top-3 runbooks (primary runbook not at rank 1)

| Ticket | Expected runbook(s) | Rank | Top-3 routed | Margin |
|---|---|---:|---|---:|
| INC-00037 | soft-serve-error-codes | — | soft-serve-motor-alarm | 0.0811 |
| INC-00136 | soft-serve-error-codes | — | soft-serve-motor-alarm, soft-serve-not-cooling | 0.0636 |
| INC-00132 | soft-serve-error-codes | 2 | soft-serve-motor-alarm, soft-serve-error-codes, soft-serve-overcooling | 0.0625 |
| INC-00110 | soft-serve-error-codes | 2 | soft-serve-motor-alarm, soft-serve-error-codes, soft-serve-overcooling | 0.0523 |
| INC-00222 | online-orders-sync-failure, pos-printer-integration, pos-payment-error | 2 | printer-wrong-output, pos-printer-integration, pos-payment-error | 0.0455 |
| INC-00275 | network-devices-not-connecting, network-slow | 2 | network-guest-wifi, network-devices-not-connecting, network-slow | 0.0453 |
| INC-00150 | soft-serve-error-codes | — | soft-serve-motor-alarm, soft-serve-cleaning-stuck, soft-serve-not-cooling | 0.0427 |
| INC-00261 | network-router-offline, network-slow, online-orders-aggregator-disconnect | 2 | online-orders-sync-failure, online-orders-aggregator-disconnect, network-slow | 0.0365 |
| INC-00169 | soft-serve-overcooling | 2 | soft-serve-not-cooling, soft-serve-overcooling, soft-serve-motor-alarm | 0.0358 |
| INC-00247 | soft-serve-overcooling | 2 | soft-serve-not-cooling, soft-serve-overcooling | 0.0310 |
| INC-00319 | soft-serve-error-codes, soft-serve-motor-alarm | 2 | soft-serve-not-cooling, soft-serve-motor-alarm, soft-serve-overcooling | 0.0303 |
| INC-00290 | network-devices-not-connecting, network-router-offline, network-slow | — | pos-payment-error, pos-slow-performance, pos-startup-failure | 0.0297 |
| INC-00291 | soft-serve-overcooling | 2 | soft-serve-not-cooling, soft-serve-overcooling | 0.0297 |
| INC-00026 | soft-serve-error-codes | — | soft-serve-not-cooling, soft-serve-overcooling, soft-serve-motor-alarm | 0.0265 |
| INC-00289 | soft-serve-error-codes, soft-serve-motor-alarm | 2 | soft-serve-not-cooling, soft-serve-error-codes, soft-serve-motor-alarm | 0.0245 |
| INC-00179 | network-router-offline, network-devices-not-connecting, network-slow | 2 | network-guest-wifi, network-slow, network-router-offline | 0.0203 |
| INC-00250 | network-devices-not-connecting, network-router-offline | 2 | network-guest-wifi, network-devices-not-connecting, network-router-offline | 0.0203 |
| INC-00017 | soft-serve-overcooling | 2 | soft-serve-not-cooling, soft-serve-overcooling, soft-serve-motor-alarm | 0.0195 |
| INC-00115 | soft-serve-error-codes | 3 | soft-serve-not-cooling, soft-serve-motor-alarm, soft-serve-error-codes | 0.0195 |
| INC-00015 | pos-printer-integration, printer-offline, printer-driver-reinstall | 2 | printer-wrong-output, pos-printer-integration | 0.0147 |
| INC-00021 | soft-serve-overcooling | 2 | soft-serve-not-cooling, soft-serve-overcooling, soft-serve-motor-alarm | 0.0103 |
| INC-00044 | network-devices-not-connecting, network-slow | 2 | network-guest-wifi, network-slow, online-orders-aggregator-disconnect | 0.0103 |
| INC-00063 | network-devices-not-connecting, network-slow, pos-slow-performance | 2 | pos-payment-error, pos-slow-performance, pos-startup-failure | 0.0097 |
| INC-00107 | network-devices-not-connecting, network-router-offline, network-slow | — | pos-payment-error, pos-slow-performance, pos-startup-failure | 0.0097 |
| INC-00269 | online-orders-sync-failure, online-orders-aggregator-disconnect, network-slow, network-router-offline | 2 | pos-payment-error, online-orders-sync-failure, network-slow | 0.0097 |
| INC-00099 | network-devices-not-connecting | 2 | network-guest-wifi, network-devices-not-connecting, network-slow | 0.0055 |
| INC-00204 | network-devices-not-connecting, network-slow, pos-slow-performance | 2 | pos-payment-error, pos-slow-performance, network-slow | 0.0045 |
| INC-00304 | soft-serve-error-codes | 2 | soft-serve-not-cooling, soft-serve-error-codes, soft-serve-overcooling | 0.0007 |
| INC-00018 | network-devices-not-connecting, network-slow, pos-software-update | 3 | pos-slow-performance, pos-payment-error, network-slow | 0.0003 |
| INC-00121 | network-router-offline, network-devices-not-connecting, network-slow | 3 | pos-slow-performance, pos-payment-error, network-slow | 0.0003 |
