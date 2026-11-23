# RAG golden-set report

Generated 2026-11-21 17:38 UTC · 10 questions · top 5 chunks · audience `store` · answer + judge model `claude-haiku-5-5`

## Totals

| Metric | Result |
|---|---|
| Retrieval hit@5 (not_in_kb: says not covered) | 8 / 10 |
| Section hit | 7 / 9 |
| must_include facts present (all facts) | 25 / 35 (71%) |
| must_include share (mean per question) | 72% |
| must_not clean | 10 / 10 |

## Per question

| ID | Type | Retrieval | Section | Facts | must_not | Top docs |
|---|---|---|---|---|---|---|
| G01 | answer | ✅ | ✅ | 4/4 | ✅ | `runbooks/pos-startup-failure.md`, `runbooks/pos-software-update.md`, `faq/kiosks-faq.md`, `runbooks/kiosk-software-crash.md` |
| G02 | answer | ✅ | ✅ | 3/3 | ✅ | `runbooks/pos-pin-pad.md` |
| G03 | answer | ✅ | ✅ | 2/3 | ✅ | `runbooks/printer-paper-jam.md`, `runbooks/printer-wrong-output.md` |
| G04 | answer | ❌ | ❌ | 2/5 | ✅ | `runbooks/printer-paper-jam.md`, `runbooks/pos-printer-integration.md`, `faq/printers-faq.md`, `faq/pos-faq.md` |
| G05 | answer | ✅ | ✅ | 3/4 | ✅ | `runbooks/printer-paper-jam.md`, `faq/printers-faq.md` |
| G06 | answer | ✅ | ✅ | 4/4 | ✅ | `runbooks/network-router-offline.md`, `runbooks/online-orders-sync-failure.md` |
| G07 | answer | ❌ | ❌ | 0/3 | ✅ | `faq/online-orders-faq.md`, `runbooks/network-slow.md` |
| G08 | answer | ✅ | ✅ | 3/4 | ✅ | `runbooks/soft-serve-error-codes.md`, `faq/soft-serve-faq.md`, `runbooks/soft-serve-overcooling.md` |
| G09 | l2_only | ✅ | ✅ | 2/2 | ✅ | `runbooks/pos-slow-performance.md`, `system-specs/foodtech-pos.md`, `faq/pos-faq.md`, `runbooks/pos-startup-failure.md`, `runbooks/pos-end-of-day.md` |
| G10 | not_in_kb | ✅ | — | 2/3 | ✅ | `runbooks/online-orders-menu-mismatch.md`, `runbooks/pos-pin-pad.md`, `runbooks/online-orders-sync-failure.md`, `runbooks/kiosk-payment-reader.md` |

## Details

### G01 · answer

**Q:** Our main till went dead after a power cut and now it won't boot up. What should we try first?

- ✅ check power cable and network connection/LED
- ✅ hold the power button about 10 seconds until it shuts down
- ✅ wait about 30 seconds before powering on
- ✅ allow about 2 minutes for startup without interrupting

**Answer:**

> Start with the basic checks: confirm the power cable is firmly connected to the terminal and the outlet, and that the Ethernet or WiFi indicator is lit (green or blue) [1].
> 
> If it still won't boot, do a safe power cycle. Hold the Power Button for 10 seconds until the terminal shuts down, wait 30 seconds, then press it once and allow 2 full minutes without interrupting [1].
> 
> Because the problem followed a power loss, the next step is the extended reset. Hold the power button for 10 seconds, unplug the power cable and any Ethernet cable, and wait 60 seconds [2].
> 
> Reconnect the cables, press the power button, and allow 3 minutes for startup. The terminal should reach the login screen, accept input, and show the correct time and date [2].

### G02 · answer

**Q:** Customers can't pay by card — the PIN pad at counter 1 isn't responding to anything.

- ✅ unplug the PIN pad cable for about 30 seconds and reconnect
- ✅ check cables are fully seated and undamaged
- ✅ restart the FoodTech POS application only, not Windows

**Answer:**

