# System design

## Architectural shape

Obsion begins as a modular control plane with durable domain boundaries. The runtime can later split high-throughput domains into services without changing public contracts.

```text
Web / IDE / CLI / API / IM adapters
                 |
          Obsion App Server
 REST management + WebSocket JSON-RPC
      + resumable event streaming
                 |
  Harness Runtime Core / Automation / Actions
 observe -> understand -> plan -> act
      -> verify -> reflect -> respond
                 |
 Agent / Skill / Capability registries
                 |
 Capability Gateway (read) / Action Gateway (closed write)
 identity -> policy -> risk -> approval -> audit
 masking -> secrets -> rate limit -> egress control
                 |
 Code | Data | Logs | Knowledge | Runtime
```

## Deployment units

The initial control plane is one Python application with separately testable modules:

- `app_server`: REST and streaming protocol, request identity, lifecycle commands.
- `harness`: orchestration loop, budgets, routing, step execution, cancellation, replay.
- `model_gateway`: model profiles, vendor adapters, routing, usage, and redaction.
- `capability_registry`: versioned descriptors and connector bindings.
- `capability_gateway`: authorization and policy enforcement before execution.
- `actions`: schema-validated requests, immutable plans, independent execute/rollback
  approvals, durable worker claims, pinned idempotent provider invocation, and
  compensating actions through a dedicated Action Gateway.
- `policy`: RBAC, ABAC, resource policy, capability policy, and risk decisions.
- `approval`: durable approval requests and resume tokens.
- `evidence`: normalization, claim linkage, lineage, conflict detection, and critic inputs.
- `knowledge`: ingestion, ACL propagation, retrieval, and citations.
- `data_intelligence`: semantics, logical plans, SQL compilation and validation.
- `observability_intelligence`: normalized metrics, logs, traces, changes, and incidents.
- `memory`: candidate review, deduplication, sensitivity classification, TTL, and persistence.
- `artifacts`: metadata and object-storage lifecycle for reports, files, charts, and results.
- `audit`: append-only security and execution records.
- `evaluation`: datasets, cases, runs, metrics, and regression gates.
- `automation`: immutable deterministic workflow versions, schedules, execution leases,
  concurrency policy, review gates, and notifications. Analysis nodes submit ordinary
  Harness Runs instead of creating a second agent runtime.

PostgreSQL is the transactional source of truth and initial event store. Redis provides ephemeral locks, rate limits, stream cursors, and short-lived state. S3-compatible storage holds large artifacts. OpenTelemetry exports traces and metrics. Kafka and ClickHouse are scale-out options, not required correctness dependencies. The administration SLO ledger (`GET /admin/slo`) is a tenant-scoped PostgreSQL projection of those durable rates and means; it does not invent histogram p95.

## Workbench, CLI, IDE, Desktop, and browser identity

The Workbench is one responsive shell: Workspace/Thread navigation on the left,
conversation in the center, and the persisted Runtime Plan/Steps/Events/Cost projection
on the right. Studio is a developer rail for Agent/Skill manifests; Eval is a
developer rail for Golden Datasets and Evaluation Runs. Neither is a conversation
Agent picker. The Experience CLI (`obsion-cli`), the VS Code extension
(`apps/ide-extension`), the IM adapter (`obsion-im`), and the Desktop client
(`obsion-desktop`)
are non-browser clients of the same App Server and REST application services. None of
them implements a model or tool loop; live Event delivery and REST reconciliation
both terminate at the same App Server/application boundary. IM senders resolve through
control-plane `(channel, sender_id)` bindings to a provisioned User; nicknames cannot
authorize. Feishu, DingTalk, and WeCom inbound envelopes are translated locally;
outbound replies default to vendor-shaped local-outbox envelopes. A loopback webhook
may bind `127.0.0.1` only. Feishu, DingTalk, and WeCom may POST through the
explicit `feishu-http` / `dingtalk-http` / `wecom-http` transports after Policy
authorization. Generic `--deliver http` remains fail-closed. Public `--public`
binds require TLS, Host allowlist, and channel security.

Browser login exchanges a development or OIDC access token for a random, revocable
opaque session. PostgreSQL stores only the session digest with organization/User,
expiry, and revocation state. An HttpOnly/SameSite cookie authenticates REST and the
WebSocket handshake; unsafe REST mutations also require an allowed Origin. Explicit
Bearer remains available to non-browser clients. This is one Principal resolver, not a
second Workbench-only identity system.

