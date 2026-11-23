# AX-15: IT support agent for ServeWell Hospitality

AX-15 is a proof of concept for ServeWell's L1 IT support. ServeWell runs 2,500+ restaurant and retail stores, and
its L1 team is overloaded. AX-15 reads a store's ticket and decides:

- whether L1 can solve it, and if so gives the steps **copied word for word** from ServeWell's runbooks;
- whether it must go to L2, and if so writes the L2 handoff;
- whether it needs clarification, or isn't an IT issue at all.

It also drafts the reply to the store. Nothing happens to a ticket until a person approves it.

Routing and priority follow ServeWell's escalation SOP. The SOP is written as rules in a config file and applied by
code, not by the model. The model reads the ticket, picks a runbook and picks steps by id. Code does the rest.

## How it works

### The 2.0 workflow

![AX-15 2.0 workflow: a ticket goes through the triage agent (load and enrich with tools, classify facts, SOP policy rules), the resolution agent (route to a runbook, resolve step ids), decide, the communication agent (compose), the guard and human review, then the action log; closed, safety, not-IT and unclear tickets skip resolution. Neon Postgres with pgvector and a trust layer sit under every run.](docs/images/ax15_v2_architecture.png)

| Step | Who | What it does |
|---|---|---|
| Load and enrich | code | Reads the ticket (eval-only fields are stripped at load) and calls the tools: store, CMDB asset, the store's devices, recent tickets at the store (30 days), SLA, past approved actions |
| Triage | LLM | Extracts facts only: symptom, device, IT or not IT, impact, SOP trigger facts, safety facts. No decisions |
| Policy | code | Adds facts from code (asset checks, unknown systems or error codes, recurrence, peak hours) and runs the SOP rules |
| Resolution | LLM + KB | Search returns the top 3 runbooks; a picker chooses one or none (none means L2). A second call picks step ids and any "When to Escalate" criteria that apply |
| Decide | code | Runs the SOP rules again with the resolution facts. Sets routing, priority, steps and the handoff fields |
| Communication | LLM | Writes the reply to the store and the L2 handoff summary from structured facts. It never writes steps |
| Guard | code | Checks the answer before anything is proposed (see below) |
| Human review | person | The proposed action waits in the console. Approve or Reject resumes the workflow from a saved checkpoint |

**Knowledge base** (runbooks, FAQs, system specs and the SOP): 48 documents indexed as 722 searchable sections; the
version matrix is loaded as a table.

**Principles**
- Facts come from tools, procedures from the knowledge base, rules from code.
- Select, don't write: the model picks runbook steps by id, and code copies the step text from the KB.
- Eval labels never reach a prompt. They live in a separate database schema that agent code doesn't read.

**SOP rules** (`config/sop_rules.yaml`, readable table in `docs/results/sop_rules_table.md`)
- 30 active routing rules (plus one proposed rule, inactive), 4 base-priority rules and 6 priority overrides. Each
  rule cites its source in the SOP or in AX-15 policy.
- The stages run in this order: verify (closed tickets) → safety → not IT → power → do not escalate → escalate →
  clarify. If no rule fires, the ticket stays at L1.
- **Safety rules.**
  - AX-SAFETY covers fire, smoke, a burning smell, sparks, an electric shock or exposed wiring, including suspected
    ones, on any equipment. It escalates at P1 with no troubleshooting steps and notifies facilities as well as L2 and
    the P1 on-call. The reply always opens with a fixed safety notice, added by code.
  - AX-POWER covers repeated loss of mains power. It escalates to L2 and facilities with no POS troubleshooting.

**Model roles** (`config/models.yaml`; model ids live only there)
- **Claude Haiku 5.5:** triage, picker, resolution and communication in 2.0, and the whole 1.0 agent.
- **GPT-6 Luna:** availability fallback in 2.0 when the Anthropic API fails.
- **OpenAI text-embedding-3-large (1536 dimensions):** knowledge-base embeddings.
- **Claude Haiku 5.5 as judge:** grades the adversarial ("chaos") set. Evaluation only.

