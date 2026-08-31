# Phase 7 Harness core loop review

## Review question

The human gate asks whether GeneralAgent, AgentSpec, the persisted Step graph, and
no-capability failure semantics are suitable as the long-term Harness baseline.
Automated completion does not create a human signature.

**Status: PENDING — no approver, approval date, or approval conclusion has been
recorded by AI.**

## Boundary

Phase 7 turns the Harness into a real runtime loop without implementing real tools.
Every ordinary Run persists the core sequence:

```text
Observe -> Understand -> Plan -> Act -> Verify -> Reflect -> Respond
```

Act is represented by zero or more `CAPABILITY` RunSteps. The only external execution
path remains:

```text
Harness -> Capability Gateway -> Policy -> connector executor
```

The Harness does not open production resources, emit ad hoc SQL, call HTTP
connectors directly, or treat a model answer as a successful capability result.

## GeneralAgent and AgentSpec

- GeneralAgent is the default conversational entry point.
- AgentSpec is parsed from the promoted AgentVersion and controls the default logical
  ModelProfile, max step budget, timeout, skills, capabilities, risk policy, memory,
  and sandbox policy.
- AgentSpec may bind only `modelPolicy.profile`. Provider names, model IDs, base
  URLs, credentials, and API keys remain outside AgentSpec and behind Model Gateway.
- Built-in Agent specs and declarative YAML manifests use the same validation path.

## Step execution

The Step executor is a deterministic DAG scheduler. It identifies:

- ready executable Capability waves;
- blocked steps whose dependencies failed, were skipped, or were cancelled;
- deadlocks caused by missing or cyclic executable dependencies.

It does not import connector implementations, HTTP clients, SQL clients, or model
providers. It chooses only which persisted RunSteps can move next.

## Acceptance semantics

- A greeting such as `你好` is routed as non-factual conversation. It still completes
  Observe, Understand, Plan, Verify, Reflect, and Respond steps, but it requires no
  Evidence, creates no Claim, and does not call a Capability or model.
- A request such as `查生产库` is routed as controlled resource access. The plan may
  request only the `data.query` Capability with a production resource descriptor; it
  must not synthesize SQL inside the Harness. With no production Capability binding,
  the Run fails as `capabilities_unavailable`, the Capability step records the
  underlying error, and Verify/Reflect/Respond are skipped through dependency failure.
- Factual answers still require Claim-Evidence verification. The no-claim path is
  available only for evidence-free, non-factual conversation plans.

## Automated acceptance map

- `test_phase7_harness_core.py` covers the two required prompts, persisted Step order,
  plan storage, no-tool greeting behavior, production database failure behavior, and
  event completeness.
- `test_step_executor.py` covers ready, blocked, and deadlocked DAG behavior.
- `test_registry_manifests.py` covers AgentSpec parsing, manifest validation, and the
  ModelProfile-only model policy boundary.
- `test_critic.py` covers the non-factual no-claim exception and preserves factual
  empty-evidence rejection.
- Contract gates keep OpenAPI, Event schemas, error-code manifests, and producer
  analysis synchronized.

Executed gate evidence for this Phase:

- Phase 7 targeted tests passed: `test_phase7_harness_core.py`,
  `test_step_executor.py`, `test_critic.py`, and `test_registry_manifests.py`
  completed 12 tests.
- Full Python and Python SDK suite passed with 325 tests and 18 opt-in PostgreSQL
  skips.
- Non-destructive PostgreSQL integration tests passed against a disposable
  PostgreSQL/pgvector database with 15 tests and three destructive migration tests
  intentionally skipped.
- A fresh disposable PostgreSQL/pgvector database upgraded through the complete
  Alembic chain, and `alembic check` reported no drift.
- Ruff lint, Ruff format check, strict mypy, Event/error contract validation,
  registry validation, evaluation validation, OpenAPI freeze, frontend lint,
  frontend typecheck, frontend production build, TypeScript SDK tests, Compose
  rendering, and Helm lint/template rendering all passed.

## Human review checklist

- Confirm that GeneralAgent is the right default entry point for employees.
- Confirm that AgentSpec contains enough policy/budget/sandbox fields for later
  registry and policy phases without exposing provider details.
- Confirm that the persisted Step graph is the compatibility baseline for Workbench,
  replay, audit, and future Evidence Fabric work.
- Confirm that the no-Evidence response exception remains limited to non-factual
  conversation and cannot be used for business, data, incident, code, or knowledge
  claims.
