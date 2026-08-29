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
3. Each request passes through the same Capability Gateway and read-only connector
   boundaries; no repair, restart, reconfiguration, deployment write, or auto PR exists.
4. Normalized observations are fused into at most three ranked candidate root causes;
   every root-cause Claim links at least two distinct Evidence types.
5. Independent verification retains the evidence timeline and unresolved conflicts and
   downgrades unsupported candidates rather than presenting them as confirmed facts.
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
| Product positioning | One identity-gated assistant over governed enterprise capabilities, presented through one responsive left-navigation/center-conversation/right-Runtime shell | Workbench composition, real Turn timeline, mobile geometry, and API acceptance tests |
| Identity and tenancy | Explicitly authenticated provisioned users, organization-owned departments, six stable system roles, custom-role safety, digest-only revocable browser sessions shared by REST/App Server, Origin controls, and repository plus composite-foreign-key tenant isolation | Missing/invalid credential, browser exchange/revoke, REST/WebSocket parity, Origin denial, role/department API, cross-Workspace, PostgreSQL adversarial-write/session, downgrade/re-upgrade, and schema-drift tests |
| Workspace lifecycle | Workspace, Thread, Turn, Run, Step, Event and artifacts | State-machine and repository tests |
| Workspace collaboration | Versioned task state machine; immutable decision revisions, disposition, and supersession lineage | API/SDK, tenant-isolation, PostgreSQL invariant, migration, and responsive Workbench tests |
| Thread lifecycle | Create, resume, fork, archive and inspect; parent/Turn lineage, ordered events and audits are durable, forks inherit only through a frozen source Turn (including nested forks), fork archives its source as read-only until explicit resume, and active Runs block direct archival | API/SDK lifecycle, fixed fork-history, source read-only/resume, one-Turn/multiple-Run replay, event cursor, audit, active-Run conflict, tenant-isolation, and responsive Workbench tests |
| Conversation continuity | Prior effective Turns become a bounded, redacted, classified, immutable Run input; collaborator input is untrusted, later parent/source activity is excluded, and history never substitutes for Evidence | Temporal/fork capture, budget, model-message ordering/trust, replay fingerprint, tenant isolation, PostgreSQL invariant, API/SDK, and responsive Workbench tests |
| Unified App Server | One authenticated WebSocket/JSON-RPC 2.0 transport adapter exposes Thread, Turn, Run, Approval, Artifact metadata and realtime event methods to Web/IDE/CLI/API clients through one application facade; the transport cannot access persistence, Harness, or Model Gateway directly | Static layer-boundary, protocol parser, handshake/origin/subprotocol, method, tenant-isolation, SDK, Workbench and end-to-end tests |
| App Server retry safety | Every protocol mutation has a durable principal-scoped request key and transactionally recorded result/error; key reuse conflicts and completed outcomes are immutable until retention expiry | Sequential/concurrent retry, payload-conflict, migration and PostgreSQL trigger tests |
| Run lifecycle | Exact pending/running/waiting-approval/waiting-user/replanning/completed/failed/cancelled graph; terminal states are immutable; cancel atomically terminates the Run, active Steps, lease, Events and audit, and blocks later Step/answer work | Exhaustive transition matrix, blocking dependent-Step cancellation, late-completion guard, event ordering, and audit tests |
| Replayability | Immutable inputs, pinned versions, conversation/memory/evidence snapshots, events and outputs; playback never re-invokes a connector or model | Snapshot fingerprint, version pinning, remapped lineage and no-external-boundary tests |
| User satisfaction | One redacted, versioned rating per principal and terminal Run; current-record tenant projection and actionable improvement reason | API/SDK lifecycle, idempotency, event ordering, tenant isolation, PostgreSQL invariant, admin projection, and responsive Workbench tests |
| Event protocol | Append-only, ordered per aggregate, correlation and causation; every Run-associated event also has a cross-aggregate monotonic Run cursor; WebSocket and SSE resume across connections; no second message or trajectory model | Transaction/concurrency, single-protocol architecture guard, actual disconnect/reconnect, Last-Event-ID contract, and cross-aggregate cursor tests |
| Agent contracts | Immutable AgentSpec versions with risk and budgets | Schema and registry tests |
| Skills | Versioned procedure, capability and evidence requirements | Schema and promotion tests |
| Capability fabric | Transport-neutral descriptors and connector bindings | Contract tests |
| Capability Gateway | Validate, authorize, approve, broker, execute, mask, evidence, audit; generic invocation stays read-only | Gateway integration tests |
| Governed actions | Closed L3 PR/ticket surface, immutable plan, non-self execute/rollback approval, pinned connector, idempotent attempt, compensation | Action API/worker, provider-recovery, PostgreSQL invariant, and UI tests |
| Policy | RBAC + ABAC + resource + capability rules; allow/mask/ask/deny | Precedence and isolation tests |
| Risk | L0-L5; generic agents capped at read-only L2; dedicated V1 action plane limited to approved L3 PR/ticket writes outside production | Denial and bypass tests |
| Credentials | Resolved only inside connector execution, never in model context | Redaction tests |
| Evidence | Immutable source, observation time, confidence, ACL and lineage; every Claim-linked item is directly inspectable in the Workbench | Normalization tests and Claim-to-Evidence responsive browser tests |
| Claims and Critic | Atomic claims, evidence links, conflicts, coverage and confidence; independent deterministic rules and immutable verification assessment graph | Critic, Harness, replay, and PostgreSQL verification tests |
| Knowledge | Versioned documents, inherited ACL, authorized retrieval, citations | Leakage and citation tests |
| Semantic data | Metrics, dimensions, entities, relations, rules and synonyms; complete validated definitions and read-only source/table/metric lineage are inspectable in API, SDKs, and Workbench | Catalog definition/lineage, tenant-isolation, SDK contract, and responsive Workbench tests |
| SQL safety | Parse AST, read-only, limit, timeout, resource policy and masking | Adversarial SQL suite |
| Observability | Phase 17 bounded read-only metric query/compare/anomaly, log search/aggregate, and deployment listing through the Capability Gateway; provider payloads normalize to a shared ObservabilityEvent subset and become Evidence; Phase 18 adds read-only Git/change lineage with repository allowlists; writes, restarts, and trace dashboards remain outside the boundary | Connector normalization/error tests, Gateway/Evidence/audit coverage, registry and static contract gates |
| Model gateway | Logical profiles; provider-neutral completion/JSON/tool calls; tenant/classification/region/context/capability routing; forced private handling; profile-scoped fallback; redacted per-attempt token/cost accounting | Profile replacement, tool-schema fail-closed, private override, fallback accounting, PostgreSQL, and static Agent/frontend boundary tests |
| Memory | Four-scope candidate/decision lifecycle, ownership, sensitivity floor, dedupe, bounded TTL, authorized context budgets, immutable Run snapshots and inspection | Policy, tenant-isolation, replay, PostgreSQL invariant, API/SDK and Workbench tests |
| Artifacts | Text, table, chart, SQL, code, diff, report, dashboard, file, and diagram; readable workspace artifacts can be selected as governed Run context | Storage lifecycle, attachment authorization/Evidence, and responsive Workbench tests |
| Streaming | Bounded multiplexed Run subscriptions emit domain event names, reauthorize access, heartbeat, complete terminal streams, and resume by Run cursor; REST/SSE remain compatible | WebSocket and REST cursor reconnect, terminal completion and authorization tests |
| Automation | Immutable DAG versions, cron/IANA schedules, idempotent background executions, leases and concurrency policy | Automation API, worker and PostgreSQL invariant tests |
| Operational ownership | Scheduled work re-authorizes the current owner; human review and notification are durable and audited | Permission-revocation, review and delivery tests |
| Administration | Users, roles, models, agents, skills, capabilities, connectors, policies, approvals, audits, evaluations, costs, prompts, knowledge and secrets metadata; credential-safe projections and approve/reject lifecycle | Admin API/UI and approval tests |
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
- A reconnect retry cannot repeat an App Server lifecycle mutation when it supplies
  the same principal-scoped client request ID; a different method or payload cannot
  reuse that authority.