## Core lifecycle

```text
Workspace
  Thread (create, resume, fork, archive)
    Turn (one user input)
      Run (one execution attempt)
        ConversationSnapshot (bounded prior effective Turns)
        Step (one planned or executed unit)
          Event (append-only fact)
```

A Turn may own multiple Runs due to retry, continuation, model routing, or explicit replay. Current state is a projection of immutable events plus transactional lifecycle rows. State changes and event appends share one database transaction.

An ordinary Run freezes its effective prior conversation when the Turn and Run are
created. The snapshot follows fork lineage only through the persisted branch point,
excludes source Runs completed after capture, and is inspected or replayed without
re-resolving current Thread state. Previous conversation assists intent resolution but
never satisfies the Evidence requirement for a new factual claim.

Recurring execution adds a separate deterministic lifecycle:

```text
WorkflowDefinition -> immutable WorkflowVersion -> WorkflowSchedule
                                      |
                             AutomationExecution
                                      |
                  Analysis Run | Human Review | Notification
```

Schedulers use PostgreSQL row locks and idempotency constraints. They re-authorize the
accountable owner at fire time; no privileged scheduler identity bypasses workspace or
capability policy. A WORKFLOW capability with a connector `workflow_id` may also
create an `AutomationExecution` through the Capability Gateway (`trigger=CAPABILITY`).
That path reuses `AutomationService.trigger_workflow` and refuses dispatch from an
automation ANALYSIS child Run. It is not Temporal, Airflow, or a second orchestrator.

Governed change execution has its own lifecycle and never runs as an ordinary agent
tool call:

```text
ActionRequest -> immutable ActionPlan -> EXECUTE Approval -> ActionAttempt
                                      -> ROLLBACK Approval -> Compensating Attempt
```

The worker uses PostgreSQL row locks and leases, re-authorizes the accountable owner,
and pins capability/connector versions. Execute and rollback have separate approvals,
policy decisions, attempts, and stable provider idempotency keys.

## Run state machine

```text
PENDING -> RUNNING -> COMPLETED
                 |-> WAITING_APPROVAL -> RUNNING
                 |-> WAITING_USER -> RUNNING
                 |-> REPLANNING -> RUNNING
                 |-> FAILED
                 |-> CANCELLED
```

Transitions are validated by the domain model. Completion requires a final answer artifact or an explicit no-output result and a terminal `run.completed` event. Cancellation is cooperative and checked before every model or capability boundary.

Cancellation has one durable linearization point. The command locks the Run, writes
the request timestamp and terminal `CANCELLED` state, clears its lease, cancels every
active Step, emits request/terminal Events, and writes audit atomically. Scheduler and
completion paths lock Run before Step, so no dependency wave can start after that
commit. Work already beyond an external boundary may finish cooperatively and remain
cost-accounted, but its result cannot reopen the Run or publish an answer.

## Event protocol

Every event has an immutable ID, aggregate type and ID, monotonic aggregate sequence, run correlation ID, causation ID, actor, timestamp, schema version, classification, and JSON payload. Important event names include:

- `thread.created`, `thread.forked`, `thread.archived`, `thread.resumed`;
- `turn.created`, `run.started`, `run.state_changed`;
- `context.resolved`, `intent.detected`, `plan.created`, `plan.updated`;
- `capability.requested`, `policy.decided`, `approval.requested`;
- `tool.started`, `tool.completed`, `tool.failed`;
- `evidence.created`, `claim.created`, `critic.completed`;
- `answer.delta`, `artifact.created`, `run.completed`.

Aggregate events carry an aggregate-local `sequence`. Events associated with a Run
also carry an independent monotonic `run_sequence`, because the Run stream can include
facts whose primary aggregate is an Artifact or another resource. Streaming clients
resume from that Run cursor and deduplicate by immutable Event ID.

