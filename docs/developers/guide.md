# Developer guide

Obsion is one Python control plane plus a Workbench that never implements an Agent
loop. Clients talk to the App Server. Harness owns Observe → Understand → Plan →
Execute → Verify → Reflect → Respond.

## Invariants

- Workspace → Thread → Turn → Run → Step → Event is the durable model.
- Agents never receive connector credentials. Policy, not prompt text, decides
  authorization.
- Production resources are read-only by default.
- PostgreSQL is the source of truth. Kafka and ClickHouse are not the V1 base.
- Do not add a second backend language.

## Local quality gates

```bash
make check
make test-java
uv run obsion validate-contracts
uv run obsion validate-registry
uv run obsion validate-evaluations
uv run obsion validate-eval-gates
uv run obsion scan-secrets
```

Registry YAML under `agents/`, `skills/`, and `connectors/` overrides builtins when the
process working directory is the repository root. Tests that construct the app from
another cwd still see builtins; e2e tests that need YAML must run from the repo root.

## Contracts

Event and error catalogs are frozen. Adding an error code requires catalog, origin
manifest, and tests. Prefer reusing an existing code with a distinct origin. Forwarding
sinks record file line numbers; do not silence contract tests by weakening the
analyzer.

Python, TypeScript, and Java SDKs live under `packages/`. They call REST (and, for
Python/TypeScript, the App Server). They do not embed Harness. The Java SDK is
JDK 21 REST only. `apps/cli` (`obsion-cli`), `apps/ide-extension`,
`apps/im-adapter` (`obsion-im`), and `apps/desktop` (`obsion-desktop`) are Experience
clients of those SDKs. Studio is a Workbench and REST surface (`/api/v1/studio`) for
Agent/Skill manifests, version compare, and Agent/Skill rollback. Prompt versions may
be compared; they are not rewritten. Each Turn pins PromptVersion on the Run. Pinned
templates render only schema-declared governed values. Context Builder records an
explicit KEEP / COMPRESS / SUMMARIZE / DROP ledger on `runs.context_budget`;
SUMMARIZE is extractive and does not call a model. Older conversation is compacted
by the same extractive rule and pinned on `runs.conversation_compact`. Workspace
identity is pinned on `runs.workspace_context`; the description is untrusted data.
Capability tool results occupy a separate `tool-result` untrusted segment.
`GET /api/v1/admin/slo` projects goal.txt core rates from PostgreSQL. It does not
invent OTel histogram p95. Workspace Files (`GET /workspaces/{id}/files`) are
path-versioned FILE artifacts and do not become SYSTEM text unless attached.
Workspace Reports (`GET /workspaces/{id}/reports`) are published REPORT artifacts
from evidenced Runs. Greetings do not create reports. Workspace Dashboards
(`GET /workspaces/{id}/dashboards`) compose published CHART/TABLE/SQL artifacts.
They do not invent Vega series. Workspace SQL (`GET /workspaces/{id}/sql`)
lists published SQL artifacts and does not invent warehouse rows. Workspace
Evidence (`GET /workspaces/{id}/evidence`) lists persisted Evidence rows and
does not invent citations. Workspace Timeline
(`GET /workspaces/{id}/timeline`) lists persisted Run Events and does not
invent Harness steps.
Eval is a Workbench and REST surface (`/api/v1/eval`) for
Golden Datasets, Evaluation Runs, Prompt/Agent pins, and baseline compare. `fixtures.actual` is rejected.
Do not import
control-plane modules from those clients. Build the extension with `make dev-ide`;
tokens go in Secret Storage or `OBSION_TOKEN`, never in settings. Build the desktop
client with `make dev-desktop`; tokens go in `desktop.secret` or `OBSION_TOKEN`, never
in config JSON. The IM adapter
implements documented inbound envelope translation for `development`, `feishu`,
`dingtalk`, and `wecom`. It submits `sender_id` to control-plane principal mapping.
Nicknames are not an identity source. Outbound replies default to vendor-shaped
local-outbox envelopes. `serve --listen` binds `127.0.0.1` only. Explicit
`feishu-http`, `dingtalk-http`, and `wecom-http` transports use environment
credentials after control-plane `im.reply.deliver` authorization. Loopback
Feishu callbacks verify official `X-Lark-Signature` when
`OBSION_FEISHU_ENCRYPT_KEY` is set. `--deliver http` is rejected. WeCom
`Encrypt` decrypts with `OBSION_WECOM_ENCODING_AES_KEY` when configured;
ciphertext without EncodingAESKey fails closed. `--public` is the only non-loopback
listener path and requires TLS, Host allowlisting, and channel-specific verification.
Feishu, DingTalk, and WeCom cloud documents are Knowledge sources. Their vendor REST
routes and `knowledge.ingest` / `knowledge.sync` Capabilities fetch through the
`feishu-docs`, `dingtalk-docs`, and `wecom-docs` connectors, then reuse Parser →
Chunk → ACL → Index. Shared sync budgets and provenance live in the Knowledge
connector contract. Do not import the IM adapter into Knowledge. The versioned
operator contract is [0.75.0-dev](../release/0.75.0-dev.md).
Live Feishu tests carry the explicit `live` marker and are deselected by default. Run
only the documented [non-sending validation target](../operators/feishu-live-validation.md);
never add tenant credentials to fixtures or make default CI depend on a vendor tenant.
Vendor REST ingest/sync must call `CapabilityGateway.invoke_operator`; do not restore
direct credential resolution in API handlers. The no-Run entry is closed to exact L2
idempotent Knowledge writes and cannot emit Events/Evidence or create Approval.

## Adding a capability

Declare a versioned Capability, bind a Connector with grants and egress, implement the
executor behind Capability Gateway, emit Evidence, and add Golden Dataset coverage.
A YAML file without a Gateway path is a placeholder, not a capability. MCP, SDK,
gRPC, WORKFLOW, and AGENT are Gateway transports: in-process adapters only. Do not
spawn `npx`/stdio servers, run `pip install`, open a grpcio channel, point
`endpoint` at Temporal/Airflow, or start a nested Harness from an AGENT connector.
The Connector SDK (`obsion_sdk.connector.ConnectorAdapter`) is the author SPI for
`health`, `discover`, and `execute`. Register adapters in-process. Discover does not
create Capability bindings. Do not importlib tenant modules. SPI connectors must
declare `plugin` (network, filesystem, capabilities, secrets, risk). Scan with
`POST /api/v1/admin/connectors/{id}/scan`. Production signatures use HMAC-SHA256 and
`OBSION_CONNECTOR_MANIFEST_KEY`. L3+ promote requires `approval.decide`. L5 is denied.
A WORKFLOW connector with `workflow_id` dispatches to `AutomationService.trigger_workflow`
with a depth-1 recursion budget. Nested ANALYSIS child Runs cannot start another
workflow. The automation API remains the engine for published WorkflowSpec executions.
Conversational specialist routing stays on Understanding and AgentRouter.

Agent sandbox is declared on AgentSpec, pinned on `run.plan.sandbox`, and enforced at
the Capability Gateway. `network` is `deny` or `gateway-only`. Do not add Docker or
gVisor wrappers in the Harness to simulate isolation. CPU and memory fields are
declarations, not cgroup enforcement.
