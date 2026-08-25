# Requirements traceability

This document turns the source blueprint into verifiable product and engineering
commitments. A feature is complete only when its contract, persistence, enforcement,
tests, telemetry, operator documentation, and user experience are all present.

## Product boundary

Obsion V1 is the complete first-generation system described by the blueprint: an
Enterprise Agent Runtime and Intelligence Workspace built on Workspace, Harness,
Capability, Evidence, and Policy. It includes the foundation, the three primary
intelligence scenarios, verified answers, and the usable workspace described in
phases 0 through 7.

Read-only recurring work uses immutable deterministic workflows that submit ordinary
Harness Runs under the current accountable owner's permissions. The Phase 7 action
plane is a deliberately closed V1 surface: only idempotent PR and ticket actions in
development/staging can cross it. Production actions, database writes, deployments,
restarts, and configuration changes remain globally denied. This is a product safety
requirement, not an incomplete implementation.

## User-facing flows

### Governed knowledge

1. The user asks one assistant and may scope the question to a workspace.
2. GeneralAgent routes internally to KnowledgeAgent.
3. Retrieval filters organization, principal ACL, classification, and document
   version before ranking.
4. Authorized chunks are normalized into Evidence with source and lineage.
5. The answer is decomposed into Claims linked to Evidence and checked by the
   Critic.
6. The Workbench exposes citations, confidence, source time, and the run trajectory.

### Governed analytics

1. Understanding resolves intent, time range, comparison, metric, dimensions, and
   risk.
2. DataAgent resolves approved semantic objects and creates a logical query plan.
3. The dialect compiler produces SQL; an independent AST policy rejects writes,
   multiple statements, unsafe functions, unknown resources, and unbounded output.
4. Capability Gateway authorizes the user, agent, connector, resource, and risk,
   then executes against a configured read-only data source.
5. Results are masked before they can enter model context and become TABLE, CHART,
   and SQL artifacts plus Evidence.
6. The answer cites metric definitions and query result evidence.

### Incident investigation

1. Understanding identifies service, environment, metric, anomaly window, and
   comparison.
2. IncidentAgent builds a bounded dependency plan across metrics, dimensions,
   deployments, logs, traces, configuration, and code.
3. Independent steps can run concurrently, but each request passes through the same
   Capability Gateway.
4. Normalized observations are correlated by time, service, environment, trace,
   deployment, and commit.
5. The Critic checks temporal consistency, conflicting causes, and evidence coverage
   before the final answer is accepted.
6. Users can inspect, resume, fork, cancel, and replay the investigation.

### Governed action

1. A workspace member creates a schema-validated PR or ticket request for
   development/staging with a client idempotency key.
2. Preflight re-authorizes the owner, resolves active execute and rollback
   capabilities and connectors, and seals their exact versions into one checksummed
   plan.
3. A different authorized user approves the execution plan; approval cannot override
   a policy denial, release boundary, missing grant, or connector failure.
4. The Action worker claims the request with a PostgreSQL lease, re-authorizes the
   owner, records a policy decision, and invokes the real provider with a stable
   idempotency key.
5. A rollback request creates a new approval for the compensating action. Execution
   approval is never reused as rollback authority.
6. The Workbench exposes plan, approvals, attempts, safe outputs, events, failures,
   notifications, and audit evidence without exposing credentials.

## Requirement matrix

