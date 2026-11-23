# AX-15 held-out run-to-run variance: 1.0 and 2.0 (frozen), 3 runs each

_43 held-out tickets (split `v1-seed42` minus INC-00162) · frozen 2.0 code (before the safety rules), unchanged · agent-output cache bypassed, every run calls the models · session spend $0.818._

> **Why there is no temperature setting.** Neither model accepts one. Claude Haiku 5.5 (`llm.base`) returns `400 temperature: Extra inputs are not permitted` for `temperature=0` and also for the default `1` (the installed SDK uses API version `2026-10-21`, which rejects sampling parameters). GPT-6 Luna (`llm.fallback`) returns `400 'temperature' does not support 0 with this model. Only the default (1) value is supported.` So these runs measure the variance at the models' default sampling.

> **Partial:** 1.0 run 3 (not run: INC-00313, INC-00316): stopped at the spend cap, so its metrics cover fewer tickets and its mean/min/max contribution is on that smaller set.

## 1.0

| Metric | Run 1 | Run 2 | Run 3 | Mean | Min | Max | Original held-out run |
|---|---:|---:|---:|---:|---:|---:|---:|
| Routing L1/L2 | 76.7% | 74.4% | 80.5% | 77.2% | 74.4% | 80.5% | 65.1% |
| Escalation precision (verify excluded) | 85.7% | 95.7% | 95.7% | 92.3% | 85.7% | 95.7% | 90.9% |
| Escalation recall (verify excluded) | 80.0% | 73.3% | 75.9% | 76.4% | 73.3% | 80.0% | 66.7% |
| Pilot routing L1/L2 | 91.7% | 66.7% | 70.0% | 76.1% | 66.7% | 91.7% | 66.7% |
| Escalation precision (all, as heldout_eval.md) | 85.7% | 95.7% | 95.7% | 92.3% | 85.7% | 95.7% | 90.9% |
| Escalation recall (all, as heldout_eval.md) | 80.0% | 73.3% | 75.9% | 76.4% | 73.3% | 80.0% | 66.7% |
| n | 43 | 43 | 41 | | | | 43 |
| n pilot | 12 | 12 | 10 | | | | 12 |
| Verify answers excluded | 0 | 0 | 0 | | | | 0 |
| Escalations | 28 | 23 | 23 | | | | 22 |
| Errors / incomplete | 0 | 0 | 0 | | | | 0 |
| Cost | $0.184 | $0.176 | $0.164 | | | | $0.177 |

**Routing changed between the 3 runs on 13 of 43 tickets** (30%). Run 1 vs the original held-out run: 12 tickets differ.

| Ticket | Pilot | Label | Run 1 | Run 2 | Run 3 | Original run |
|---|---|---|---|---|---|---|
| INC-00053 |  | l2_escalation | l2_escalation | needs_clarification | l2_escalation | needs_clarification |
| INC-00074 |  | l1_guided | l2_escalation | needs_clarification | l2_escalation | needs_clarification |
| INC-00098 | yes | l2_escalation | l2_escalation | l1_guided | l1_guided | l2_escalation |
| INC-00126 |  | l2_escalation | l1_guided | l2_escalation | l2_escalation | l1_guided |
| INC-00145 | yes | l2_escalation | l2_escalation | l2_escalation | l1_guided | l2_escalation |
| INC-00161 |  | l1_guided | l2_escalation | l1_guided | l1_guided | l1_guided |
| INC-00202 |  | l1_guided | l2_escalation | l1_guided | l1_guided | l2_escalation |
| INC-00205 | yes | l2_escalation | l2_escalation | l1_guided | l2_escalation | l1_guided |
| INC-00212 |  | l2_escalation | l2_escalation | l1_guided | l1_guided | l1_guided |
| INC-00218 |  | l1_guided | l1_guided | needs_clarification | l1_guided | l1_guided |
| INC-00227 | yes | l1_guided | l1_guided | l2_escalation | l1_guided | l2_escalation |
| INC-00239 |  | l1_guided | l2_escalation | l1_guided | l1_guided | l1_guided |
| INC-00271 |  | l2_escalation | l1_guided | l2_escalation | l2_escalation | l2_escalation |

## 2.0

| Metric | Run 1 | Run 2 | Run 3 | Mean | Min | Max | Original held-out run |
|---|---:|---:|---:|---:|---:|---:|---:|
| Routing L1/L2 | 72.1% | 79.1% | 81.4% | 77.5% | 72.1% | 81.4% | 74.4% |
| Escalation precision (verify excluded) | 81.5% | 85.7% | 86.2% | 84.5% | 81.5% | 86.2% | 82.1% |
| Escalation recall (verify excluded) | 73.3% | 80.0% | 83.3% | 78.9% | 73.3% | 83.3% | 76.7% |
| Pilot routing L1/L2 | 66.7% | 66.7% | 83.3% | 72.2% | 66.7% | 83.3% | 66.7% |
| Escalation precision (all, as heldout_eval.md) | 81.5% | 85.7% | 86.2% | 84.5% | 81.5% | 86.2% | 82.1% |
| Escalation recall (all, as heldout_eval.md) | 73.3% | 80.0% | 83.3% | 78.9% | 73.3% | 83.3% | 76.7% |
| n | 43 | 43 | 43 | | | | 43 |
| n pilot | 12 | 12 | 12 | | | | 12 |
| Verify answers excluded | 0 | 0 | 0 | | | | 0 |
| Escalations | 27 | 28 | 29 | | | | 28 |
| Errors / incomplete | 0 | 0 | 0 | | | | 0 |
| Cost | $0.097 | $0.099 | $0.097 | | | | $0.096 |

**Routing changed between the 3 runs on 6 of 43 tickets** (14%). Run 1 vs the original held-out run: 3 tickets differ.

| Ticket | Pilot | Label | Run 1 | Run 2 | Run 3 | Original run |
|---|---|---|---|---|---|---|
| INC-00025 |  | l2_escalation | l1_guided | l2_escalation | l2_escalation | l1_guided |
| INC-00074 |  | l1_guided | l1_guided | l1_guided | l2_escalation | l2_escalation |
| INC-00090 | yes | l2_escalation | l1_guided | l1_guided | l2_escalation | l1_guided |
| INC-00126 |  | l2_escalation | l1_guided | l2_escalation | l2_escalation | l1_guided |
| INC-00202 |  | l1_guided | l2_escalation | l1_guided | l1_guided | l1_guided |
| INC-00227 | yes | l1_guided | l2_escalation | l2_escalation | l1_guided | l2_escalation |

## Reading this

- One ticket = 2.3 points overall and 8.3 points in the pilot subset (n = 12).
- Inputs, code, prompts and configs are identical across runs; differences are the models' choices.
- `Original held-out run` = the single held-out run of `eval/heldout_eval.py` (2026-11-23, same frozen code); shown for reference, not included in mean/min/max.
- Verify-mode answers (Resolved/Closed tickets) are excluded from escalation P/R per the freeze rulings; the held-out split has none, so both P/R rows agree.