The App Server exposes the shared lifecycle through WebSocket and JSON-RPC 2.0. It
authenticates once per connection, durably deduplicates mutations by a
principal-scoped client request ID, and multiplexes bounded Run subscriptions. Event
notifications retain their domain names (`answer.delta`, `tool.started`,
`approval.requested`, and others). REST remains available for management and binary
Artifact transfer; SSE is a compatibility stream over the same Run cursor. Language
SDKs (Python, TypeScript, Java) are clients of this plane. The Java SDK speaks REST
only and is not a second backend. The Python Connector SPI (`health`/`discover`/`execute`)
is hosted in-process by the control plane and is not a package installer. The
transport delegates every authenticated operation to an application facade and is
statically forbidden from opening database sessions or importing persistence,
Harness, or Model Gateway implementations.

## Capability contract

A capability is transport-neutral and versioned. An organization-owned
`CapabilityDefinition` has immutable `CapabilityVersion` revisions; each active
revision is projected as a `CapabilityDescriptor` containing stable identity and
version, input/output JSON Schema, transport, risk level, side-effect classification,
permission action, timeout/limits, data classification, and an output mapping whose
kind is `Evidence`. Schema and Evidence mapping validation happens before a descriptor
is exposed. Implementations can use HTTP, gRPC, MCP, SDK, SQL proxy, another agent, or
a deterministic workflow, but the descriptor never embeds connector credentials or
execution code. MCP, SDK, gRPC, WORKFLOW, and AGENT are installed as in-process
adapters (JSON-RPC `tools/call`, `{sdk, method, arguments}`, `{service, method,
message}`, `{workflow, operation, input}`, and `{agent, operation, input}`). Process
spawn, pip installs, remote MCP/SDK URLs, remote gRPC channels, remote workflow
engines, and nested Harness agent loops fail closed.

Harness resolves an AgentSpec's declared capability IDs against the current
organization's active Registry (and the Principal's permission-visible set) before
planning. Only that intersection can become a Capability Step; an unregistered,
inactive, unauthorized, or undeclared capability is not selectable.

The execution path is fixed:

```text
resolve active binding -> identity -> policy/risk
        -> connector grant/schema validation -> approval/rate limit
        -> credential broker -> timeout-bounded connector
        -> output validation -> DLP/masking -> evidence normalization
        -> audit and telemetry -> result
```

No runtime plugin can bypass this gateway. Connector SDK plugins are scanned as
static declarations (network, filesystem, capabilities, secrets, risk) and HMAC-signed
before production load. Connectors receive short-lived execution credentials, not model-visible secrets.

Audit is written in the same transaction as the governed outcome. Capability records
carry the actor/run correlation, policy and approval references, descriptor risk,
connector resource, agent/model/capability version IDs, result classification, and
latency. Run completion and failure also emit canonical audit rows. Turn input is
redacted before persistence, so Replay and context snapshots never reintroduce raw
credential material.

The Gateway re-checks a pinned AgentVersion's declared capability IDs and risk budget
at execution time, so a persisted or model-produced plan cannot expand its authority.
Distributed rate limiting is fail-closed outside test environments. Connector errors
retain stable error codes, while tool events use the registered bounded failure
vocabulary.

The generic Capability Gateway rejects every side effect. The separate Action Gateway
is not exposed as a general invoke endpoint: it accepts only sealed governed-action
contracts in development/staging and requires an exact, unexpired action
approval. Production, configuration, restart, deployment, destructive, and
non-idempotent writes are hard-denied before provider invocation.

## Agent and skill contracts

Agents and skills are declarative, immutable by version, and promoted through environments. An AgentSpec declares model policy, budgets, skills, allowed capabilities, maximum risk, memory scopes, sandbox policy, and verification policy. A Skill declares the reasoning procedure, capability requirements, required evidence types, expected artifact contract, and verification rules.

GeneralAgent is the only primary conversational entry point. DataAgent, IncidentAgent, EngineeringAgent, KnowledgeAgent, AnalyticsAgent, OperationAgent, and SupportAgent are internal routes.

Sandbox `network` is `deny` or `gateway-only` (default). The normalized policy is
pinned on `run.plan.sandbox`. `deny` forbids Capability Gateway execution.
`gateway-only` still cannot `curl` arbitrary hosts: connectors execute only through
the Gateway with grants and egress allowlists. Declared CPU, memory, disk, and
process fields are stored; this control plane does not apply cgroups or start a
container. Mounts may only name `/workspace`, `/repo`, `/artifacts`, and `/tmp`.

## Model gateway

