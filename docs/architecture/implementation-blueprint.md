# Implementation blueprint

## Why a modular control plane

The correctness boundary spans lifecycle state, event append, authorization,
approval, evidence, and audit. Keeping these modules in one Python deployment at V1
allows their durable writes to share a PostgreSQL transaction while interfaces remain
explicit enough to extract under measured load. It also satisfies the project
requirement to prefer Python and avoids a second backend stack. Java, Python, and
TypeScript SDKs are clients of this plane; they do not host Harness or Policy.

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
  -> registry (active, tenant-scoped, AgentSpec-filtered descriptors)
  -> capability_gateway (the only external execution boundary)
  -> event_store (transactional trajectory)

capability_gateway
  -> registry -> schema validation -> policy -> approval
  -> connector grants -> rate limit -> credential broker -> timeout-bounded connector
  -> INTERNAL (including Connector SDK SPI execute, plugin scan/HMAC before load) | HTTP | MCP (in-process JSON-RPC) | SDK (in-process envelope) | GRPC (in-process unary) | WORKFLOW (in-process envelope to AutomationService) | AGENT (in-process envelope) | SQL_PROXY executors
  -> circuit breaker -> DLP/masking -> EvidenceFabric normalization
  -> evidence -> Claim linkage -> audit -> telemetry

actions
  -> preflight -> immutable plan -> independent approval -> durable worker
  -> action_gateway -> pinned idempotent provider -> compensation -> audit

intelligence
  -> knowledge: ingest -> ACL -> chunk -> retrieve -> rerank -> evidence
  -> data: understand -> semantics -> logical plan -> SQL AST -> query -> evidence
  -> code: static parse -> ACL -> snapshot -> symbol/call graph -> evidence
  -> incident: normalize -> fuse -> rank candidates -> verify -> evidence
