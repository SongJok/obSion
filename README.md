# Obsion

Obsion is an open-source **Enterprise Agent Runtime and Intelligence Workspace**.
It gives employees one assistant for governed work across enterprise knowledge, data,
code, observability, and runtime systems while preserving the evidence, policy
decision, approval, and replayable trajectory behind every result.

The name combines **OBServability + Intelligence + OrchestratiON**.

Current release status: **`0.80.0-alpha.1` repository candidate**. Its machine
contract, migration lineage, verification procedure, and operator-owned limits are in
the [Alpha.1 release notes](docs/release/0.80.0-alpha.1.md). No external tag, package,
image, signature, or production approval is implied.

Obsion is not a chat wrapper, an unrestricted tool runner, or a prompt-only Text-to-SQL
application. Its durable architecture centers on five assets:

- **Workspace and Harness** — persistent Thread, Turn, Run, Step, Event, memory, and
  artifact lifecycles.
- **Capability Fabric** — versioned, transport-neutral access to internal handlers,
  HTTP APIs, in-process MCP, SDK, gRPC, WORKFLOW, and AGENT adapters, and read-only SQL proxies.
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
- a static, ACL-filtered Code Graph for authorized repositories: immutable snapshots,
  Python AST plus conservative Java/TypeScript symbol extraction, callers/callees,
  SQL table references, CODE Evidence citations, and an explicit unknown answer when
  the current principal cannot recall matching symbols;
- semantic query understanding, logical planning, bounded read-only SQL, masking,
  lineage, reusable table/chart/SQL artifacts, and tenant-scoped metric definition
  and source-to-table-to-metric inspection in the Workbench and SDKs;
- concurrent incident investigation plans across configured metric, log, trace,
  deployment, configuration, Kubernetes, and code capabilities;
- durable create/resume/fork/archive/replay/cancel lifecycles with ordered events,
  audits, fork-induced source read-only semantics, frozen fork-point history,
  one-Turn/multiple-Run replay, bounded immutable conversation context,
  Python/TypeScript/Java SDKs, a unified WebSocket/JSON-RPC App Server with durable
  mutation idempotency and cross-aggregate Run cursors, resumable SSE compatibility,
  and responsive Workbench controls;
- policy effects `ALLOW`, `MASK`, `ASK`, and `DENY`, durable approvals, secret
  brokering, distributed rate limiting, and immutable audit records;
- model-independent profiles, normalized completion/JSON/tool-call contracts,
  sensitive-data private routing, per-attempt token/cost accounting, and
  OpenAI-compatible provider adapters without fabricated fallback answers;
- governed four-scope memory with policy decisions, bounded retention, authorized
  context budgets, inspect/edit/delete (revoke) lifecycle, immutable Run snapshots
  and replay inspection; evidence-producing, version-pinned evaluation gates with
  immutable case results and baseline regression comparison;
- immutable deterministic workflows with manual and cron/IANA-timezone triggers,
  durable execution leases, concurrency policy, human review gates, recurring Harness
  analysis, and recipient-scoped notifications;
- governed PR and ticket actions in development/staging with immutable preflight
  plans, independent execute/rollback approvals, provider idempotency, durable leases,
  compensating actions, notifications, policy decisions, and audit evidence;
- MCP, SDK, gRPC, WORKFLOW, and AGENT as in-process Capability Gateway transports
  (JSON-RPC `tools/call`, `{sdk, method, arguments}`, `{service, method, message}`,
  `{workflow, operation, input}`, and `{agent, operation, input}`); a WORKFLOW
  connector `workflow_id` dispatches to `AutomationService.trigger_workflow` once;
  remote servers, stdio spawn, pip/importlib installs, gRPC channels, Temporal/Airflow,
  and nested Harness loops fail closed and are not faked;
- Agent sandbox pinned on the Run plan (`network: deny | gateway-only`); capabilities
  execute only through the Gateway, and this process does not start a container runtime;
- workspace collaboration with versioned task state transitions, optional Run
  provenance, immutable checksummed decision revisions, acceptance/rejection,
  explicit supersession lineage, events, audits, SDKs, and responsive Workbench UI;
- governed terminal-Run feedback with redacted reasons, optimistic revisions,
  ordered Run events, tenant-scoped satisfaction reporting, SDKs, and real
  copy/playback/rating controls;
- responsive Workbench, runtime/evidence inspector, workspace artifact center with
  governed upload/download and report/table/chart/code/SQL previews, knowledge/code/data
  views, selectable existing-artifact context, direct Claim-to-Evidence navigation,
  governed-action center, Studio, Eval, and administration console.

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
  Frozen Conversation + Memory + Evidence → Understand → Plan → Execute → Verify → Reflect → Respond
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
apps/desktop/             Experience Desktop client (`obsion-desktop`); App Server + REST, no Harness
apps/cli/                 Experience CLI (`obsion-cli`); App Server + REST client
apps/ide-extension/       Experience VS Code client; App Server + REST, no Harness
apps/im-adapter/          Experience IM adapter (`obsion-im`); envelopes, loopback webhook, explicit Feishu HTTP
services/control-plane/   Python App Server, Harness, gateways, and domain services
packages/                 Python, TypeScript, and Java SDKs (Connector SPI is Python)
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
documentation is at <http://localhost:8080/api/docs>. The Experience CLI talks to the
same App Server:

```bash
export OBSION_URL=http://127.0.0.1:8080
export OBSION_TOKEN="$OBSION_DEV_BEARER_TOKEN"
uv run obsion-cli ask "你好"
```

The Experience VS Code extension uses the same App Server. After `make bootstrap`,
build it with `make dev-ide`, then run **Obsion: Set Token** (or export
`OBSION_TOKEN`) and **Obsion: Ask**. Settings may store `obsion.baseUrl` and
`obsion.protocol` only.