### 1.0: the single-agent baseline

![AX-15 1.0: one Claude Haiku 5.5 agent in a loop reads the ticket, calls 7 read-only tools and reads their results until it writes a JSON answer (routing, priority, steps, reply); a person approves or rejects the proposed action, which goes to the action log.](docs/images/ax15_v1_architecture.png)

**1.0** is the baseline: one Claude tool-calling loop. The model can call 7 read-only tools; `get_ticket` and
`log_action` run in code. It reads the ticket, calls tools, searches the KB and writes the whole answer itself,
including the steps. **2.0** splits that into the workflow above:
- the model only extracts facts and selects runbooks and step ids;
- SOP rules in code decide routing and priority;
- a guard checks every answer before a person reviews it.

On the dev set 2.0 routes more tickets correctly, at about half the cost per ticket and a lower median time. On held-out tickets the two tie
on routing, but 2.0 is more repeatable between runs. 1.0's free-form judgement caught the safety hazards in test10
before 2.0 had a rule for them. Results are below.

## Quick start

You need Python 3.11+, a Postgres database with pgvector (we use Neon), an Anthropic key and an OpenAI key.

```bash
python -m venv .venv
.venv\Scripts\activate                 # Windows; use: source .venv/bin/activate elsewhere
pip install -e ".[app,dev]"
copy .env.example .env                 # cp on macOS / Linux; then fill in the keys
```

Keys in `.env`:
- `ANTHROPIC_API_KEY`: the agents and the judge.
- `OPENAI_API_KEY`: embeddings and the GPT-6 Luna fallback.
- `DATABASE_URL`: the pooled connection string for the app.
- `DATABASE_URL_DIRECT`: the direct string for migrations; the pooled one is used if it's empty.
- `LANGFUSE_*`: optional, for hosted traces. Without them, traces are written locally.
- `GROQ_API_KEY`: optional; only used for comparison.
- `DATASET_DIR`: defaults to `data-original`.

Create the tables and load the data once:

```bash
python -m ax15.migrate                    # numbered, idempotent SQL migrations
python -m ax15.l0_data.reference          # stores, CMDB assets, SLA and version tables (+ data quality report)
python -m ax15.l0_data.load_train         # 256 train tickets
python -m ax15.l0_data.load_labels        # labels, into the separate eval schema
python -m ax15.l0_data.eval_assets        # dev / held-out split and chaos labels
python -m ax15.l1_knowledge.ingest        # chunk + embed the knowledge base
```

**Wake the database.** Neon scales to zero, so wake it a minute before a demo:

```bash
python scripts/check_setup.py --free      # PASS / FAIL per service, no paid calls; also wakes Neon
```

Opening the console does the same. Its sidebar pings Neon on load and has a Refresh button.

**Run the console:**

```bash
streamlit run app/console.py
```

The sidebar has the agent choice (2.0, 1.0, or compare both) and a status panel:
- Neon, Anthropic, OpenAI, Luna and Langfuse;
- the agent version and its prompt/config hash.

Run is disabled while Neon or Anthropic is red.

The console has five tabs:
- **New ticket:** type a ticket as a store would.
- **Existing ticket:** dev and chaos tickets.
- **Batch:** run a folder of tickets.
- **Ask the KB:** the retrieval layer on its own.
- **System:** knowledge-base and ticket counts, models and last eval.

## Demo walkthrough

1. **A normal L1 ticket.** Existing ticket → **INC-00031** ("Kitchen receipt printer not responding") → agent 2.0 →
   Run.
   - **What to look at:** the routing and priority with the SOP rules behind them, the runbook section, and the steps.
     The steps are verbatim from the KB, and each one has its id.
   - **Workflow and trace:** the workflow panel shows each node, and the trace shows every tool and model call.
   - **Review:** Approve or Reject the proposed action. That resumes the paused workflow.