- A Run stream cursor is monotonic across events from every primary aggregate and is
  advanced only after delivery; aggregate-local sequence numbers are never used as a
  substitute.
- A reconnect on a different WebSocket/SSE connection resumes from the last processed
  Run cursor without a gap, and a committed cancellation cannot start another Step,
  publish an answer, or transition out of `CANCELLED`.
- Satisfaction feedback cannot alter verification, must remain caller-scoped at the
  Run boundary, and a revision cannot silently overwrite a newer version.
- A memory can affect a Run only when it is approved, unexpired, authorized at capture
  time, linked to a persisted policy decision, and copied into an immutable snapshot;
  deterministic replay uses that snapshot rather than current memory state.
- A recurring occurrence creates at most one execution, pins one immutable workflow
  version, and cannot acquire more authority than its accountable owner currently has.
- A task update cannot silently overwrite another member's version, and a formed
  decision cannot be rewritten or removed. Replacing an accepted decision preserves
  both records and atomically links their supersession lineage.
- An action cannot execute without an immutable plan, current owner authority, a
  non-self approval for the exact checksum, and a real pinned provider. Execute and
  rollback have separate approvals, attempts, policy decisions, and idempotency keys.
- Development authentication cannot start in a production environment.
- Development authentication still requires an explicit bearer credential; a missing
  or incorrect credential cannot enter any protected REST or App Server operation.
- User-role, department, Workspace-owner, and Workspace-member relationships cannot
  cross an organization at either the repository or PostgreSQL foreign-key boundary.
- V1 rejects every production action, deferred config/restart/deploy action,
  non-idempotent or destructive write, and all L4-L5 operations even if another
  policy appears to allow them.
