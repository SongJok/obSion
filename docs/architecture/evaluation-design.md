# Evaluation architecture

## Purpose

Obsion evaluations are release evidence, not a self-reported score. A Golden Dataset
case declares one explicit evaluator and immutable expectations. An Evaluation Run
pins the dataset snapshot, Agent version and checksum, resolved Skill, Capability and
Prompt versions, model-profile routing metadata, application revision, gate policy,
and any terminal Harness Runs used as observations.

The control plane supports three V1 evaluator contracts:

- `ROUTING` executes the real Understanding routing component and checks declared
  intent, route and risk fields.
- `SQL_POLICY` executes the production SQL AST policy and checks allow/deny outcome,
  error code, authorized tables and the bounded query contract.
- `RUN_OUTPUT` inspects a real terminal Harness Run whose Agent and model-profile IDs
  match the evaluation pins. It checks route, intent, capabilities, SQL, Evidence,
  answer requirements, Claim coverage, citation validity and verified-claim ratio.

`fixtures.actual` is rejected. Answer evaluation never accepts a case's claimed output
as the observed system result.

## Golden Dataset and Run bindings

Version-controlled cases use stable `run_ref` names instead of environment-specific
Run IDs. CI or an operator first executes each candidate question through the normal
Workbench/API path, waits for a terminal Run, and supplies a `run_bindings` map when
starting the Evaluation Run. This separates immutable expected behavior from the
candidate execution while retaining exact Run, Evidence and Artifact provenance.

The repository validates all dataset contracts with:

```bash
uv run obsion validate-evaluations
```

The command is part of `make check` and CI. It rejects missing evaluator types,
unsupported expectations, duplicate case revisions and self-reported actual output.

## Result and regression model

Every case creates an `EvaluationCaseResult` containing its case fingerprint,
evaluator, pass/fail/error status, boolean checks, normalized scores, safe observed
metadata and Evidence fingerprints. Raw answers are represented by hashes and lengths;
the result points back to governed Evidence instead of copying source content.

An Evaluation Run aggregates pass rate, error count and named score averages. It may
pin a completed baseline over the exact same dataset snapshot. The release gate fails
when pass rate is below its minimum, a named score is below threshold, a case errors,
or regression rate exceeds its limit. Regressed and improved case revisions remain
visible in the metrics contract.

PostgreSQL prevents updates and deletes of case results and terminal Evaluation Runs.
This makes a passed gate durable evidence rather than mutable dashboard state.