Agents select logical profiles such as `reasoning-high`, `coding-high`, `fast`, `private`, or `vision`, never provider model IDs. The current router evaluates tenant, sensitivity, region, provider family, context window, required chat/tool/JSON capability, availability, and call budget. Sensitive classifications can force an explicitly private profile and endpoint. Profile-scoped fallback preserves those eligibility constraints. Every provider attempt records effective profile, endpoint, redacted request fingerprint, token usage, latency, cost, and outcome; failed attempts are not rewritten as the later fallback result.

External content is always tagged as untrusted data. System policy, AgentSpec, and Skill instructions occupy distinct context segments that retrieval and tool output cannot override. Token Budget Manager records Keep / Compress / Summarize / Drop on the Run; summarize is extractive, not a nested model call. Older conversation is compacted extractively before that budget runs. Workspace
identity is pinned on the Run; workspace description stays untrusted. Capability
tool results occupy a separate untrusted context segment.

## Evidence and claims

Connector output and workspace attachments pass through the shared `EvidenceFabric`,
which normalizes source, resource, observed/ingestion time, content, deterministic
fingerprint, confidence, permissions, classification, and lineage after recursive
redaction. The answer layer emits atomic Claims linked many-to-many to Evidence;
transport-specific payloads never become a second result contract.

The Critic evaluates question coverage, evidence sufficiency, temporal consistency, metric definition consistency, conflicts, alternatives, query reliability, and unsupported statements. Critic output is recorded independently from the executing agent and may trigger bounded replanning.

## Data intelligence

Natural language never compiles directly to executable SQL. The pipeline is:

```text
understanding -> semantic resolution -> logical query plan
-> dialect compiler -> parsed SQL AST -> policy validation
-> query gateway -> read replica -> masked result -> evidence
```

The semantic catalog versions Metric, Dimension, Entity, Relation, BusinessRule, TimeDefinition, Synonym, DataSource, Table, and Column. SQL policy permits read-only statements, enforces limits and timeouts, computes scan budgets when available, applies row/column restrictions, and rejects multi-statements and unknown functions according to policy.

## Knowledge intelligence

Document ACLs and classifications propagate to chunks and retrieval indexes. Retrieval authorization happens before ranking. The pipeline versions the source, parser, extracted structure, chunks, metadata, ACL, embedding, and index state. Feishu, DingTalk, WeCom, and Confluence Cloud documents enter that pipeline through the `feishu-docs`, `dingtalk-docs`, `wecom-docs`, and `confluence` connectors and `knowledge.ingest` after Policy `knowledge.write`. Agents never receive vendor credentials. Answers cite Evidence records rather than opaque vector hits. A `KNOWLEDGE` route is internally pinned to the L1 `knowledge-agent` and its `knowledge-qa` Skill; unsupported questions produce an explicit unknown answer instead of a model-only claim.

Vendor REST ingest/sync uses the same Capability Gateway registry/binding, Policy,
grant, schema, rate, credential, executor, masking, telemetry, and Audit boundary via
a no-Run operator entry. It cannot emit Run Events/Evidence or create Approval without
a real Harness Run. Vendor source browsing uses the same no-Run entry through
versioned L1, side-effect-free `knowledge.source.containers` and
`knowledge.source.items` contracts. REST only projects the canonical container/item
result into existing vendor response shapes; browsing never creates Run Evidence.

L2 no-Run idempotent writes use a dedicated control-plane ledger, not Runtime Events.
The claim transaction commits `IN_PROGRESS` before credential/connector work; a
second transaction commits Knowledge changes, Audit, and the terminal result. Exact
retries reauthorize and replay without execution. Expired leases become UNKNOWN and
require connector-specific reconciliation. This ledger carries no raw request,
credential, Run, Event, or Evidence and therefore does not create a second Harness
trajectory.

## Incident intelligence

Observability sources normalize into events keyed by timestamp, service, environment, trace, request, masked business identifiers, deployment, commit, host, pod, and severity. Incident plans correlate metrics, dimensions, deployments, logs, traces, configuration, and code before verification.

## Extension model

Connectors, capabilities, agents, and skills carry manifests and versions. Production promotion requires schema validation, static/security scanning, declared network/filesystem/secrets requirements, a signature, approval, and registry publication. Runtime discovery never auto-installs untrusted code.