| Blueprint area | V1 implementation commitment | Primary verification |
| --- | --- | --- |
| Product positioning | One assistant over governed enterprise capabilities | Workbench and API acceptance tests |
| Workspace lifecycle | Workspace, Thread, Turn, Run, Step, Event and artifacts | State-machine and repository tests |
| Thread lifecycle | Create, resume, fork, archive and inspect | API integration tests |
| Run lifecycle | Pending, running, waiting, replanning, terminal states | Transition property tests |
| Replayability | Immutable inputs, pinned versions, events, evidence and outputs; playback never re-invokes a connector or model | Snapshot fingerprint, version pinning, remapped lineage and no-external-boundary tests |
| Event protocol | Append-only, ordered per aggregate, correlation and causation | Transaction/concurrency tests |
| Agent contracts | Immutable AgentSpec versions with risk and budgets | Schema and registry tests |
| Skills | Versioned procedure, capability and evidence requirements | Schema and promotion tests |
| Capability fabric | Transport-neutral descriptors and connector bindings | Contract tests |
| Capability Gateway | Validate, authorize, approve, broker, execute, mask, evidence, audit; generic invocation stays read-only | Gateway integration tests |
| Governed actions | Closed L3 PR/ticket surface, immutable plan, non-self execute/rollback approval, pinned connector, idempotent attempt, compensation | Action API/worker, provider-recovery, PostgreSQL invariant, and UI tests |
| Policy | RBAC + ABAC + resource + capability rules; allow/mask/ask/deny | Precedence and isolation tests |
| Risk | L0-L5; generic agents capped at read-only L2; dedicated V1 action plane limited to approved L3 PR/ticket writes outside production | Denial and bypass tests |
| Credentials | Resolved only inside connector execution, never in model context | Redaction tests |
| Evidence | Immutable source, observation time, confidence, ACL and lineage | Normalization tests |
| Claims and Critic | Atomic claims, evidence links, conflicts, coverage and confidence | Verification tests |
| Knowledge | Versioned documents, inherited ACL, authorized retrieval, citations | Leakage and citation tests |
| Semantic data | Metrics, dimensions, entities, relations, rules and synonyms | Catalog tests |
| SQL safety | Parse AST, read-only, limit, timeout, resource policy and masking | Adversarial SQL suite |
| Observability | Normalized metric/log/trace/deployment/config/code evidence | Correlation tests |
| Model gateway | Logical profiles, provider adapters, routing, budget and redaction | Adapter and routing tests |
| Memory | Candidate, policy, sensitivity, dedupe, TTL and scoped persistence | Governance tests |
| Artifacts | Text, table, chart, SQL, code, diff, report, dashboard, file, diagram | Storage lifecycle tests |
| Streaming | Resumable ordered run event stream | Cursor reconnect tests |
| Automation | Immutable DAG versions, cron/IANA schedules, idempotent background executions, leases and concurrency policy | Automation API, worker and PostgreSQL invariant tests |
| Operational ownership | Scheduled work re-authorizes the current owner; human review and notification are durable and audited | Permission-revocation, review and delivery tests |
| Administration | Users, roles, models, agents, skills, capabilities, connectors, policies, approvals, audits, evaluations, costs, prompts, knowledge and secrets metadata | Admin API/UI tests |
| Evaluation | Explicit Golden Dataset evaluators, real terminal-Run observations, full configuration snapshots, immutable case evidence, score gates and exact-snapshot baseline comparison | Manifest validation, API/SDK tests, regression tests and PostgreSQL immutability tests |
| Observability | OpenTelemetry-compatible run, model, tool and policy telemetry | Span/metric assertions |
| Open-source quality | License, governance, contribution, security, CI and reproducible deployment | Repository quality checks |

## Non-negotiable acceptance gates

- No agent or skill imports a connector implementation directly.
- Every capability invocation has a persisted policy decision and audit record,
  including denied and failed invocations.
- Every tenant-owned query is scoped by organization at the repository boundary.
- Production SQL is read-only, single-statement, bounded, timed out, and executed by
  a separately configured read-only identity.
- Retrieval authorization occurs before ranking; post-filtering alone is invalid.
- Secrets, credentials, raw PII, and authorization tokens never enter events, model
  payloads, artifacts, telemetry, or exception messages.
- A completed factual answer exposes Claims, Evidence coverage, source timestamps,
  and Critic status.
- A run can be resumed, forked, cancelled, inspected, and replayed without relying
  on ephemeral process memory.
- A recurring occurrence creates at most one execution, pins one immutable workflow
  version, and cannot acquire more authority than its accountable owner currently has.
- An action cannot execute without an immutable plan, current owner authority, a
  non-self approval for the exact checksum, and a real pinned provider. Execute and
  rollback have separate approvals, attempts, policy decisions, and idempotency keys.
- Development authentication cannot start in a production environment.
- V1 rejects every production action, deferred config/restart/deploy action,
  non-idempotent or destructive write, and all L4-L5 operations even if another
  policy appears to allow them.
