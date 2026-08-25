# System design

## Architectural shape

Obsion begins as a modular control plane with durable domain boundaries. The runtime can later split high-throughput domains into services without changing public contracts.

```text
Web / IDE / CLI / API / IM adapters
                 |
          Obsion App Server
     REST management + event stream
                 |
  Harness Runtime Core / Automation / Actions
   context -> understand -> plan -> execute
      -> observe -> verify -> reflect -> respond
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

PostgreSQL is the transactional source of truth and initial event store. Redis provides ephemeral locks, rate limits, stream cursors, and short-lived state. S3-compatible storage holds large artifacts. OpenTelemetry exports traces and metrics. Kafka and ClickHouse are scale-out options, not required correctness dependencies.

## Core lifecycle

```text
Workspace
  Thread (create, resume, fork, archive)
    Turn (one user input)
      Run (one execution attempt)
        Step (one planned or executed unit)
          Event (append-only fact)
```

A Turn may own multiple Runs due to retry, continuation, model routing, or explicit replay. Current state is a projection of immutable events plus transactional lifecycle rows. State changes and event appends share one database transaction.

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
capability policy.

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

## Event protocol

Every event has an immutable ID, aggregate type and ID, monotonic aggregate sequence, run correlation ID, causation ID, actor, timestamp, schema version, classification, and JSON payload. Important event names include:

- `thread.created`, `thread.forked`, `thread.archived`;
- `turn.created`, `run.started`, `run.state_changed`;
- `context.resolved`, `intent.detected`, `plan.created`, `plan.updated`;
- `capability.requested`, `policy.decided`, `approval.requested`;
- `tool.started`, `tool.completed`, `tool.failed`;
- `evidence.created`, `claim.created`, `critic.completed`;
- `answer.delta`, `artifact.created`, `run.completed`.

Streaming clients resume from event sequence/cursor. WebSocket support can be added as a protocol adapter; Server-Sent Events is the initial browser stream because run events are primarily server-to-client and are naturally resumable.

## Capability contract

A capability is transport-neutral and versioned. Its descriptor contains identity, input/output JSON Schema, risk level, side-effect classification, permission action, timeouts, limits, data classification, and evidence mapping. Implementations can use HTTP, gRPC, MCP, SDK, SQL proxy, another agent, or a deterministic workflow.

The execution path is fixed:

```text
request -> schema validation -> identity -> policy -> risk
        -> approval if required -> credential broker -> connector
        -> output validation -> DLP/masking -> evidence normalization
        -> audit and telemetry -> result
```

No runtime plugin can bypass this gateway. Connectors receive short-lived execution credentials, not model-visible secrets.

The generic Capability Gateway rejects every side effect. The separate Action Gateway
is not exposed as a general invoke endpoint: it accepts only the sealed Phase 7
PR/ticket contracts in development/staging and requires an exact, unexpired action
approval. Production, configuration, restart, deployment, destructive, and
non-idempotent writes are hard-denied before provider invocation.

## Agent and skill contracts

Agents and skills are declarative, immutable by version, and promoted through environments. An AgentSpec declares model policy, budgets, skills, allowed capabilities, maximum risk, memory scopes, sandbox policy, and verification policy. A Skill declares the reasoning procedure, capability requirements, required evidence types, expected artifact contract, and verification rules.

GeneralAgent is the only primary conversational entry point. DataAgent, IncidentAgent, EngineeringAgent, KnowledgeAgent, AnalyticsAgent, OperationAgent, and SupportAgent are internal routes.

## Model gateway

Agents select logical profiles such as `reasoning-high`, `coding-high`, `fast`, `private`, or `vision`, never provider model IDs. Routing evaluates task type, sensitivity, region, context window, latency, tool use, availability, and budget. All calls record profile, resolved provider/model, redacted request fingerprint, token usage, latency, cost, and outcome.

External content is always tagged as untrusted data. System policy, AgentSpec, and Skill instructions occupy distinct context segments that retrieval and tool output cannot override.

## Evidence and claims

Connector output is normalized into Evidence with source, resource, observed time, ingestion time, content reference, confidence, permissions, classification, and lineage. The answer layer emits atomic Claims linked many-to-many to Evidence.

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

Document ACLs and classifications propagate to chunks and retrieval indexes. Retrieval authorization happens before ranking. The pipeline versions the source, parser, extracted structure, chunks, metadata, ACL, embedding, and index state. Answers cite Evidence records rather than opaque vector hits.

## Incident intelligence

Observability sources normalize into events keyed by timestamp, service, environment, trace, request, masked business identifiers, deployment, commit, host, pod, and severity. Incident plans correlate metrics, dimensions, deployments, logs, traces, configuration, and code before verification.

## Extension model

Connectors, capabilities, agents, and skills carry manifests and versions. Production promotion requires schema validation, static/security scanning, declared network/filesystem/secrets requirements, a signature, approval, and registry publication. Runtime discovery never auto-installs untrusted code.