> The PIN pad is unresponsive, so start with these steps from the runbook:
> 
> 1. **Power check:** Confirm the PIN pad has a solid power light (green on an Ingenico Move 5000, blue on a Verifone Vx820). If there's no light, check that the power adapter is plugged into a working outlet and the barrel connector is secure at the PIN pad. [2]
> 2. **Power cycle:** Unplug the USB (or serial) cable from the PIN pad for 30 seconds, then reconnect. Don't restart the whole POS yet. [3]
> 3. **Check connections:** Make sure the cable is fully seated at both the PIN pad and the POS computer, with no visible damage or kinks. [3]
> 4. **Restart the app:** Close FoodTech POS (without restarting Windows), wait 15 seconds, and reopen it to see if the PIN pad reconnects. [3]
> 5. **Test:** Go to **Settings → Payment Devices → PIN Pad Configuration**, click **Test Connection**, and confirm it shows "Connection Successful." [4]
> 
> If the PIN pad still has no power after step 1, the runbook says to escalate because hardware failure is suspected, so contact IT. [2] If the errors point to a driver problem (error E003, a yellow mark in Device Manager, or "Device Not Recognized"), IT can help with a driver reinstall. [5]

### G03 · answer

**Q:** The receipt printer is feeding paper but every receipt comes out completely blank.

- ✅ check thermal paper is loaded shiny side down toward the print head
- ✅ use the FEED button test (hold about 2 seconds) to print a test pattern
- ❌ a blank test print means hardware or driver failure

**Answer:**

> This matches the blank/garbled output case, so try these steps in order:
> 
> 1. **Power cycle the printer:** Turn off the Epson TM-T88VI with the rear power switch, wait 30 seconds, then power on and wait until the LED is solid green [5].
> 2. **Check the paper:** Make sure the thermal paper is loaded shiny side down, toward the print head, and that it isn't jammed or stuck in the feed mechanism [5]. The paper width should match the printer specification [4].
> 3. **Run a manual test print:** Press and hold the FEED button on the top panel for 2 seconds to trigger the factory test pattern. If the test pattern prints clearly, the hardware is working [5].
> 
> The excerpts don't say what to do if the test pattern is also blank, so raise that with L2 support if the problem continues.

### G04 · answer

**Q:** Receipts are printing but the first lines are shifted to the right and cut off. We already restarted the printer.

- ❌ open StarMC Print v2 printer configuration settings
- ✅ check receipt width setting
- ❌ check margins (left 0mm, top 5mm) or restore factory settings
- ❌ save, reopen StarMC and run a test print
- ✅ escalate to L2 if the format is still wrong

**Answer:**

> The closest match is a paper alignment problem. Misaligned paper can cause lines to be cut off or incomplete [1]. Since you've already restarted the printer, try these steps:
> 
> 1. Open the printer cover and check that the paper roll is seated correctly and the paper edge is aligned with the center guide. Reposition the roll so the paper width is centered, reload it, and close the cover [1].
> 2. Confirm the paper is standard receipt stock and is not curled, wet, or expired, and that the printhead is clean [2].
> 3. In Hardware Configuration, open the printer's Properties and check that Print Density is 100% and Paper Width matches the printer specs. Save any changes and test print [2].
> 4. If the text is still shifted or cut off, escalate to L2 [2].
> 
> The excerpts describe misalignment mainly for the last line of a receipt, not the first lines, so the knowledge base doesn't directly confirm this fix for your symptom [1].

### G05 · answer

**Q:** Paper is stuck inside the receipt printer and won't come out. How do we clear it safely?

- ✅ power the printer off and wait about 10 seconds
- ✅ open the cover and look for torn fragments or debris
- ✅ do not insert fingers beyond the visible area / do not force it
- ❌ check the paper roll type and condition

**Answer:**

> Power the printer off and wait 10 seconds before touching it [3]. Open the top cover and look into the paper path, without inserting fingers beyond the visible area [3].
> 
> 1. Lift the cover fully and press the paper roll release lever, then slide the roll out gently without forcing it [1].
> 2. If paper is partly visible at the slot, pull it slowly straight out in the direction of paper flow to avoid tearing [1]. If it is deeply lodged, guide it free with a plastic spoon or soft plastic card rather than pulling hard [1].
> 3. Remove torn fragments from the print head area with a soft, dry cloth [2]. Do not use metal tools or scissors near the print head [1].
> 4. If the paper is wrapped around internal components or won't come out easily, stop and contact IT support, since forcing it can cause damage [2].

### G06 · answer

**Q:** Nothing in the store can get online — POS, kitchen screen, everything. The router lights look off.

- ✅ check ethernet cables to the router WAN port and modem
- ✅ check the modem has power and power-cycle it if unresponsive
- ✅ check whether other stores are affected (ISP outage)
- ✅ note the outage time and alert the shift manager

**Answer:**

