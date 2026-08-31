# API contract

Obsion exposes JSON management APIs under `/api/v1`, health probes under `/health`, and
the OpenAPI document at `/api/openapi.json`. Interactive Swagger UI is enabled at
`/api/docs` outside production. The checked-in `openapi.json` is generated from the
same FastAPI schemas used at runtime.

The unified bidirectional App Server is available at `/api/v1/app-server` using the
`obsion.jsonrpc.v1` WebSocket subprotocol. Its protocol version is `2026-08-26`.
Clients receive `server.ready`, then must send `server.initialize` before any other
request. The complete method, error, idempotency, and subscription contract is in the
[App Server protocol](../architecture/app-server-protocol.md).

## Authentication

Development mode still requires an explicit `OBSION_DEV_BEARER_TOKEN` and maps only
that credential to the seeded local administrator; omitting credentials is never a
local-login fallback. Production uses an OIDC access token and validates signature,
issuer, audience, algorithm, expiry, subject, organization mapping, roles, and
permissions.

Browser clients exchange the access token through `POST /api/v1/auth/session`, receive only a
revocable opaque `HttpOnly`/`SameSite=Strict` cookie, inspect the current safe Principal
DTO with `GET /api/v1/auth/session`, and revoke/logout with
`DELETE /api/v1/auth/session`. The cookie
authenticates REST and App Server initialization without exposing the token to browser
JavaScript. The server stores only a session digest, rejects disallowed Origins for
unsafe cookie requests, disables caching on session responses, and marks the cookie
Secure in staging/production. OpenAPI declares Bearer and the default `obsion_session`
cookie as alternative security schemes; deployments that rename the cookie must apply
the same name to their generated client/edge configuration. SDK, CLI, and service clients continue to use
`Authorization: Bearer ...`; explicit Bearer takes precedence over a browser cookie.

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
  `POST /threads/{id}/archive|resume|fork` implements the persisted Thread lifecycle,
  while `GET /threads/{id}/events` exposes its ordered history with `after_sequence`
  cursors. A fork reads inherited Turns and Runs only through its persisted source
  Turn; later parent activity never enters that branch, and nested forks preserve the
  same fixed history. Forking archives the source Thread as read-only until an explicit
  resume. A direct archive request is rejected while any Run in the Thread is active.
  Turn `attachment_refs` accept only artifacts in the same authorized workspace;
  the Harness safely parses supported content, redacts it, and persists Evidence with
  artifact checksum lineage before model use.
  `GET /workspaces/{id}/files` lists path-versioned FILE artifacts. A file path is
  data; it is not SYSTEM or Skill text until a Turn attaches the artifact.
  `GET /workspaces/{id}/reports` lists published REPORT artifacts from evidenced
  Runs. Conversation greetings do not appear there.
  `GET /workspaces/{id}/dashboards` lists published DASHBOARD artifacts that
  reference existing CHART/TABLE/SQL rows. They do not invent series.
  `GET /workspaces/{id}/sql` lists published SQL artifacts. It does not execute
  the warehouse or invent SELECT text.
  `GET /workspaces/{id}/evidence` lists persisted Evidence rows for Runs in the
  workspace. It does not invent citations.
  `GET /workspaces/{id}/timeline` lists persisted Run Events for those Runs. It
  does not invent Harness steps.
- `/experience/im/messages` ingests a bound sender into one Turn. Feishu live
  delivery uses `POST /experience/im/runs/{id}/deliveries` then
  `/deliveries/{id}/complete|fail`. The adapter posts to Feishu only after that
  Policy-authorized receipt. Generic HTTP delivery is not a public API.
- `/runs/{id}` supports inspection, cancellation, and deterministic terminal-run
  replay. Replay copies the immutable recorded snapshot and never re-invokes a model
  or connector; use a new Turn when current external state is required. Cancellation
  immediately commits a terminal Run, clears its worker lease, cancels all active
  Steps, and prevents any dependent Step or answer from starting afterward.
- `GET /runs/{id}/conversation` returns the exact bounded prior-Thread context frozen
  when the Run was created. Each row exposes source lineage, classification, capture
  time, redacted user/assistant content, and a fingerprint; it is context, not Evidence.
- `/runs/{id}/events` and `/runs/{id}/events/stream` expose ordered replayable events;
  use `after` or `Last-Event-ID` to resume (the newer value wins). These cursors use `run_sequence`, while
  `sequence` remains the event's primary aggregate position. Consumers deduplicate by
  immutable Event ID.
