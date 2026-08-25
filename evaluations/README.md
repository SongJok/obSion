# Obsion Golden Datasets

Files in `datasets/` are version-controlled release contracts. Every case must declare
`ROUTING`, `SQL_POLICY`, or `RUN_OUTPUT`; case revisions are immutable after ingestion.

`RUN_OUTPUT` cases use a stable `run_ref`. A regression job executes the candidate
Agent through the ordinary governed API and binds that name to the resulting terminal
Run ID when it starts the evaluation. See `examples/run-output-case.json` and the
[evaluation architecture](../docs/architecture/evaluation-design.md).

Validate all committed datasets before opening a pull request:

```bash
uv run obsion validate-evaluations
```