> This matches the "Network Router Completely Offline" runbook, since all devices are down and the router LEDs are off [1]. Store staff should try these checks before contacting IT:
> 
> 1. Check that all ethernet cables are firmly connected to the router's WAN (Internet) port and to the modem, and reseat any loose cables [5].
> 2. Check that the modem, if separate, has power and normal status lights. If it is unresponsive, have the store manager power-cycle it [5]. If the modem shows no lights, unplug it for 30 seconds, plug it back in, and wait 2 minutes for it to boot [4].
> 3. Ask whether neighboring locations are also down, which can point to an ISP-wide outage [5].
> 4. Note the outage start time and alert the shift manager about the revenue impact [5].
> 
> The router power-cycle sequence applies only when the router is powered and responsive but has no ISP connection [2], so it doesn't fit dark LEDs. If the router still doesn't come back, contact IT with the exact LED status [2].

### G07 · answer

**Q:** It's lunch rush and the problem is slowing service. Does that change how urgent the ticket is?

- ❌ peak service hours are 11:00–14:00 and 18:00–21:00
- ❌ issues during peak hours raise priority by one level
- ❌ maximum priority is P1

**Answer:**

> Yes, a live operational impact like this makes the issue urgent. The FAQ says urgent issues impacting current operations should be phoned in to the helpdesk first, with a ticket created as backup documentation [1]. The examples it lists are sync failures, widespread order loss, and integration disconnections [1], so it doesn't explicitly name slow network issues. Still, slowness that is delaying service during lunch rush fits the "impacting current operations" category.
> 
> If the slowness continues after the L1 steps, the runbook requires escalation to L2, and that is done by L2, so raise it with them [2]. Include the business impact on POS and ordering in the escalation [2], and note the exact time slowness began [4]. Store staff should not adjust QoS settings at L1, so don't change them during the rush [3][5].

### G08 · answer

**Q:** Soft serve machine shows E04 and won't dispense. What does that mean and what do we do?

- ❌ E04 means hopper low or empty
- ✅ refill the hopper with mix to the full line
- ✅ if it persists, power off, remove the hopper and gently clean the sensor arm
- ✅ escalate if E04 recurs after refill and sensor cleaning

**Answer:**

> The excerpts don't define E04 directly, but the L1 procedure for E04 is a hopper refill and sensor check. Start by checking the hopper: if it's empty or low, refill it to the full line and close it securely [2]. If the error persists, power off, remove the hopper, gently clean the sensor arm with a dry, lint-free cloth without bending it, reinstall the hopper, and power on [2]. If E04 still recurs, escalate with error code E04 and note that the hopper was refilled and the sensor cleaned [2].
> 
> If the machine is a CreamTech SC-300 with firmware below 3.1.2 and the ambient temperature is above 35°C, E04 may be a false alarm. In that case, turn off FREEZE MODE (MENU > FREEZE MODE > OFF, then CONFIRM), wait 60 seconds, and check whether E04 clears [4]. Also, if the display shows a temperature alarm or product above 45°F (7°C), stop serving immediately and contact IT support, because the machine must be taken offline [3].

### G09 · l2_only

**Q:** Can you give me the steps to upgrade our FoodTech POS terminal to the latest version myself?

- ✅ upgrades are performed by L2 only
- ✅ raise/escalate to L2 rather than attempting it at the store
- 🔒 L2-only stub passed: FoodTech POS — System Specification Sheet > Version Upgrade Path > Upgrade Procedure (L2 Only)

**Answer:**

> This question is about upgrading, which is an L2-only procedure. The knowledge base says the upgrade procedure must be performed by L2, not store staff [2], so I can't give you upgrade steps.
> 
> Please raise the upgrade request with L2 support. Before you do, record your current FoodTech version from Settings → About → System Information (or Settings → System Information → Version) so L2 knows your starting point [1][3]. The current supported release is v5.1.1, and moving from v4.x to v5.x is a major upgrade requiring full database migration [2].

### G10 · not_in_kb

**Q:** Our KitchenBridge v2.3 display isn't syncing with the POS. How do we reset KitchenBridge?

- ✅ KitchenBridge is not covered in the knowledge base
- ✅ no invented KitchenBridge steps
- ❌ escalate to L2 with system/vendor/version details

**Answer:**

> The knowledge base doesn't cover this. It has no runbook or excerpt on KitchenBridge displays or their reset procedure; the excerpts cover online-order menu sync, POS PIN pads, online-order sync failures, and kiosk payment readers.

