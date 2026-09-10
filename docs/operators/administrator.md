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

A vendor success response must include its vendor-issued message receipt before the
control plane can record an IM delivery as sent. The local Obsion delivery identifier
is an idempotency and audit key only. In particular, a DingTalk response without
`messageId` or `message_id` is treated as an unconfirmed failed delivery for
operator reconciliation.

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

### Yunxiao repository discovery

Yunxiao repository discovery is a first-party, read-only HTTP connector. Do not add
the official remote MCP endpoint, `npx` server, Docker image, or its write-capable
tool catalog to the Connector registry. The only supported capability is
`yunxiao.repositories.list`.

Start from `connectors/examples/yunxiao-read-only.yaml` as a `DRAFT`. Store the
personal access token as one opaque secret-manager value and reference it with
`secret://yunxiao-read-only` (development-only local testing may use the blank
`OBSION_YUNXIAO_PAT` environment variable through `env://OBSION_YUNXIAO_PAT`). Never
place the value in YAML, connector configuration, a URL, browser input, Agent prompt,
or logs. The connector sends it only in `X-Yunxiao-Token` after Gateway Policy and
connector-grant authorization.

For the central service, pin `https://openapi-rdc.aliyuncs.com` in both `baseUrl` and
`allowedEgress`; Obsion enumerates organizations visible to the PAT and reads the
fixed Codeup repository route for each. A regional organization endpoint must be an
exact HTTPS origin in `allowedEgress` and must explicitly list the permitted
`organization_ids`. Activate and bind a connector only after staging validation of
Policy allow/deny behavior, pagination, audit records, and token redaction. The
connector cannot create repositories, branches, merge requests, pipelines, or
deployments.

Use the separate [Yunxiao live-validation procedure](yunxiao-live-validation.md)
before activating a staging connector. The probe is explicitly opt-in and
read-only; a successful PAT check does not authorize a Connector binding or
replace Policy, Audit, or staging evidence.

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

## Trusted DingTalk admission (M1a control plane complete)

Provision in this order using an administrator session on the control plane:

1. `POST /api/v1/admin/im-installations` with `channel=dingtalk`, vendor installation
   identifier, `corp_id`, and `app_key`. These are identity fields, not app secrets.
2. `POST /api/v1/admin/im-bindings` with the returned installation UUID,
   `sender_id`, and a provisioned Obsion `user_id`. The nullable `installation_id`
   field is mandatory for trusted ingress. Old channel-only mappings are not inferred.
3. Optionally map a group to a workspace through
   `POST /api/v1/admin/im-conversation-audiences`. This does not grant permission to
   publish private answers into that group.
4. A validated adapter submits `POST /api/v1/experience/im/trusted-events` with
   installation/corp/app identity, vendor event ID, sender, conversation, text and
   group flag. A 202 response means the Inbox transaction committed, not that a
   model completed or an outbound message was delivered.
5. Retrieve `GET /api/v1/experience/im/trusted-events/{event_id}` to follow the
   content-free status and eventual Run ID. Same-key changed content is a 409;
   unknown or inactive bindings are denied. Rebinding a sender after acceptance
   invalidates queued dispatch instead of changing the task owner.

The Inbox worker runs in the existing Python control plane. Configure
`OBSION_IM_INBOX_LEASE_SECONDS` (default 30),
`OBSION_IM_INBOX_POLL_INTERVAL_SECONDS` (default 0.25), and
`OBSION_IM_INBOX_MAX_ATTEMPTS` (default 5). Terminal failures emit
`identity.im.inbox.reject` Audit records. Do not manually rewrite an Inbox terminal
record to retry it; correct the cause and accept a genuinely new vendor event.

Apply the forward migration before activating the new service. Feature rollback
preserves the ledger; destructive downgrade is not the normal production rollback.
Multiple installation-specific identities and UNKNOWN deliveries need explicit
reconciliation before an old-schema downgrade can represent them safely.

## Durable vendor delivery (M1b repository-local complete)

Run one supervised adapter worker for each explicit vendor transport after the
control-plane migration and policy/binding configuration are in place:

```bash
obsion-im --channel dingtalk --deliver dingtalk-http outbox
```

Use `outbox --once` only for a bounded readiness probe. The worker refuses the
local-outbox transport. It claims one fenced delivery attempt, checks the current
recipient and Policy state, and marks `SENT` only after the vendor returns a message
identifier. A known pre-send or rate-limit failure receives bounded retry scheduling.
An ambiguous vendor outcome becomes `UNKNOWN` and must not be resent.

An authorized administrator reconciles UNKNOWN entries through the control-plane
delivery API with a bounded evidence reference. `SENT` requires the vendor receipt;
`CONFIRMED_UNSENT` can make the delivery eligible for a fresh scheduled attempt.
Do not modify delivery or attempt rows directly.

The `obsion-im --channel dingtalk stream` command starts the optional Stream adapter
after loading the vendor SDK and credentials from secure runtime configuration. Install
the adapter with `obsion-im[dingtalk-stream]`; that extra declares both the official
Stream SDK and SOCKS WebSocket support needed by proxy-routed deployments. Set
`OBSION_DINGTALK_APP_KEY`, `OBSION_DINGTALK_APP_SECRET`,
`OBSION_DINGTALK_UNIFIED_APP_ID`, and `OBSION_DINGTALK_ROBOT_CODE` through the runtime
secret manager. If its Python runtime has no system CA roots, the adapter uses certifi's
verified bundle for the vendor WebSocket; an operator-provided `SSL_CERT_FILE` remains
authoritative. TLS validation is never disabled. The adapter uses the same trusted Inbox
endpoint and returns a vendor ACK only after the Inbox acceptance transaction commits.
It does not prove a real tenant connection by being installed or started. M1c still
requires a test-tenant Stream lifecycle, shared HTTP ingress convergence, live
receipt/replay/denial evidence and timing measurements. An ONLINE robot configuration
does not verify message processing, and an Obsion Inbox UUID is never a vendor message
receipt.