2. **1.0 vs 2.0 side by side.** Sidebar → Compare → Existing ticket → **INC-00127**, a chaos ticket. It reports a
   receipt-printer paper jam and, in the same message, asks about a missing salary payment.
   - **Both agents:** handle the jam at L1 with runbook steps and redirect the salary question to HR.
   - **1.0:** raises the priority to P1, because of the ticket's "ASAP".
   - **Judge verdicts:** 2.0 PASS, 1.0 FAIL (the reasons are in `docs/results/agent_eval_v1.md` and
     `agent_eval_v2_final.md`).
3. **A safety ticket.** New ticket → subject "Smoke coming from the fryer in the kitchen", no asset → 2.0 → Run.
   - **Routing:** L2 at P1 via AX-SAFETY, with no troubleshooting steps.
   - **Reply:** opens with the fixed safety notice.
   - **Handoff:** notifies facilities, L2 and on-call.
4. **A panel-style batch.** Batch → folder `test10` → Run → the results table → click INC-90005 (a smoking POS
   terminal) to open its answer and trace. Each row shows routing, priority (ticket → agent), the rules that fired,
   the runbook, flags, cost and latency.

## Running a panel CSV

Put the file in its own folder under `incoming/` and run it:

```bash
ax15 run-batch incoming/<batch>              # agent 2.0 (default); add --agent v1 for 1.0
ax15 clear-batch <batch>                     # remove the batch from the database afterwards
```

What a batch folder can contain:
- `*.csv` with train-like columns. List cells can be JSON strings, and missing optional columns are fine.
- `*.json` with one ticket or an array of tickets.
- `*.txt` with one free-text ticket per file.
- `labels.json`, optional ground truth.

How a batch is handled:
- **Bad rows:** malformed rows are reported and skipped; the rest of the batch still runs.
- **Labels:** label and eval-only columns (for example `correct_routing`, `chaos_type`) are removed before the agent
  sees anything. If labels are present, the batch is scored with the same code as the evaluation.
- **Loading:** tickets are loaded with `source=panel_test` and `batch_id=<folder name>`. Re-running replaces the batch.
- **No writes:** runs use eval mode, so no actions are written.
- **Report:** goes to `outputs/batch_<name>_report.md` and `.csv`, with a summary block, then one row per ticket.

The same works in the console's Batch tab, with file upload.

## Results

All results files are in `docs/results/`. **Pilot** = tickets whose main runbook is POS or receipt printer. Routing
is scored as L1 vs L2, because the train labels only have `l1_guided` and `l2_escalation`.

**Held-out (43 tickets, never used for tuning; 3 fresh runs per agent)**, from `heldout_variance.md`:

| | 1.0 mean (min–max) | 2.0 mean (min–max) |
|---|---:|---:|
| Routing L1/L2 | 77.2% (74.4–80.5) | 77.5% (72.1–81.4) |
| Escalation precision | 92.3% (85.7–95.7) | 84.5% (81.5–86.2) |
| Escalation recall | 76.4% (73.3–80.0) | 78.9% (73.3–83.3) |
| Pilot routing (12 tickets) | 76.1% (66.7–91.7) | 72.2% (66.7–83.3) |
| Tickets whose routing changed between runs | 13 / 43 | 6 / 43 |

- **Routing:** held-out is a tie, and the run ranges overlap.
- **Repeatability:** 2.0 changes its routing between runs on about half as many tickets as 1.0.
- **Escalation:** 1.0 has higher precision; recall is close.
- **Excluded ticket:** INC-00162 is left out because it was used in early smoke tests.
- **Third 1.0 run:** it covers 41 of the 43 tickets.

**Dev (176 tickets, used for tuning 2.0; single run)**, from `agent_compare.md`:

| | 1.0 | 2.0 |
|---|---:|---:|
| Routing L1/L2 | 68.2% | 74.4% |
| Pilot routing (52) | 65.4% | 80.8% |
| Escalation precision / recall | 85.3% / 69.8% | 87.9% / 69.0% |
| Right runbook cited | 67.0% | 77.3% |
| Steps verbatim from the KB | 99.3% | 100% |
| Cost per ticket | $0.0039 | $0.0022 |
| Median time per ticket | 23.3 s | 15.4 s |

