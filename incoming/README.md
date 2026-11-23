# Landing zone for test batches

Put each test batch in its own folder:

```
incoming/<batch_name>/
  *.csv         tickets with train-like columns (list cells may be JSON strings; missing optional columns are fine)
  *.json        one ticket or an array, same schema as data-original/tickets/train
  *.txt         optional free-text tickets, one per file
  labels.json   optional ground truth -> results are scored automatically
```

Run: `ax15 run-batch incoming/<batch_name>` (add `--agent v1` for 1.0). Remove it again with
`ax15 clear-batch <batch_name>`. Batches are loaded with `batch_id=<batch_name>` and `source=panel_test`, run in eval
mode (no actions written) and are never used for tuning. Example: `incoming/test10/`.