- `/runs/{id}/steps|evidence|claims|artifacts|memories` expose the verification
  trajectory. The memory endpoint returns the immutable, policy-linked context
  snapshots captured for that Run, not the current mutable memory view.
- Incident answer Artifacts include an `incident_fusion` projection with ranked Top1/Top3
  candidate root causes, Evidence type coverage, bounded timeline, and unresolved conflicts.
- `GET /admin/audit` is the tenant-scoped audit projection. Each row includes the
  correlation/actor identity, action/resource, outcome, policy and approval IDs,
  risk, latency, and recursively redacted metadata. Gateway and Run records include
  canonical agent, model profile, capability, resource, result classification, and
  error dimensions; prompt credentials are never persisted in the Turn input.
- `GET|PUT /runs/{id}/feedback` reads or records the caller's rating after the Run is
  terminal. The first write omits `expected_version`; every changed revision supplies
  the version last read. Identical content is idempotent.
- `/capabilities/{name}/invoke` executes only within an existing active run and always
  crosses schema, policy, risk, approval, rate-limit, secret, masking, evidence, audit,
  and telemetry boundaries.
- `/knowledge`, `/data`, `/memories`, and `/approvals` expose governed domain actions.
  Knowledge questions are internally routed to the pinned L1 `knowledge-agent`/
  `knowledge-qa` Skill. Run answer artifacts include deterministic citations linked to
  DOCUMENT Evidence; when authorized retrieval has no substantive hit, the answer is
  explicitly `不知道` and carries no Claim. Document and Chunk ACL checks happen before
  ranking and are reused by detail/download endpoints.
  `POST /knowledge/sources/feishu/documents` fetches one Feishu docx (or a wiki
  node that resolves to docx) through the `feishu-docs` connector and writes it
  into that same pipeline. It is not an IM delivery.
  `GET /knowledge/sources/feishu/spaces` and
  `GET /knowledge/sources/feishu/spaces/{space_id}/nodes` list wiki spaces and
  walked nodes. `POST /knowledge/sources/feishu/spaces/{space_id}/sync` ingests
  each `docx` node through the same pipeline and records non-docx nodes as
  skipped. Space listing is not invented when Feishu denies the call.
  `POST /knowledge/sources/dingtalk/documents` fetches one DingTalk cloud
  document through the `dingtalk-docs` connector. Workspace list and sync
  endpoints walk `api.dingtalk.com` only and skip unsupported node types.
  `POST /knowledge/sources/wecom/documents` fetches one WeCom wedoc through the
  `wecom-docs` connector. Space describe/list/sync endpoints require an
  operator-supplied WeDrive `space_id` and skip nodes without a resolvable
  `docid`.
  `POST /knowledge/sources/confluence/pages` fetches one current Confluence
  Cloud page through the `confluence` connector. Space list and sync endpoints
  skip non-current pages. Pagination cannot leave the Cloud site origin.
  `GET /data/metrics` returns validated metric definitions, and
  `GET /data/lineage/{metric_id}` returns the caller tenant's read-only data-source,
  table, and metric chain for inspection in clients without executing a query.
  Administrators define versioned metrics, dimensions, entities, relations, business
  rules, time definitions, and tenant-safe synonyms under `/admin/data/*`; a DataAgent
  query resolves these IDs into a deterministic logical-plan compilation and rejects
  unregistered metrics before any capability call. `/data/sql/validate` applies the
  parser/AST policy to a read-only source and rejects unbounded SQL unless the caller
  supplies an explicit LIMIT; `/data/sql/explain` returns the bounded policy plan,
  scan estimate, and a tenant-scoped audit ID. SQL execution remains behind the
  read-replica Query Gateway, where timeouts, scan budgets, row policies, and column
  masking are enforced.
  Metric-bearing questions, including decline follow-ups, are internally pinned to the
  DataAgent/governed-analytics Skill; root-cause segmentation remains within governed
  dimensions and never expands into logs or traces. Successful runs expose SQL, table,
  and (when a temporal numeric series exists) trend-chart Artifacts linked to the same
  DATA Evidence.
  Read-only observability bindings expose bounded metric (`metric.query`,
  `metric.compare`, `metric.anomaly`), log (`log.search`, `log.aggregate`), and
  deployment (`deployment.list`) operations through the Capability Gateway. Provider
  responses are normalized into `ObservabilityEvent` Evidence; upstream failures and
  malformed responses retain stable structured error codes. Trace dashboards,
  Kubernetes restarts, and deployment writes are outside this surface.
  Read-only engineering bindings expose `git.commit`, `git.diff`, `git.history`,
  `deployment.commit`, and bounded `code.search` operations through an
  `engineering.v1` connector. Commit/deployment responses become normalized CODE or
  DEPLOYMENT Evidence; repository allowlists fail closed before network access, and
  auto-PR or deployment mutation is not part of the capability surface.
  Memory candidates use one exact TURN, SESSION, WORKSPACE, or USER_PREFERENCE owner,
  receive a bounded expiry and persisted policy decision, and require explicit
  approval before Harness use. `GET /memories` supports `scope`, `owner_ref`, and
  `status` filters; approval and rejection require a reason.
