# Administrator guide

Administrators operate identity, policy, connectors, models, evaluations, and secrets.
They do not grant Agents a second permission path. Policy Engine decisions remain
ALLOW, MASK, ASK, or DENY.

## Roles

System roles are defined in the control plane. Support and Viewer cannot execute L3
actions. Wildcards are reserved for break-glass operators. Department and resource
attributes participate in ABAC; a role assignment never bypasses connector grants or
egress allowlists.

IM bots need `im.delegate`. Bind `(channel, sender_id)` to a User with `identity.write`
before ingest. Chat nicknames are not identity keys. Use the Workbench IM binding panel
or `POST /api/v1/admin/im-bindings`. Feishu/DingTalk/WeCom inbound envelopes are
translated by `obsion-im`; outbound replies default to local-outbox vendor envelopes.
Explicit `feishu-http`, `dingtalk-http`, and `wecom-http` transports post only a
completed, Policy-authorized reply; generic `--deliver http` remains rejected. The
three clients pin `open.feishu.cn`, `oapi.dingtalk.com`, and
`qyapi.weixin.qq.com`, respectively, and load credentials only from namespaced
environment variables. Inbound Feishu events verify `X-Lark-Signature`; WeCom
`Encrypt` callbacks verify/decrypt with Token and EncodingAESKey. Ciphertext without
the required material fails closed.

`obsion-im serve --listen 127.0.0.1:8787` remains the default. Public Feishu,
DingTalk, or WeCom callbacks require `--public`, TLS certificate/key files, an exact
Host allowlist, and channel-specific verification. Never put vendor credentials or
TLS private keys in TOML, connector YAML, Helm values, or Agent context.

Feishu, DingTalk, and WeCom cloud documents are separate Knowledge Capability paths
through `obsion-feishu-docs`, `obsion-dingtalk-docs`, and `obsion-wecom-docs`.
They require `knowledge.write`, pinned egress, bounded sync/rate limits, and an
explicit ACL unless authorized member-derived inheritance is available. IM bot
visibility never grants organization-wide document access. See the
[0.75.0-dev release notes](../release/0.75.0-dev.md) for the complete matrix and
rollout/rollback contract.
Use the separate [Feishu live-validation procedure](feishu-live-validation.md) for a
non-sending tenant credential/scope smoke; it is not a replacement for a governed
test-document ingest with allowed and denied Principals.
REST ingest/sync now records a user PolicyDecision and Capability Audit with the HTTP
request id. A Policy ASK is rejected because source-management requests have no Run;
use a real Harness workflow when approval and Run Evidence are required.

Desktop operators set `OBSION_TOKEN` or write `~/.config/obsion/desktop.secret` with
mode `0600`. Desktop config JSON may contain `baseUrl` and `protocol` only.

Engineers with `registry.read` / `registry.write` use Workbench Studio to validate
manifests and publish immutable Agent/Skill versions. Promote is the runtime cutover;
publish alone does not bind new Turns. Rollback restores a previous checksummed
version without rewriting it. Compare is a registry diff, not live A/B. Prompt
templates are snapshots; do not edit production text. Each Turn pins the published
snapshot it started with. Prompt `{name}` interpolation is schema-bound and cannot
include user-turn or secret fields. Specs cannot contain secrets, DSNs, or vendor model IDs.

## Secrets and connectors

Register secret references only. The Gateway resolves `credential_ref` inside connector
execution and discards the material. Administration APIs return `has_credential`, never
the envelope. HTTP connectors require an exact egress authority and TLS outside
development. Repeated transport failures open a fail-closed circuit. MCP, SDK,
gRPC, WORKFLOW, and AGENT connectors are in-process only: `command`, `module`,
`pip`, `host`, `temporal`, `harness`, `url`, and non-empty egress fail closed. The
development echo capabilities are not production integrations. Connector SDK adapters
implement `health`/`discover`/`execute` in-process; `POST /api/v1/admin/connectors/{id}/health`
and `/discover` are audited operator probes. Discover never auto-binds a Capability.
`POST /scan` is a static plugin policy (not a binary scanner). `POST /promote` activates
an SPI connector after scan; L3+ also needs `approval.decide`. Production plugins
require HMAC-SHA256 with `OBSION_CONNECTOR_MANIFEST_KEY`. L5 is denied.
A WORKFLOW connector
may set `workflow_id` to a published WorkflowDefinition UUID; the Gateway then
calls `AutomationService.trigger_workflow` once. Nested dispatch from an ANALYSIS
child Run is rejected (`budget_exceeded`).
Agent sandbox network is `gateway-only` by default and is pinned on each Run.
`network: deny` blocks capabilities at the Gateway. CPU/memory numbers in AgentSpec
are not operating-system isolation.

## Models

Bind logical profiles (`fast`, `reasoning-high`, `private`) to endpoints. Agents declare
a profile, never a vendor model ID. CONFIDENTIAL and RESTRICTED traffic must hit a
private endpoint. Token and cost accounting is per attempt.

## Evaluations

Golden Datasets live under `evaluations/datasets`. Release requires
`evaluations/gates/v1-release.yaml`. Start an Evaluation Run with `run_bindings` that
map each `run_ref` to a real terminal Harness Run. `fixtures.actual` is rejected.
Engineers with `evaluations.read` / `evaluations.write` use Workbench **评测台**
(`/api/v1/eval`) to create datasets, start runs, and compare two completed runs on the
same snapshot. Analysts may read. Agent/Skill runtime rollback is Studio rollback
(promote of a previous snapshot), not Eval compare. Prompt Change is two Evaluation
Runs with distinct `prompt_pins` on the same dataset snapshot.

## Production writes

V1 conversational Agents stay read-only at L0-L2. The Action Gateway may create or
close PRs and tickets only in development/staging after immutable preflight and
independent approval. Production deploy, restart, configuration write, and database
mutation remain server-side DENY.
