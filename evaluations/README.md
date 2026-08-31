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
uv run obsion validate-eval-gates
uv run obsion evaluate-datasets
```

The `v1-knowledge-qa` dataset contains 20 KnowledgeAgent cases, including explicit
user, role, and department denial cases that require zero recall and an unknown answer.
The routing and safety dataset also includes a metric-decline case that locks the
DataAgent route and root-cause classification before execution. Agent-quality
RUN_OUTPUT contracts cover Knowledge, Data, Incident, Engineering, Support,
Operation, and Analytics; CI binds `run_ref` names to real terminal Runs.

Incident RUN_OUTPUT cases may additionally assert `minimum_incident_candidates`,
`minimum_cross_type_claims`, and `incident_top1_evidence_types`; these checks keep the
Top1/Top3 candidate and two-Evidence-type Claim contract in regression tests.