**Chaos set (36 adversarial tickets, LLM judge against our written expected behaviour)**, from `agent_compare.md`:
1.0 passes 11 / 36, and 2.0 passes 18 / 36. 2.0 is better on misdirection (4/5 vs 1/5), red herrings (2/4 vs 0/4) and
contradictory tickets (3/4 vs 1/4). Both are weak on miscategorised tickets (1/5 each) and false assets (2/4 and 1/4).

**test10 (10 new tickets written after the freeze)**, from `test10_comparison.md`:
- **Before the safety rule:** 2.0 differed from the expected routing or priority on 3 tickets.
  - **INC-90005 and INC-90006:** a smoking POS and an electric shock from a printer. 2.0 escalated both, but at P2 and
    only because no runbook fit.
  - **INC-90008:** the store's mains power kept dropping. 2.0 sent it to self-service.
  - **1.0:** escalated all three, with P1 for the two hazards.
- **The fix:** AX-SAFETY and AX-POWER.
  - **Afterwards:** INC-90005 and INC-90006 go to P1 via AX-SAFETY, INC-90008 goes to L2 via AX-POWER, and a fryer fire
    goes to P1.
  - **Unchanged:** the other re-run tickets.

**Retrieval** (`retrieval_eval.md`, `rag_golden_report.md`):
- **Runbook routing (dev, 175 tickets):** the picker chose a correct runbook for 76.0%, escalated with no runbook for
  18.3%, and chose a wrong runbook for 5.7%. On the 52 pilot tickets it chose a correct runbook every time.
- **RAG golden set (10 questions):** retrieval hit 8 / 10, 25 of 35 required facts present, and 10 / 10 free of
  forbidden content.

**Data** (`data_quality_report.md`):
- 311 source files were checked against an integrity manifest, and none had changed.
- 120 stores and 477 CMDB assets were loaded.
- 4 tickets name an asset that isn't in the CMDB.

## Evaluation, guardrails, observability, human in the loop

**Evaluation**
- **Splits:** the labelled train tickets are split with seed 42 into 176 dev and 44 held-out (`eval/splits.json`).
  2.0 was tuned on dev only.
- **Chaos set:** 36 adversarial tickets, with expected behaviour written by us (`eval/chaos_expected_behavior.md`).
  An LLM judge grades them.
- **Scripts:** `eval/agent_eval.py` (dev + chaos), `eval/heldout_variance.py` (held-out, 3 runs),
  `eval/retrieval_eval.py`, `python -m ax15.eval.rag_golden`.
- **Tests:** `pytest -m "not llm"` runs the full test suite without paid model calls.

**Guardrails** (`src/ax15/trust/guard.py`, pure code). Before anything is proposed, the guard checks that:
- every step is verbatim KB text;
- no L2-only text reaches the store;
- no steps are shown for unknown systems or for rules that forbid them;
- ids, error codes, numbers and device names in the reply appear in the sources;
- employee ids are redacted;
- the answer matches the schema.

Anything that fails is stripped and flagged. If nothing safe is left, the ticket goes to L2.

Other safeguards:
- **Spend cap:** a per-session spend cap stops runs before they overspend.
- **Fallback:** if a model call fails, the ticket is escalated rather than guessed.

**Observability**
- **Traces:** every run writes a trace with each node, tool call and model call, plus their tokens, cost and latency.
  The trace is a local JSON file, and also goes to Langfuse when keys are set. The console shows it next to the
  answer.
- **Spend ledger:** model spend goes to a ledger with the per-session cap.

**Human in the loop**
- **Proposed actions:** every proposed action (L1 guidance, escalation, CMDB correction) is written to `action_log`
  as `proposed`, with the run's trace id. A person approves or rejects it in the console. CMDB corrections are
  proposed, never applied.
- **Memory:** approved escalations are the only past actions a later run can see; they count toward recurrence.

## Trade-offs

- **Rules in code vs model judgement.** SOP rules make routing auditable and repeatable, and every decision cites a
  rule. The cost is that anything the SOP doesn't cover is missed until a rule exists. test10 showed this with the
  safety tickets, which 1.0's free-form judgement handled.
