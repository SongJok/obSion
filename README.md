# Obsion

Obsion is an open-source **Enterprise Agent Runtime and Intelligence Workspace**.
It gives employees one assistant for governed work across enterprise knowledge, data,
code, observability, and runtime systems while preserving the evidence, policy
decision, approval, and replayable trajectory behind every result.

The name combines **OBServability + Intelligence + OrchestratiON**.

Obsion is not a chat wrapper, an unrestricted tool runner, or a prompt-only Text-to-SQL
application. Its durable architecture centers on five assets:

- **Workspace and Harness** — persistent Thread, Turn, Run, Step, Event, memory, and
  artifact lifecycles.
- **Capability Fabric** — versioned, transport-neutral access to internal handlers,
  HTTP APIs, MCP, SDKs, workflows, agents, and read-only SQL proxies.
- **Evidence Fabric** — normalized evidence, atomic claims, provenance, confidence,
  conflicts, and independent critic verification.
- **Semantic Layer** — governed metrics, dimensions, entities, relations, business
  rules, logical plans, dialect compilation, and SQL AST validation.
- **Enterprise Control Plane** — identity, policy, risk, approval, credentials,
  masking, rate limits, egress control, governed actions, audit, and evaluations.

## What is implemented

The first-generation boundary covers the foundation and three complete intelligence
paths:

- governed document ingestion and ACL-before-ranking PostgreSQL full-text/pgvector
  retrieval, hybrid reranking, citations, and versioned originals;
- semantic query understanding, logical planning, bounded read-only SQL, masking,
  lineage, reusable table/chart/SQL artifacts, and tenant-scoped metric definition
  and source-to-table-to-metric inspection in the Workbench and SDKs;
- concurrent incident investigation plans across configured metric, log, trace,
  deployment, configuration, Kubernetes, and code capabilities;
- durable create/resume/fork/archive/replay/cancel lifecycles with ordered events,
  audits, fork-induced source read-only semantics, frozen fork-point history,
  one-Turn/multiple-Run replay, bounded immutable conversation context,
  Python/TypeScript SDKs, a unified WebSocket/JSON-RPC App Server with durable
  mutation idempotency and cross-aggregate Run cursors, resumable SSE compatibility,
  and responsive Workbench controls;
- policy effects `ALLOW`, `MASK`, `ASK`, and `DENY`, durable approvals, secret
  brokering, distributed rate limiting, and immutable audit records;
- model-independent profiles, normalized completion/JSON/tool-call contracts,
  sensitive-data private routing, per-attempt token/cost accounting, and
  OpenAI-compatible provider adapters without fabricated fallback answers;
- governed four-scope memory with policy decisions, bounded retention, authorized
  context budgets, immutable Run snapshots and replay inspection; evidence-producing,
  version-pinned evaluation gates with immutable case results and baseline regression
  comparison;
- immutable deterministic workflows with manual and cron/IANA-timezone triggers,
  durable execution leases, concurrency policy, human review gates, recurring Harness
  analysis, and recipient-scoped notifications;
- governed PR and ticket actions in development/staging with immutable preflight
  plans, independent execute/rollback approvals, provider idempotency, durable leases,
  compensating actions, notifications, policy decisions, and audit evidence;
- workspace collaboration with versioned task state transitions, optional Run
  provenance, immutable checksummed decision revisions, acceptance/rejection,
  explicit supersession lineage, events, audits, SDKs, and responsive Workbench UI;
- governed terminal-Run feedback with redacted reasons, optimistic revisions,
  ordered Run events, tenant-scoped satisfaction reporting, SDKs, and real
  copy/playback/rating controls;
- responsive Workbench, runtime/evidence inspector, workspace artifact center with
  governed upload/download and report/table/chart/code/SQL previews, knowledge/data
  views, selectable existing-artifact context, direct Claim-to-Evidence navigation,
  governed-action center, and administration console.

Conversational agents and the generic Capability Gateway remain read-only at risk
levels L0-L2. A separate Action Gateway opens only the L3, idempotent PR and ticket
contracts in development/staging after immutable preflight and independent approval.
Production actions, database writes, deployments, restarts, configuration changes,
non-idempotent writes, and L4-L5 operations remain hard-denied by the server.

## Architecture

```text
Workbench / IDE / CLI / SDK / API
                 │
 Python App Server · Automation Scheduler · Action Worker
  Thread · Turn · Run · Action · Approval · Artifact
                 │
 Harness Runtime · Deterministic Workflow Engine
  Frozen Conversation + Memory + Evidence → Understand → Plan → Execute → Verify → Respond
                 │
 Agent Registry · Skill Registry · Model Gateway · Memory
                 │
 Capability Gateway (reads) · Action Gateway (closed writes)
 AuthN · Policy · Risk · Approval · Rate · Secret · Mask · Audit
                 │
 Code · Data · Logs · Metrics · Knowledge · Runtime systems
```

