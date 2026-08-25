# Implementation blueprint

## Why a modular control plane

The correctness boundary spans lifecycle state, event append, authorization,
approval, evidence, and audit. Keeping these modules in one Python deployment at V1
allows their durable writes to share a PostgreSQL transaction while interfaces remain
explicit enough to extract under measured load. It also satisfies the project
requirement to prefer Python and avoids a second backend stack.

Modules communicate through application services and typed contracts. They do not
read another module's tables. High-volume event and telemetry projections may move to
Kafka and ClickHouse later without changing the source-of-truth contract.

## Module map

```text
api
  -> identity context
  -> workspace application service
  -> harness application service
  -> automation and action application services
  -> administration services

harness
  -> agent router -> planner -> bounded executor -> critic -> responder
  -> model_gateway (logical profile only)
  -> capability_gateway (the only external execution boundary)
  -> event_store (transactional trajectory)

capability_gateway
  -> registry -> schema validation -> policy -> approval
  -> credential broker -> connector runtime -> DLP/masking
  -> evidence -> audit -> telemetry

actions
  -> preflight -> immutable plan -> independent approval -> durable worker
  -> action_gateway -> pinned idempotent provider -> compensation -> audit

intelligence
  -> knowledge: ingest -> ACL -> chunk -> retrieve -> rerank -> evidence
  -> data: understand -> semantics -> logical plan -> SQL AST -> query -> evidence
  -> incident: normalize -> correlate -> verify -> evidence
```

## Dependency rules

1. Domain packages depend only on standard library and shared contracts.
2. Application services depend on domain interfaces, never concrete infrastructure.
3. Infrastructure implements repositories, connectors, model providers, storage, and
   telemetry ports.
4. API routers perform transport validation and delegate; policy cannot live only in
   routers because background runs use the same application services.
5. Agents and skills declare capability IDs. Importing connector code from either is
   an architecture-test failure.
6. Events and audit records are outputs of use cases, not best-effort logging.

## Persistence and transactions

PostgreSQL is authoritative. Tenant-owned rows carry `organization_id`; repositories
require it in every read and write method. Lifecycle mutation, aggregate event append,
and outbox append use one transaction. Events use a unique `(aggregate_type,
aggregate_id, sequence)` key and optimistic aggregate versions.

Append-only tables reject update and delete through application code and production
database grants. User-editable resources use version rows and soft deletion. Large
artifact bodies live in S3-compatible storage while checksums, classification, ACL,
and lineage remain transactional metadata.

## Harness execution

The executor is durable and bounded. Each external boundary checks cancellation,
deadline, step count, token budget, monetary budget, and recursion depth. Independent
DAG nodes may execute concurrently. Retries are allowed only for declared transient,
idempotent operations. V1 read-only capability steps receive at most one recovery
attempt; the runtime enters `REPLANNING`, records `plan.updated`, restores affected
dependent nodes, and charges every attempt against the pinned step budget. Policy
denials and other deterministic failures are never retried.

Run steps and events are persisted before and after each boundary. Waiting for an
approval or user input releases the worker; resume acquires a lease and validates a
single-use hashed resume token. Replay pins recorded agent, skill, model profile,
capability versions, inputs, and evidence snapshots, and distinguishes replay from a
fresh rerun.

A replay never re-enters the Capability or Model Gateway. The worker atomically
materializes the terminal source Run's steps, version IDs, evidence fingerprints,
Claims, Claim-Evidence links, artifacts, usage, and safe event envelopes under new
resource IDs. The replay records one stable SHA-256 fingerprint over the source
snapshot, preserves source observation timestamps, and exposes replay-specific events
so an inspector cannot confuse historical playback with a new external invocation.
Repeating a replay of the same immutable source produces the same snapshot fingerprint.

## Model execution

Agent specifications refer to profiles such as `reasoning-high`, `fast`, `private`,
or `coding-high`. The router selects only enabled endpoints compatible with data
classification, region, context, tool support, latency, and budget. Provider responses
are schema-validated and usage is recorded. External data occupies an explicitly
untrusted context segment and cannot become system or skill instructions.

No provider is required to boot the control plane. A run that needs a model and has no
eligible endpoint becomes a typed, recoverable configuration failure; it never falls
back to fabricated content.

## Evaluation execution

Golden Dataset cases declare an explicit routing, SQL-policy, or recorded-Run
evaluator. Evaluation Runs fingerprint the immutable case set and snapshot the Agent,
resolved registry dependencies, model routing metadata, application revision, real
terminal Run bindings, and gate configuration. Results are immutable per-case records
with safe observations and Evidence references. A baseline comparison is valid only
for the exact same dataset fingerprint; release gates combine pass rate, case errors,
regression rate, and named quality-score thresholds. The detailed contract is in the
[evaluation architecture](evaluation-design.md).

## Policy semantics

Policy input contains principal, organization, roles, agent/version, capability,
action, resource attributes, environment, data classification, time, and risk.
Explicit deny wins. The generic capability policy denies side effects and L3-L5;
`MASK` supplies enforceable obligations and `ASK` creates a durable approval only for
otherwise permitted L2 reads.

The Action Gateway has a separate, closed policy entry point. It admits only pinned
L3 `IDEMPOTENT_WRITE` PR/ticket capabilities in development/staging after an
independent approval for the exact immutable plan checksum. It still applies explicit
deny, current owner authorization, connector grants, rate limiting, credential
brokering, schema validation, egress control, redaction, audit, and telemetry.
Production actions, database writes, deployment, restart, configuration mutation,
destructive writes, and L4-L5 remain immutable denials.

## User interface logic

The Workbench keeps the prompt and answer central, like mature conversational tools,
but makes enterprise execution visible:

- the left rail selects workspaces, threads, files, reports, data, and administration;
- the center presents conversation and rich artifacts with a persistent composer;
- the right inspector presents plan, live runtime events, evidence, tool activity,
  costs, memory, and audit information;
- a conclusion opens its evidence without navigating away;
- responsive layouts collapse navigation and inspector into accessible drawers;
- keyboard navigation, focus states, reduced motion, contrast, and screen-reader live
  regions are first-class requirements.

The interface never asks ordinary users to select specialist agents. It may expose
which internal route was used after execution for transparency.

## Failure behavior

Failures are typed as validation, authentication, authorization, approval, budget,
model, capability, connector, timeout, cancellation, conflict, or internal errors.
Public responses contain a correlation ID and safe remediation. Secret-bearing input
is redacted before structured logging. Partial evidence remains inspectable, while
failed or unverified claims are never presented as verified conclusions.