- **Select, don't write.** 100% of 2.0's steps are verbatim KB text, but fewer answers include steps: 38.1% vs 48.3%
  on dev. When no runbook fits, 2.0 escalates rather than improvising.
- **A small, cheap model.** Claude Haiku 5.5 keeps cost at about $0.002 per ticket for 2.0. Neither it nor the
  fallback model accepts a temperature setting, so answers vary between runs (see Known limits).
- **One runbook per ticket.** Routing picks a single runbook, which keeps answers focused but misses tickets that span
  two procedures.
- **Neon scales to zero.** Hosting is cheap, but the first query after idle is slow; the status check wakes it.

## Known limits

- **Missed escalations.** On dev, 30 L2-labelled tickets are routed to L1 by 2.0, mostly intermittent Wi-Fi or
  network issues for which the SOP has no trigger. A possible rule ("network issue affecting POS during service → L2")
  is evaluated offline by `eval/whatif_network_rule.py` and stays inactive unless ServeWell adopts it.
- **Run-to-run variance.** With identical inputs, code and configs, the model's choices of runbook and escalation
  criteria differ between runs. Across the 3 held-out runs:
  - routing changed on 13 of 43 tickets for 1.0 and on 6 of 43 for 2.0;
  - pilot routing moved by up to 25 points.

  This can't be turned off, because neither model accepts a temperature setting. Compare agents on repeated runs.
- **Safety rules came after the evaluation.** The dev, held-out and chaos numbers are from 2.0 without them. Whether
  they fire on any of those tickets is unchecked. A keyword scan of the dev and held-out ticket text finds no fire,
  smoke, spark or shock mentions, and no repeated mains power loss.
- **Integration trigger.** The SOP's integration trigger escalates some online-order tickets that are labelled L1. It
  is kept as the SOP says.
- **Chaos verdicts are LLM-judged.** They haven't all been reviewed by hand.
- **Test batches share the database.** The recent-ticket lookup sees every loaded ticket at a store, so remove a test
  batch after use (`ax15 clear-batch`).
- **No live ITSM.** Intake is the console form, the batch folder and the command line.
- **Not built:** MCP servers, voice and Indic languages, and cloud deployment.
- **ROI.** ServeWell's baseline numbers aren't available yet, so ROI figures are assumptions.

## What's next

1. Review the safety rules and the proposed network rule with ServeWell, then re-run the held-out evaluation with
   repeated runs.
2. Pilot on POS and receipt printers, measuring L1 deflection, escalation quality and time to first response.
3. Grow the knowledge base where the picker escalates for lack of a runbook (online orders, loyalty, VPN).
4. Production path: managed Postgres, an ITSM webhook for intake, and monitoring of routing mix, guard strips,
   KB-gap reports and cost per ticket.

## Repo layout

```
app/               Streamlit console (console.py) and a standalone retrieval bot
config/            models.yaml (model ids), sop_rules.yaml (SOP as rules)
data-original/     the original ServeWell dataset (read-only)
docs/              deck (pdf, pptx), images/ (architecture diagrams), results/ (evaluation reports)
eval/              evaluation scripts, judge prompt, splits, chaos and test10 expected behaviour
incoming/          landing zone for test batches; test10/ is included
migrations/        numbered, idempotent SQL migrations
prompts/           agent prompts (1.0 system; 2.0 classify, resolve, compose)
scripts/           check_setup.py, heldout_rehearsal.py
src/ax15/          l0_data, l1_knowledge, tools, trust, agents, eval, cli.py, status.py
tests/             pytest
```

## Versions

- **1.0:** the frozen baseline, a single tool-calling agent.
- **2.0 (version 2.0.2):** the current workflow, including the safety rules AX-SAFETY and AX-POWER. The console calls
  it 2.0 and shows the version with a hash of the prompts and config.
- **Results:** the dev, held-out and chaos results are from 2.0 before the safety rules. test10 shows both.
