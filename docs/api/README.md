# API contract

Obsion exposes JSON management APIs under `/api/v1`, health probes under `/health`, and
the OpenAPI document at `/api/openapi.json`. Interactive Swagger UI is enabled at
`/api/docs` outside production. The checked-in `openapi.json` is generated from the
same FastAPI schemas used at runtime.

## Authentication

Development mode resolves the seeded local administrator. Production uses an OIDC
Bearer token and validates signature, issuer, audience, algorithm, expiry, subject,
organization mapping, memberships, roles, and permissions. The browser sends same-site
credentials when an authenticating reverse proxy is used.

Every response includes `X-Request-ID`. Clients may send their own safe request ID for
correlation. Error responses use:

```json
{
  "code": "stable_machine_code",
  "message": "Operator-safe explanation",
  "correlation_id": "request-id",
  "details": {}
}
```

## Core resources

- `/workspaces`, `/threads`, and `/threads/{id}/turns` manage the durable work context.
- `/runs/{id}` supports inspection, cancellation, and deterministic terminal-run
  replay. Replay copies the immutable recorded snapshot and never re-invokes a model
  or connector; use a new Turn when current external state is required.
- `/runs/{id}/events` and `/runs/{id}/events/stream` expose ordered replayable events;
  use `after` or `Last-Event-ID` to resume without duplicating earlier events.
- `/runs/{id}/steps|evidence|claims|artifacts` expose the verification trajectory.
- `/capabilities/{name}/invoke` executes only within an existing active run and always
  crosses schema, policy, risk, approval, rate-limit, secret, masking, evidence, audit,
  and telemetry boundaries.
- `/knowledge`, `/data`, `/memories`, and `/approvals` expose governed domain actions.
- `/workspaces/{id}/workflows` and `/workflows/{id}` manage deterministic workflow
  definitions, immutable versions, lifecycle, schedules, and manual triggers.
- `/automation/executions/{id}` and `/automation/steps/{id}/review` expose durable
  execution state, cancellation, child Harness Run references, and human decisions.
- `/notifications` exposes the authenticated recipient's durable in-app inbox.
- `/workspaces/{id}/actions` creates and lists governed PR/ticket requests;
  `/actions/{id}/preflight` seals the plan, and `/actions/{id}` plus
  `/actions/{id}/events` expose its approvals, attempts, safe results, and trajectory.
- `/action-approvals/{id}/approve|reject` records an independent execution or rollback
  decision. `/actions/{id}/rollback` requests a separately approved compensating
  operation, while `/actions/{id}/cancel` cancels only an eligible lifecycle state.
- `/admin` manages tenant-scoped registries, bindings, models, policies, catalog,
  evaluations, and audit metadata.
- `/admin/evaluations/datasets/{id}/runs` starts a deterministic release gate.
  `run_bindings` connects Golden Dataset `run_ref` values to real terminal Runs;
  `/admin/evaluations/runs/{id}/results` exposes immutable per-case checks, scores and
  Evidence references. Baselines must use the exact same dataset snapshot.

Create-turn, replay, and evaluation-run commands return the created resource and use
durable identifiers. Consumers should retry reads and event-stream connections; they
must not blindly retry capability invocations or approval decisions. Workflow triggers
accept an idempotency key and clients should reuse it only when retrying the same
logical occurrence.

Action creation requires a caller-supplied `idempotency_key`. Reuse it only for the
same workspace, owner, action type, environment, target, parameters, rollback
parameters, and timeout; conflicting reuse returns a stable conflict error. Do not
retry approval decisions with a different reason, and never call an action provider
directly. The server owns provider attempt keys and reuses the same key when recovering
an expired worker lease or a lost response.

## Regenerating the contract

```bash
uv run obsion openapi --output docs/api/openapi.json
```

Contract changes require compatibility review, updated SDK types, integration tests,
and a changelog entry. Secret values, raw authorization tokens, and production data
must never appear in generated examples.