```

The internal Knowledge route resolves the active `knowledge-agent` and pinned
`knowledge-qa` Skill before planning. The Skill is limited to Knowledge capabilities,
requires DOCUMENT Evidence, renders citations from substantive Claim links, and emits an
explicit unknown answer when authorized retrieval has no supporting source.

Internal specialist routing also pins `analytics-agent`, `operation-agent`, and
`support-agent` without a user-facing agent picker. Support diagnosis searches
ACL-filtered tickets (`source=ticket`) and knowledge through the same INTERNAL index;
it cannot create tickets or write to production. Operations stay on read-only
status, configuration, log, and metric capabilities. Event v1 `intent.detected` and
`plan.created` route enums add `ANALYTICS`, `SUPPORT`, and `OPERATION` without a
breaking version bump.

The semantic catalog is also an inspectable product surface. Validated metrics expose
their complete versioned definition and a tenant-scoped, read-only lineage chain from
data source to table to metric through the API, both SDKs, and the responsive
Workbench. Inspection never opens a connector session or bypasses query policy.

Incident evidence fusion is deterministic and read-only. It produces at most three
ranked candidate root causes from the current Run's normalized Evidence. A root-cause
Claim must link two distinct Evidence types; unresolved conflicts remain attached to
the answer Artifact and downgrade verification rather than becoming a causal fact.

The internal Engineering route resolves `engineering-agent` and the pinned
`code-architecture` Skill. Source is ingested into an immutable Code Graph snapshot
through static parsers that never execute repository files. Repository ACLs are
applied before symbol ranking. `code.symbol`, `code.reference`, `code.callers`, and
`code.callees` are INTERNAL capabilities bound to `obsion-code-index`. Missing
authorized CODE Evidence yields an explicit unknown answer; citations name repository,
path, symbol, and commit.

## Dependency rules

1. Domain packages depend only on standard library and shared contracts.
2. Application services depend on domain interfaces, never concrete infrastructure.
3. Infrastructure implements repositories, connectors, model providers, storage, and
   telemetry ports.
4. API routers perform transport validation and delegate; policy cannot live only in
   routers because background runs use the same application services.
5. Agents and skills declare capability IDs. Harness resolves those IDs against the
   active tenant Registry before planning; importing connector code from either is an
   architecture-test failure. Agent sandbox is pinned on the Run plan and re-checked
   at the Capability Gateway; `network: deny` yields no executable capabilities.
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

Thread create, archive, resume, and fork mutations commit their aggregate event,
outbox message, and redacted audit record in the same transaction. A fork records its
parent Thread and exact source Turn without rewriting either history. Its effective
history is a fixed read projection of the parent trajectory through that source Turn,
followed by local Turns; later parent Turns are excluded, local ordinals continue from
the inherited prefix, and nested forks resolve the same bounded lineage. Archived
Threads remain inspectable but reject new Turns until explicitly resumed; a Thread
with a non-terminal Run cannot be archived.

Creating an ordinary Turn also captures the effective prior conversation in the same
transaction as its Run. Capture works newest-first under configured Turn, total
character, and per-message limits, then persists the selected rows chronologically.
Only answers from Runs completed before capture are eligible. The Harness sends the
current principal's earlier input as user history, treats other collaborators' input
as untrusted data, propagates the highest snapshot classification to model routing,
and continues to require current-Run Evidence for every factual Claim. Replay clones
these rows and includes them in the deterministic snapshot fingerprint instead of
querying live Thread history.

Workspace collaboration uses optimistic versions at the API and row locks in the
service. PostgreSQL independently enforces task status/version rules, decision
disposition and supersession rules, immutable decision revisions, and the absence of
direct deletion. Each accepted mutation commits its aggregate event, outbox message,
and audit record atomically.

Run satisfaction follows the same durable boundary. A principal can record one
redacted rating per terminal Run, revisions require the exact current version, and a
Run row lock serializes concurrent first submissions. PostgreSQL rejects identity
changes, skipped versions, and deletion. Feedback mutation extends the existing Run
aggregate sequence and commits its outbox and audit evidence atomically; the admin
rate is projected from current records rather than submission-event volume.

## Harness execution

Ordinary Runs persist the Harness loop as first-class RunSteps:
`OBSERVE -> UNDERSTAND -> PLAN -> CAPABILITY* -> VERIFY -> REFLECT -> RESPOND`. The Act phase is
empty for non-factual conversation and is otherwise represented only by Capability
Gateway requests. Missing Capability bindings, policy denials, schema failures, and
connector failures terminate the evidence path explicitly; they cannot be masked by a
model-written answer.

The executor is durable and bounded. Each external boundary checks cancellation,
deadline, step count, token budget, monetary budget, and recursion depth. Independent
DAG nodes may execute concurrently. Retries are allowed only for declared transient,
idempotent operations. V1 read-only capability steps receive at most one recovery
attempt; the runtime enters `REPLANNING`, records `plan.updated`, restores affected
dependent nodes, and charges every attempt against the pinned step budget. Policy
denials and other deterministic failures are never retried. After the capability wave,
the deterministic Critic may append at most one additional wave of unused, Agent-
authorized, read-only capabilities for missing required Evidence types. Git operations
produce `GIT` Evidence; query results remain `DATA` and also satisfy a `SQL`
requirement. The critic-replan bound is pinned on the Run (`run_max_critic_replans`)
so a persistent gap cannot recurse.

Run steps and events are persisted before and after each boundary. Before planning, the
runtime resolves only currently authorized, approved, unexpired TURN, SESSION,
WORKSPACE, and USER_PREFERENCE memories. It applies item and character budgets,
captures immutable `RunMemorySnapshot` rows, and supplies them to the model as
untrusted data rather than instructions. Memory cannot substantiate a factual Claim
without Evidence.

Waiting for an
approval or user input releases the worker; resume acquires a lease and validates a
single-use hashed resume token. Replay pins recorded agent, skill, model profile,
capability versions, inputs, memory snapshots, and evidence snapshots, and
distinguishes replay from a fresh rerun.

A replay never re-enters the Capability or Model Gateway. The worker atomically
materializes the terminal source Run's steps, version IDs, memory/evidence
fingerprints, Claims, Claim-Evidence links, artifacts, usage, and safe event envelopes
under new resource IDs. The replay records one stable SHA-256 fingerprint over the
source snapshot, preserves source observation and memory-capture timestamps, and
exposes replay-specific events so an inspector cannot confuse historical playback
with a new external invocation.
Repeating a replay of the same immutable source produces the same snapshot fingerprint.

## Model execution

Agent specifications refer to profiles such as `reasoning-high`, `fast`, `private`,
or `coding-high`. The router selects only enabled endpoints compatible with data
classification, region, provider family, context, chat/tool/JSON support, private
deployment policy, and budget. `CONFIDENTIAL` and `RESTRICTED` inputs force the
configured private profile by default and fail before provider access when no honest
private route exists. Provider responses and normalized tool arguments are
schema-validated, every attempt records usage/cost, and fallback stays within the
selected logical profile. External data occupies an explicitly untrusted context
segment and cannot become system or skill instructions. Context Builder records
Keep / Compress / Summarize / Drop against the character budget. Summarize is
extractive identity or head/tail text, never a nested model call. The ledger is
pinned on `runs.context_budget`. Older conversation is compacted extractively
(`runs.conversation_compact`) before that budget runs; recent turns stay verbatim.
Workspace identity is an AGENT segment; workspace description is untrusted and
pinned on `runs.workspace_context`. Capability TOOL evidence is a sibling
`tool-result` untrusted segment, not mixed into `evidence-bus`.
Admin `GET /admin/slo` projects success, replan, approval, satisfaction, coverage,
tokens, cost, and mean latencies from PostgreSQL. TTFT stays an OTel histogram
and is not presented as p95. Workspace Files reuse the Artifact store with an
optional governed path and version; they do not become SYSTEM text automatically.
Workspace Reports are published `REPORT` artifacts from cited or evidenced
answers; greetings do not create them. Workspace Dashboards are published
`DASHBOARD` artifacts that only reference existing CHART/TABLE/SQL rows; they
do not invent series. Workspace SQL lists published `SQL` artifacts and does
not invent warehouse rows. Workspace Evidence lists persisted `Evidence` rows
joined through Run → Turn → Thread and does not invent citations. Workspace
Timeline lists persisted Run Events the same way and does not invent Harness
steps.

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

- the login page exchanges an access token once for a revocable opaque HttpOnly
  session; browser code never persists or forwards the bearer, and REST/WebSocket use
  the same provisioned Principal;
- `obsion-cli` is a non-browser Experience client of the same App Server and REST
  services; it never implements Observe/Understand/Plan/Execute locally;
- `apps/ide-extension` is the VS Code Experience client of the same protocol; only
  `extension.ts` imports `vscode`, and the runtime never implements Harness;
- `obsion-im` is the IM Experience client of the same protocol; it translates
  documented inbound envelopes, may listen on `127.0.0.1`, renders vendor-shaped
  local-outbox replies, may deliver Feishu/DingTalk/WeCom replies through the
  explicit `*-http` transports after Policy authorization, resolves senders through
  control-plane principal mapping, and does not accept generic `--deliver http`;
- `obsion-desktop` is the Desktop Experience client of the same protocol; only
  `electron-main.ts` may import Electron, the loopback UI binds `127.0.0.1`, and the
  runtime never implements Harness;
- the left rail selects workspaces, threads, files, reports, data, Studio, Eval, and administration;
- the task-and-decision view keeps actionable follow-up beside immutable team
  rationale, version history, and replacement lineage;
- the center presents conversation and rich artifacts with a persistent composer;
- terminal responses expose working copy, deterministic playback, and versioned
  satisfaction controls with an improvement-reason form;
- the composer can select readable artifacts already in the workspace; selection
  reuses the same access check, parser, redaction, Evidence normalization, immutable
  Turn reference, and replay path as a newly uploaded attachment;
- the right inspector presents plan, live runtime events, evidence, tool activity,
  costs, memory, and audit information;
- a conclusion opens its evidence without navigating away;
- responsive layouts collapse navigation and inspector into accessible, dismissible
  drawers without page-level horizontal scrolling;
- keyboard navigation, focus states, reduced motion, contrast, and screen-reader live
  regions are first-class requirements.

The interface never asks ordinary users to select specialist agents. Studio is a
developer registry workbench. Eval is a Golden Dataset console over the existing
evaluation engine. Neither appears in the composer. The
shell may expose which internal route was used after execution for transparency.

## Failure behavior

Failures are typed as validation, authentication, authorization, approval, budget,
model, capability, connector, timeout, cancellation, conflict, or internal errors.
Public responses contain a correlation ID and safe remediation. Secret-bearing input
is redacted before structured logging. Partial evidence remains inspectable, while
failed or unverified claims are never presented as verified conclusions.
