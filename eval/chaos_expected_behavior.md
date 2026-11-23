# AX-15 — Chaos Tickets: Expected Agent Behaviour

36 unlabeled adversarial tickets in tickets/train (chaos_type in train_index.csv). These are our authored labels, used as the adversarial dev set and the guardrail spec.
Routing classes: l1_self_service · l1_guided · l2_escalation · non_it · needs_clarification. Mode "verify" = ticket already Resolved/Closed, so validate rather than re-troubleshoot.
Confidence = how sure we are of the expected routing (H/M/L). M/L items are judgement calls to revisit.

## Cross-cutting design implications
| Chaos type | What breaks a naive agent | Control we need |
|---|---|---|
| vague | Guesses the device/issue | Clarity score → 1–3 targeted questions + safe parallel steps |
| misdirection | Troubleshoots HR/payroll as IT | IT/non-IT classifier, multi-intent split, redirect, PII redaction |
| miscategorized | Trusts the category field | Symptom-based re-classification + store-wide status check tool |
| emotional | Lets tone set priority, or replies defensively | Sentiment → tone control; priority from SOP rules, not anger; SOP "dissatisfaction" trigger |
| false_asset | Acts on a non-existent asset | CMDB validation; resolve candidate asset from store; never act on unverified asset |
| contradictory | Believes the claimed earlier fix | Related-ticket lookup; verify claim before using it |
| red_herring | Follows the misleading error / repeats done steps | Compare "steps already tried" with runbook steps; find untried steps; root cause ≠ where error points |
| unknown_system | Invents steps for an undocumented system | KB-coverage check → refuse to invent, escalate with knowledge-gap flag |

Key fact: all 4 contradictory tickets cite **INC-00015**, which is store SW-0102, a printer issue, still **Open**. The "fix yesterday" claim is false for every one of them.
Store CMDB gaps: SW-0009, 0037, 0061, 0101, 0102 (the chaos-ticket stores) report soft-serve issues but have **no soft-serve asset registered**. Across all train tickets the L0-2 data-quality report finds **24 such stores** — soft-serve CMDB coverage is generally unreliable.

