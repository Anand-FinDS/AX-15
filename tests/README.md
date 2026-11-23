# Tests

pytest suites for every layer: data loading and leakage stripping, knowledge base and retrieval, tools, SOP rules,
guard, 1.0 and 2.0 agents (with fake models), batch intake, console (Streamlit AppTest) and status checks.

```bash
pytest -m "not llm"      # everything that does not call a paid model
pytest                   # also the few live tests (needs API keys; costs a fraction of a cent)
```

Tests marked `db` need `DATABASE_URL` and skip without it.