PostgreSQL with pgvector is the transactional source of truth, event store, authorized
full-text index, and HNSW vector index. Redis coordinates
distributed capability rate limits. S3-compatible storage is provisioned for large
artifacts. OpenTelemetry exports runtime, capability, model, policy, HTTP, and database
traces plus operational counters.

## Repository layout

```text
apps/web/                 Next.js Workbench and administration UI
services/control-plane/   Python App Server, Harness, gateways, and domain services
packages/                 Python and TypeScript SDKs
agents/                   Live declarative AgentSpec definitions
skills/                   Live governed Skill definitions
connectors/               Connector contracts and deployment manifests
evaluations/              Version-controlled evaluation datasets
deploy/                   Container, Compose, Helm, and Kubernetes assets
docs/                     Product, architecture, security, operations, API, and ADRs
```

The source blueprint is traced to verifiable commitments in
[requirements traceability](docs/product/requirements-traceability.md). Architectural
details live in [system design](docs/architecture/system-design.md) and the
[implementation blueprint](docs/architecture/implementation-blueprint.md). The closed
write boundary and provider contract are specified in the
[Action Agent architecture](docs/architecture/action-agent-design.md), and governed
context retention is specified in the [memory architecture](docs/architecture/memory-design.md).
Task and decision invariants are specified in the
[workspace collaboration architecture](docs/architecture/workspace-collaboration-design.md).
Run feedback and the durable satisfaction projection are specified in the
[run-feedback architecture](docs/architecture/run-feedback-design.md).
The single-entry Workbench, revocable browser session, Runtime timeline, and responsive
shell contract are specified in the
[Phase 5 Workbench gate](docs/architecture/phase-5-workbench-gate.md).

## Local development

Install Python 3.12, [uv](https://docs.astral.sh/uv/), Node.js 22, npm, Docker, and
Docker Compose. Then:

```bash
cp .env.example .env
make bootstrap
make compose-up
make migrate
make dev-api
```

Run `make dev-web` in a second terminal. Open <http://localhost:3000>; development API
documentation is at <http://localhost:8080/api/docs>. Development mode seeds one local
organization, administrator, capability catalog, model profiles, agents, skills, and
the internal knowledge connector. Paste the local-only `OBSION_DEV_BEARER_TOKEN` from
`.env` into the Workbench login page to create a revocable browser session. REST and App
Server SDK clients may continue to send it as an explicit Bearer; development mode no
longer treats an absent credential as the seeded administrator.

To build and run the complete containerized stack instead, use:

```bash
cp .env.example .env
make stack-up
```

The credentials in `.env.example` are public local-only defaults. Replace the
development bearer value for any shared environment. Production mode refuses
development authentication.

## Quality gates

```bash
make check
```

This runs Ruff lint and format checks, strict mypy, frozen contract validation, Python
unit/integration/end-to-end tests, ESLint, TypeScript checks, package tests, and Alembic
drift detection. CI additionally verifies the audit-table rename round trip in a
disposable PostgreSQL 17 database and builds both production containers. Event and error
contracts can be validated with `uv run obsion validate-contracts`, declarative registry
files with `uv run obsion validate-registry`, Golden Datasets with
`uv run obsion validate-evaluations`, and the OpenAPI contract regenerated with
`uv run obsion openapi`.

## Production deployment

Compose is suitable for local evaluation and single-host development. Kubernetes
deployments use the [Obsion Helm chart](deploy/helm/obsion/README.md), which includes
non-root workloads, read-only filesystems, probes, disruption budgets, network-policy
defaults, and an idempotent pre-upgrade migration Job.

Before production, configure OIDC, TLS, PostgreSQL backups, Redis persistence, object
storage lifecycle, an OTLP collector, external secrets, a read-only query identity,
connector egress allowlists, and tenant-scoped policies. See the
[operator runbook](docs/operators/runbook.md) and [security model](docs/security/security-model.md).

## APIs and SDKs

The management contract is REST under `/api/v1`; the unified bidirectional client
contract is WebSocket/JSON-RPC at `/api/v1/app-server`, and Run events also remain
available as resumable Server-Sent Events. The generated REST contract plus protocol,
authentication, error, and retry conventions are documented in
[API documentation](docs/api/README.md). Async Python and browser-safe TypeScript
clients live under `packages/`.

## Community

Obsion is licensed under [Apache License 2.0](LICENSE). Please read
[CONTRIBUTING.md](CONTRIBUTING.md), [GOVERNANCE.md](GOVERNANCE.md), the
[Code of Conduct](CODE_OF_CONDUCT.md), and [SECURITY.md](SECURITY.md) before
contributing or reporting a vulnerability.
