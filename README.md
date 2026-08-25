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
  lineage, and reusable table/chart/SQL artifacts;
- concurrent incident investigation plans across configured metric, log, trace,
  deployment, configuration, Kubernetes, and code capabilities;
- durable create/resume/fork/archive/replay/cancel lifecycles with resumable SSE;
- policy effects `ALLOW`, `MASK`, `ASK`, and `DENY`, durable approvals, secret
  brokering, distributed rate limiting, and immutable audit records;
- model-independent profiles and OpenAI-compatible provider routing without fabricated
  fallback answers;
- governed memory candidates and evidence-producing, version-pinned evaluation gates
  with immutable case results and baseline regression comparison;
- immutable deterministic workflows with manual and cron/IANA-timezone triggers,
  durable execution leases, concurrency policy, human review gates, recurring Harness
  analysis, and recipient-scoped notifications;
- governed PR and ticket actions in development/staging with immutable preflight
  plans, independent execute/rollback approvals, provider idempotency, durable leases,
  compensating actions, notifications, policy decisions, and audit evidence;
- responsive Workbench, runtime/evidence inspector, workspace artifact center with
  governed upload/download and report/table/chart/code/SQL previews, knowledge/data
  views, governed-action center, and administration console.

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
  Context → Understand → Plan → Execute → Verify → Respond
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
[Action Agent architecture](docs/architecture/action-agent-design.md).

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
the internal knowledge connector.

To build and run the complete containerized stack instead, use:

```bash
cp .env.example .env
make stack-up
```

The credentials in `.env.example` are local-only. Production mode refuses development
authentication.

## Quality gates

```bash
make check
```

This runs Ruff, strict mypy, Python unit/integration/end-to-end tests, ESLint,
TypeScript checks, package tests, and Alembic drift detection. CI additionally builds
both production containers against a real PostgreSQL migration job. Declarative
registry files can be validated with `uv run obsion validate-registry`, Golden
Datasets with `uv run obsion validate-evaluations`, and the OpenAPI contract regenerated
with `uv run obsion openapi`.

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

The management contract is REST under `/api/v1`; run events are available as resumable
Server-Sent Events. The generated contract and authentication/error conventions are
documented in [API documentation](docs/api/README.md). Async Python and browser-safe
TypeScript clients live under `packages/`.

## Community

Obsion is licensed under [Apache License 2.0](LICENSE). Please read
[CONTRIBUTING.md](CONTRIBUTING.md), [GOVERNANCE.md](GOVERNANCE.md), the
[Code of Conduct](CODE_OF_CONDUCT.md), and [SECURITY.md](SECURITY.md) before
contributing or reporting a vulnerability.