The Experience IM adapter is the same client boundary. Bind a stable sender id to a
User first. Nicknames cannot authorize. `development` uses conversation flags;
`feishu`, `dingtalk`, and `wecom` require a documented callback envelope. Outbound
replies default to vendor-shaped local outbox envelopes. Explicit HTTP delivery
uses `--deliver feishu-http`, `dingtalk-http`, or `wecom-http` with matching
`OBSION_FEISHU_*` / `OBSION_DINGTALK_*` / `OBSION_WECOM_*` credentials.
`--deliver http` is rejected. Official Feishu callbacks use
`X-Lark-Signature` and `OBSION_FEISHU_ENCRYPT_KEY`. Do not put vendor secrets
in TOML. Feishu cloud documents enter Knowledge through
`POST /api/v1/knowledge/sources/feishu/documents` after `knowledge.write`;
they are not IM messages.

The consolidated Feishu/DingTalk/WeCom support matrix, secret-name contract,
rollout, smoke checks, limitations, and rollback procedure are in the
[0.75.0-dev vendor integration release notes](docs/release/0.75.0-dev.md).
The opt-in, non-sending real-tenant procedure is documented in
[Feishu live validation](docs/operators/feishu-live-validation.md).
Vendor REST ingest/sync and Agent execution now share the governed write boundary
described in the [Phase 77 architecture gate](docs/architecture/phase-77-vendor-knowledge-write-gateway.md).
Vendor source browsing uses the same no-Run Gateway through the L1, side-effect-free
contracts described in the
[Phase 78 architecture gate](docs/architecture/phase-78-vendor-knowledge-read-gateway.md).
No-Run L2 writes use the durable retry/UNKNOWN contract described in the
[Phase 79 idempotency gate](docs/architecture/phase-79-operator-capability-idempotency-gate.md).

```bash
uv run obsion-im ingest --conversation ops-room --sender-id alice-stable --text "你好"
uv run obsion-im --channel feishu ingest --envelope '{"type":"url_verification","challenge":"challenge-1"}'
uv run obsion-im --channel feishu serve --listen 127.0.0.1:8787
uv run obsion-im --channel feishu --deliver feishu-http health
uv run obsion-im --channel dingtalk --deliver dingtalk-http health
uv run obsion-im --channel wecom --deliver wecom-http health
```

The Experience Desktop client is the same App Server boundary in a dedicated window.
Config JSON may store `baseUrl` and `protocol` only. The bearer goes in
`~/.config/obsion/desktop.secret` or `OBSION_TOKEN`:

```bash
npm run build --workspace @obsion/desktop
npx obsion-desktop ask "你好"
npx obsion-desktop serve
```

Development mode seeds one local organization, administrator, capability catalog, model
profiles, agents, skills, and the internal knowledge connector. Paste the local-only
`OBSION_DEV_BEARER_TOKEN` from `.env` into the Workbench login page to create a
revocable browser session. REST and App Server SDK clients may continue to send it as
an explicit Bearer; development mode no longer treats an absent credential as the
seeded administrator.

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
`uv run obsion validate-evaluations`, release evaluation gates with
`uv run obsion validate-eval-gates`, credential literals with
`uv run obsion scan-secrets`, an SBOM with `uv run obsion sbom`, and the OpenAPI
contract regenerated with `uv run obsion openapi`.
The current operator release contract is validated with
`uv run obsion validate-release-notes`; CI rejects non-contiguous phase ranges,
missing referenced documents, unsafe vendor origins, credential values in place of
environment-variable names, and incomplete rollout/rollback declarations.

## Production deployment

Compose is suitable for local evaluation and single-host development. Kubernetes
deployments use the [Obsion Helm chart](deploy/helm/obsion/README.md), which includes
non-root workloads, read-only filesystems, probes, disruption budgets, network-policy
defaults, and an idempotent pre-upgrade migration Job.

Before production, configure OIDC, TLS, PostgreSQL backups, Redis persistence, object
storage lifecycle, an OTLP collector, external secrets, a read-only query identity,
connector egress allowlists, and tenant-scoped policies. See the
[operator runbook](docs/operators/runbook.md), [deployment](docs/operators/deployment.md),
[administrator guide](docs/operators/administrator.md),
[developer guide](docs/developers/guide.md),
[threat model](docs/security/threat-model.md),
[backup/restore](docs/operators/backup-restore.md), [upgrade](docs/operators/upgrade.md),
and [v1 readiness](docs/release/v1-readiness.md).

## APIs and SDKs

The management contract is REST under `/api/v1`; the unified bidirectional client
contract is WebSocket/JSON-RPC at `/api/v1/app-server`, and Run events also remain
available as resumable Server-Sent Events. The generated REST contract plus protocol,
authentication, error, and retry conventions are documented in
[API documentation](docs/api/README.md). Async Python and browser-safe TypeScript
clients live under `packages/`. `obsion-cli`, `@obsion/ide-extension`, `obsion-im`,
and `obsion-desktop` are Experience clients of those SDKs and must not implement a
second Agent loop. Workbench Studio uses REST `/api/v1/studio` for registry
validation, version publish, compare, and Agent/Skill rollback; it is not a second
runtime and does not split traffic.

## Community

Obsion is licensed under [Apache License 2.0](LICENSE). Please read
[CONTRIBUTING.md](CONTRIBUTING.md), [GOVERNANCE.md](GOVERNANCE.md), the
[Code of Conduct](CODE_OF_CONDUCT.md), and [SECURITY.md](SECURITY.md) before
contributing or reporting a vulnerability.