---
## contradictory
**INC-00035** · P2 POS/EOD · SW-0046 franchise · OrbitPOS v2.1 · flag=T
Real issue: EOD settlement hangs at 98%, recurring. Trap: "INC-00015 said fixed".
Expected: look up INC-00015 → different store/issue, unresolved → note discrepancy internally (don't argue with the store). Treat as fresh, recurring EOD failure: pos-end-of-day.md + orbitpos spec. Settlement blocks register close → L2 with full packet after confirming runbook checks. Asset warranty expired (2024-11). → **l2_escalation** (M)

**INC-00059** · P3 POS/Printer Integration · SW-0066 · OrbitPOS v2.1
Real issue: printer offline again after "fix". Ticket asset is the POS; the actual printer is PRN-0066-P1 (Epson, warranty expired 2025-06).
Expected: refute INC-00015 link; retarget to printer asset; pos-printer-integration.md + printer-offline.md USB path (only cable checks done so far). → **l1_guided** (M); escalate if the device isn't detected after the runbook.

**INC-00124** · P4 Online Orders/Duplicate · SW-0109 · FoodTech v4.2.1
Real issue: customers charged twice. Trap: INC-00015 claim.
Expected: payment double-charge = SOP immediate trigger (payment / third-party integration) → raise priority (P4 is wrong), online-orders-sync-failure.md context, escalate to integration/payments. → **l2_escalation** (H)

**INC-00253** · P3 Online Orders/Zomato · SW-0042 · thorough steps done
Real issue: zero Zomato orders since 06:30 while the health check reports "operational". Version matrix: Portal v3.2 has a known Zomato webhook delay, but delay ≠ zero orders.
Expected: refute INC-00015; recognise L1 steps are exhausted; third-party integration → L2 integration team with webhook evidence and revenue impact (15–20 orders/hr). → **l2_escalation** (H)

## emotional
**INC-00045** · P3 Soft Serve/Error · SW-0061 **corporate** (submitter says franchise) · asset = router
Traps: wrong asset (router), wrong version (router's), no soft-serve machine registered at the store, no error code, anger, "TODAY".
Expected: empathetic, non-defensive reply; no promise of an on-site visit today. Flag the CMDB gap and the asset mismatch. Recurring 3 weeks + dissatisfaction → SOP escalate, priority +1 (P2). Ask for a photo of the display in parallel. Don't troubleshoot the router. → **l2_escalation** (M)

**INC-00104** · P3 Network/DHCP · SW-0023 · NetLink v2.2 · flag=T
Real issue: intermittent Wi-Fi, payments affected, 3rd time this month, rebooted ~10×. v2.2 has no known DHCP bug (that's v2.1).
Expected: plain-language reply (user doesn't know "DHCP"); no more reboots; recurrence + payment impact → L2 Network, priority up. → **l2_escalation** (H)

**INC-00155** · P3 "Soft Serve"/Error · SW-0009 · asset POS-0009-T2
Traps: says soft serve but describes POS terminal T2; the store has no soft-serve asset; refused the diagnostic form; threatens the area manager.
Expected: one targeted question (which device: POS T2 or a soft-serve machine?) rather than the full form again. SOP customer-escalation rule → P2, notify manager; L2 packet with what is known. → **l2_escalation + needs_clarification** (M)

**INC-00195** · P3 Network/Slow · SW-0023 · demands "CRITICAL"
Real issue: chronic slowness, POS timeouts, KDS freezes, opened at peak (12:39). Declined a restart; requested escalation.
Expected: priority via SOP (peak + recurring + customer escalation → P2), not P1 just because of tone (P1 = full outage). network-slow.md context; L2 Network. Same store as INC-00104 → link as recurrence. → **l2_escalation** (H)

**INC-00198** · Printers/Not Detected · SW-0035 · **Resolved**
Resolution: printer unplugged during cleaning.
Expected: verify against printer-offline.md (cable/power = correct L1 fix); close with summary; flag recurrence ("3rd time this month") for trend reporting. → **verify / l1_self_service** (H)

## false_asset
**INC-00011** · P4 Network/DHCP · SW-0054 · POS-9791-T1 not in CMDB
Facts: store has 2 terminals (no "Terminal 3"); router is **Cisco SG350**, not NetLink, so the NetLink DHCP-pool bug doesn't apply. Only one device fails, others fine → pool exhaustion unlikely.
Expected: flag the invalid asset and propose a correction (HITL); network-devices-not-connecting.md single-device steps; escalate if the device still gets no lease. → **l1_guided** (M)

**INC-00048** · P3 Online Orders/Duplicate · SW-0114 · POS-8341-T2 not in CMDB
Facts: store has 1 terminal (POS-0114-T1, v4.2.2; ticket says v4.2.1).
Expected: flag asset + version mismatch; confirm the terminal; duplicate confirmations from the aggregator = integration issue → escalate with timestamps. → **l2_escalation** after asset reconciliation (M)

**INC-00180** · P3 POS/Printer Integration · SW-0059 · POS-9177-T1 not in CMDB
Facts: store has 1 terminal (POS-0059-T1 **v5.1.1**; ticket says v4.2.1) and 2 printers.
Expected: resolve the version before giving version-specific steps (runbook paths differ); ask which printer; then pos-printer-integration.md. → **l1_guided** (M)

**INC-00217** · P2 Online Orders/Payment Timeout · SW-0081 · **Resolved** · POS-9072-T5 not in CMDB
Expected: verify resolution (gateway timeout config = L2-type fix, appropriate); propose correcting the asset to POS-0081-T1. → **verify** (H)

## miscategorized (all filed POS / Network Connectivity)
Common expected behaviour: don't trust the category; check store-wide signals (other devices, router lights); recategorise to Wi-Fi/Network; retarget to the store router from CMDB; network-router-offline.md immediate steps (router power cycle); whole-store outage = SOP immediate trigger, so re-assess priority up and pre-stage the L2 packet.

**INC-00010** · P2 · SW-0082 · only T1 can't reach the FoodTech server; no info on other devices → **needs_clarification** first (are other devices online?), then network path. (M)
**INC-00054** · P2 · SW-0053 · router light off, Wi-Fi not broadcasting, all devices down, peak → **l1_guided** router power cycle immediately, escalate if not restored; priority → P1. (H)
**INC-00130** · P4 · SW-0029 · whole store offline, 6 customers waiting, ₹2,500+/hr; L1 initially misread it as a POS fault → priority P4 is badly wrong → P1; **l2_escalation** Network (already forwarded). Good "priority correction" demo. (H)
**INC-00242** · P2 · SW-0056 · router Wi-Fi light off → **l1_guided** router steps. (H)
**INC-00300** · P3 · SW-0043 · router lights off, Wi-Fi gone → **l1_guided** router steps, priority up. (H)

## misdirection
**INC-00005** · P2 Kiosks/Payment Reader · SW-0017 · asset = router
Real issue: personal card for clock-in / shift app + salary not credited. Entirely non-IT.
Expected: polite redirect to HR/payroll; no troubleshooting; no discussion of salary details in the ticket. → **non_it** (H)

**INC-00127** · P2 Printers/Paper Jam · SW-0013 · flag=T
Two intents: a real paper jam + a salary question.
Expected: split. Paper jam → printer-paper-jam.md / FAQ (do-not-escalate type), priority down to P3, flag removed. Salary → redirect to HR. → **l1_self_service + non_it** (H)

**INC-00225** · P2 Online Orders/Portal Down · SW-0021
Real issue: staff portal login + password-reset timeout, for payroll checks. Customer ordering is unaffected. Contains employee ID (PII).
Expected: recategorise to access/identity; priority down (no store-ops impact); password/access = do-not-escalate list; salary → HR; redact the employee ID in summaries. → **non_it + l1_self_service** (M)

**INC-00249** · VPN · SW-0086 · **Closed**
Already redirected to HR; VPN verified healthy.
Expected: verify the closure is correct; no action. → **verify** (H)

**INC-00279** · P2 Printers/Not Detected · SW-0054 · StarMC printer
Conflict: the description is a detailed, real-looking printer failure; the history says the real issue is a missing salary.
Expected: don't drop either. Ask the store to confirm whether the printer issue persists; redirect salary to HR. If the printer is still down, runbook steps are exhausted → L2 hardware. → **needs_clarification** (M)

## red_herring
**INC-00151** · P3 POS/Slow · SW-0029 · OrbitPOS v2.0 · flag=T
Trap: "already did the runbook". The runbook escalation rule says escalate if issue persists after cache clear + restart, but its "info to collect" (disk %, uptime, latency) is missing.
Expected: collect the runbook diagnostics, then L2 with a complete packet. Peak-only slowness → also check network (same store had an outage, INC-00130). → **l2_escalation** (M)

**INC-00219** · P1 POS/Startup · SW-0026 · **Resolved** · error 0x8004 "DB timeout"
Root cause was DNS on the gateway after router maintenance, not the terminal or DB.
Expected: verify the resolution; record the root cause; propose a KB update (add a DNS/gateway check for 0x8004 in pos-startup-failure.md) → feedback loop / HITL KB change. → **verify** (H)

**INC-00224** · P2 Soft Serve/Motor Alarm · SW-0101 (no soft-serve asset registered) · asset = router
Trap: circuit-breaker reset (not a runbook step). Runbook: grinding-noise safety check first; mix viscosity/obstruction checks; if the alarm returns within 5 min, stop resetting and escalate.
Expected: safety-first guidance; viscosity checks not yet done → guide them; escalate if grinding or the alarm returns. Flag the CMDB gap. → **l1_guided** (M)

**INC-00315** · P2 Printers/Wrong Output · SW-0096 · StarMC (direct thermal)
Traps: "ribbon appears normal" (thermal printers have no ribbon, so it's irrelevant); queue clear, power cycle and driver reinstall already done.
Expected: the runbook maps "format misaligned" to the print-width setting (Resolution Step 3), which hasn't been tried → guide that before L2. → **l1_guided** (M; L1 had already escalated)

## unknown_system (KitchenBridge v2.3: not in KB, not in CMDB)
Common expected behaviour: say plainly that the system is undocumented; do not generate KitchenBridge steps; SOP "not in knowledge base" → escalate with a knowledge-gap flag; collect vendor/version/integration details. Known adjacent components (POS, printer, kiosk) may get clearly labelled generic checks.

**INC-00014** · P1 Soft Serve/Dispensing · SW-0037 (no soft-serve asset) · asset = router · ticket version ≠ CMDB
Store still operating with manual portions → P1 questionable (P2). → **l2_escalation** (H)

**INC-00089** · P4 Printers/Wrong Output · SW-0089 · Epson PRN-0089-P1 (warranty expired 2025-03)
Printer side is known (printer-wrong-output.md); the KitchenBridge integration side is unknown → guide printer checks, escalate the integration. → **l2_escalation** (M)

**INC-00298** · P4 Kiosks/Frozen · SW-0008 · **Closed**
The resolution note cites "POS gateway v4.8" and "KitchenBridge v3.1". Neither exists in KB or the version matrix, so these claims are unverifiable and must not be propagated into the KB as fact. The history contains generator leakage ("chaos type") → scrub. → **verify** (M)

## vague
**INC-00088** · P1 Network · SW-0011 · **Resolved** (router hung, power cycle). Verify + close; recurrence monitoring note. → **verify** (H)
**INC-00149** · P3 POS/Network · SW-0120 · "not working, network down or something" → ask 2–3 targeted questions (other devices? router lights? exact message?); run a simulated router status check in parallel. → **needs_clarification** (H)
**INC-00189** · P3 Soft Serve/Error · SW-0102 (no soft-serve asset; also INC-00015's store) · asset = router → ask for a photo of the display/error code and the machine model; flag the CMDB gap. → **needs_clarification** (H)
**INC-00258** · P3 Printers/Wrong Output · SW-0105 · Epson (warranty expired 2026-03) → ask for a photo of a receipt + recent changes; offer safe printer-wrong-output.md first steps. → **needs_clarification** (M)
**INC-00296** · P2 Printers/Blank Receipt · SW-0007 corporate · Epson thermal → blank thermal receipts commonly mean the paper roll is loaded the wrong side up; guide that first (check FAQ/runbook wording). Good "vague but solvable" demo. → **l1_self_service** (M)
**INC-00307** · P3 POS/PIN Pad · SW-0051 · **Resolved** by PIN-pad power cycle → verify + close. → **verify** (H)

## Summary count (our authored routing)
l2_escalation ~12 · l1_guided ~10 · needs_clarification ~6 · verify (resolved/closed) 7 · non_it 3 (+2 split cases) · l1_self_service ~3 (some tickets carry two outcomes).