- `/workspaces/{id}/tasks` creates and lists versioned collaboration tasks;
  `/workspace-tasks/{id}` requires `expected_version` for every update and the event
  endpoint exposes its ordered trajectory.
- `/workspaces/{id}/decisions` creates and lists governed decision records.
  `/workspace-decisions/{id}` creates a new immutable content revision, while
  `/accept|reject`, `/versions`, and `/events` expose disposition, history, and audit
  trajectory. Accepting a replacement atomically supersedes its linked prior decision.
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
  evaluations, and audit metadata. `POST /admin/connectors/{id}/health` and
  `/discover` probe Connector SDK adapters; discover never auto-binds Capabilities.
  `POST|GET /admin/models/profiles` manages logical,
  secret-free Profile requirements and fallback policy;
  `POST|GET /admin/models/endpoints` manages provider/model metadata, capabilities,
  classifications, pricing, egress base URL, and gateway-only credential references
  (GET returns only `has_credential`); and
  `POST /admin/models/profiles/{id}/endpoints` binds an endpoint at an explicit
  fallback priority. Agents and browser clients never receive these provider choices
  as a model selector.
- `/admin/feedback/summary` requires audit-read authority and reports the current
  tenant response counts and helpful rate without exposing individual reasons.
- `/admin/slo` requires audit-read authority and projects success, replan, approval,
  satisfaction, evidence coverage, tokens, cost, steps, and mean latencies from
  PostgreSQL. TTFT is histogram-only and is not a p95 SLA.
- `/admin/evaluations/datasets/{id}/runs` starts a deterministic release gate.
  `run_bindings` connects Golden Dataset `run_ref` values to real terminal Runs;
  `/admin/evaluations/runs/{id}/results` exposes immutable per-case checks, scores and
  Evidence references. Baselines must use the exact same dataset snapshot.

Create-turn, replay, and evaluation-run commands return the created resource and use
durable identifiers. App Server lifecycle mutations require `client_request_id`; reuse
it only for the exact same method and validated params. The server records the result
or domain error in the mutation transaction, so reconnect retries do not repeat the
operation. REST callers should retry reads and event-stream connections, but must not
blindly retry capability invocations or approval decisions. Workflow triggers accept
their own idempotency key and clients should reuse it only for the same occurrence.

Task updates and decision revisions or dispositions must reuse the version last read
by the client. A version conflict means the record was changed by another actor;
refresh, review the new state, and issue a new explicit request rather than
automatically replaying the stale mutation.

Feedback revisions follow the same refresh-on-conflict rule. A feedback rating is a
product signal only: clients must not present it as Evidence, Claim verification, or
authorization input.

Action creation requires a caller-supplied `idempotency_key`. Reuse it only for the
same workspace, owner, action type, environment, target, parameters, rollback
parameters, and timeout; conflicting reuse returns a stable conflict error. Do not
retry approval decisions with a different reason, and never call an action provider
directly. The server owns provider attempt keys and reuses the same key when recovering
an expired worker lease or a lost response.

Vendor Knowledge ingest/sync POST routes use the validated `X-Request-ID` UUID as a
principal-scoped no-Run Capability idempotency key. Exact retries replay the immutable
terminal Gateway result without consuming another connector rate slot or resolving a
credential. Reusing the UUID for different canonical input returns
`idempotency_key_reused`. An expired in-progress attempt becomes
`operator_invocation_outcome_unknown` and is never automatically re-executed. Admins
with `audit.read` can inspect the content-free reconciliation projection at
`GET /api/v1/admin/operator-invocations`; inputs and result payloads are not returned.
Safe non-UUID correlation strings remain valid for ordinary API correlation but are
rejected on vendor source operations because they cannot be durable replay keys.

## Regenerating the contract

```bash
uv run obsion openapi --output docs/api/openapi.json
```

Contract changes require compatibility review, updated SDK types, integration tests,
and a changelog entry. Secret values, raw authorization tokens, and production data
must never appear in generated examples.
