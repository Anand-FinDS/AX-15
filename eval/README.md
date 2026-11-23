# Evaluation assets and scripts

Ground truth lives here and in `data-original/labels/`. It is loaded into the database's separate `eval` schema and
is never passed to an agent prompt.

| File | What it is |
|---|---|
| `splits.json` | Seeded (42) dev / held-out split of the 220 labelled train tickets: 176 dev, 44 held-out |
| `chaos_expected_behavior.md` | Our authored expected behaviour for the 36 adversarial ("chaos") tickets |
| `chaos_labels.json` | The same expectations as structured labels (`python -m ax15.l0_data.eval_assets` builds it from the .md) |
| `rag_golden_set.json` | Golden questions for the retrieval layer |
| `test10_expected.md` | 10 new post-freeze test tickets (`incoming/test10/test10.csv`) and what a good answer does |
| `prompts/chaos_judge.md` | The LLM judge prompt for the chaos set |
| `agent_eval.py` | Dev split + chaos set for one agent (cached runs, scoring, judge) |
| `agent_compare.py` | 1.0 vs 2.0 side by side from two eval dumps |
| `heldout_eval.py` | Held-out split, both agents, one run |
| `heldout_variance.py` | Held-out split, 3 fresh runs per agent: run-to-run variance |
| `retrieval_eval.py` | Retrieval quality (doc routing, hit rates) |
| `whatif_network_rule.py` | Offline what-if for a proposed SOP rule (no model calls) |

Results from these scripts are in `docs/results/`.
