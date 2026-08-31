# Security model

## Trust boundaries

Obsion treats users, model providers, retrieved content, connector responses, plugins, sandboxes, and integrated enterprise systems as separate trust zones. Model output is untrusted until schema-validated and authorized. Retrieved documents, logs, web pages, SQL values, and code comments are data and cannot alter system instructions or permissions.

## Authorization

Authorization combines:

- RBAC for stable organizational responsibilities;
- ABAC for department, environment, device, time, classification, and ownership;
- resource policy for repositories, services, tables, columns, documents, and connectors;
- capability policy for action, risk, side effects, and agent identity.

Each decision returns `ALLOW`, `MASK`, `ASK`, or `DENY`, matched policy versions, obligations, and reason codes. The Capability Gateway enforces obligations; callers cannot downgrade them.

The policy fingerprint includes the effective Principal roles, permissions, department,
attributes, AgentVersion, capability version, resource, context, and matched policy
revisions. A policy may restrict an already-authorized action but cannot elevate a
Principal who lacks the capability permission. L5 is always denied, and generic L3-L5
or side-effecting capabilities never reach a connector.

## Identity and tenant boundary

Every REST operation under `/api/v1` crosses one shared authentication dependency
before the route handler runs. The App Server uses the same principal resolver during
`server.initialize`; a WebSocket connection has no authority before that exchange
succeeds. OIDC tokens must have a valid issuer, audience, lifetime, subject, and
organization claim, and the subject must resolve to an active provisioned user.

Development mode is not an authentication bypass. It maps one configured bearer
credential to a deterministic local organization and user, compares the credential in
constant time, and remains prohibited in production. Missing and invalid credentials
fail before Workspace, Thread, Run, or administration services are entered.

Browser login exchanges that development bearer or an OIDC access token once at
`POST /api/v1/auth/session`. The server returns a random opaque session in an
`HttpOnly`, `SameSite=Strict` cookie and persists only its SHA-256 digest, tenant-bound
to the provisioned User. Browser code does not store or replay the access token. REST
and App Server resolve the same revocable record; expiry, logout, user deactivation, or
deletion removes its authority. Unsafe cookie-authenticated requests must also have an
allowed Origin, and production refuses wildcard origins. Secure cookies are mandatory
outside local/test development. Explicit Bearer remains the non-browser client path.

The stable system roles are `admin`, `engineer`, `analyst`, `operator`, `support`, and
`viewer`. Only `admin` has the wildcard permission. Custom roles cannot shadow a
system-role name or receive the wildcard; permissions are explicit, normalized action
identifiers. Department membership is an organization-owned reference, not free-form
identity text.

IM senders are not Principals until bound. The durable key is `(channel, sender_id)`
to `users.id`. Chat nicknames, `display_name`, and `sender_display` have zero
authorization weight. Unmapped senders fail closed. The bot that submits an IM message
needs `im.delegate`; the resulting Turn is owned by the bound User. Documented vendor
callback envelopes may be translated locally; they are not a substitute for vendor
HTTP. Outbound replies default to local-outbox envelopes. Feishu, DingTalk, and
WeCom HTTP are the explicit `*-http` transports after Policy authorization;
`--deliver http` is rejected.
`obsion-im serve --listen` binds `127.0.0.1` only. Feishu official
`X-Lark-Signature` verification uses `OBSION_FEISHU_ENCRYPT_KEY`. WeCom
`Encrypt` decrypts with `OBSION_WECOM_ENCODING_AES_KEY` when configured;
ciphertext without EncodingAESKey fails closed. Optional `OBSION_IM_WEBHOOK_SECRET` verifies the
development JSON wrapper. Feishu cloud documents are a Knowledge source, not an
IM capability: `obsion-feishu-docs` calls `https://open.feishu.cn` only after
`knowledge.write` and never stores app secrets on the connector. Config files
must not contain vendor app credentials.

Non-browser Experience clients (`obsion-cli`, `@obsion/ide-extension`, `obsion-im`,
`obsion-desktop`) send an explicit Bearer. Desktop stores that bearer in
`desktop.secret` with owner-only permissions, not in config JSON.

Studio validate/publish is a registry write path. Specs cannot carry credentials or
DSNs; Policy still decides capability permission after a version is promoted. Eval
start/compare is an evaluation write path. Cases cannot self-report `fixtures.actual`.

Tenant isolation has two independent layers:

- repositories scope protected reads and writes by `Principal.organization_id` and
  Workspace ownership or membership;
- PostgreSQL composite foreign keys bind users, roles, departments, Workspace owners,
  and Workspace members to the same organization, so an application defect cannot
  persist a cross-organization identity edge.

Unauthorized reads use non-disclosing not-found responses where resource existence
would leak. Authorized same-organization users receive an explicit denial for a known
Workspace write they cannot perform.

## Risk levels

| Level | Meaning | Default |
| --- | --- | --- |
| L0 | public information | automatic when identity permits |
| L1 | ordinary internal read | automatic with audit |
| L2 | sensitive production read | explicit grant plus masking |
| L3 | change operation | approval required |
| L4 | production operation | strong multi-party approval |
| L5 | prohibited high-risk operation | deny |

Version one caps conversational agents and generic capability invocation at read-only
L2. The dedicated Action Gateway admits only approved L3, idempotent PR/ticket writes
in development/staging. Production actions, database writes, deployments, restarts,
configuration changes, destructive writes, and L4-L5 operations are denied regardless
of prompt, agent, role, connector, or organization policy.

## Database safety

Production query capabilities use a dedicated read-only identity and target a replica or governed query service. SQL is parsed into an AST. Only approved read forms are accepted; multi-statements, DDL, DML, unsafe functions, unbounded results, excess timeout, or excess scan budgets are rejected. Row and column policies are applied before execution, and masking is applied before data can reach a model.

## Credential safety

Secrets are stored in an external secret manager or encrypted credential store. Agent context holds capability and connector IDs only. The execution boundary resolves a short-lived credential after authorization, passes it directly to the connector, and discards it. Secret values are redacted from logs, events, exceptions, traces, and model payloads. MCP, SDK, gRPC, WORKFLOW, and AGENT executors must not copy the credential into JSON-RPC `tools/call` params, SDK/gRPC/workflow/agent invocation envelopes, or tool results. WORKFLOW `workflow_id` dispatch starts an `AutomationExecution` in the same Gateway transaction; it does not send connector credentials to a remote orchestrator.

Model credentials follow the same rule inside the Model Gateway: only a
`credential_ref` is persisted, the secret is resolved immediately before the provider
request, and `model_calls` stores only a redacted request fingerprint and usage/cost
metadata. Provider URLs are egress-allowlisted and require TLS outside local
development/test.

`CONFIDENTIAL` and `RESTRICTED` model inputs force the configured private Profile by
default. The route must also bind an endpoint explicitly marked `private=true`; missing
or inconsistent configuration fails before a provider request. Model tool requests
remain untrusted data, must match a declared JSON Schema, and cannot execute or grant
permission without the later Capability Gateway and Policy boundary.

## Prompt injection and data exfiltration

- context segments have explicit trust labels and precedence;
- external text cannot register tools, grant permissions, or become instructions;
- capability calls are re-authorized independently of planning output;
- sandbox egress defaults to `gateway-only` and is pinned on the Run; `deny` blocks
  Capability Gateway execution. Direct `curl` of arbitrary hosts is not a Harness path;
- DLP scans outbound model requests and connector responses;
- sensitive resource references are scoped to the run and user authorization.

## Sandbox

AgentSpecs declare `sandbox.network` as `deny` or `gateway-only` (default
`gateway-only`). The normalized policy, including allowed mounts
(`/workspace`, `/repo`, `/artifacts`, `/tmp`), is pinned on `run.plan.sandbox`.
Capability execution is Gateway-only; `network: deny` fails closed at the Gateway
before the executor. Model Gateway and Artifact Store remain control-plane paths,
not Agent-opened sockets. Optional CPU, memory, disk, and process fields are
declared and pinned; this control plane does not apply cgroups, seccomp, or a
container runtime. Agents never receive connector credentials.

## Audit and privacy

Audits record who, when, agent, model profile, capability, resource, policy, approval, result classification, risk, and latency. Sensitive query values, PII, passwords, tokens, and secrets are removed or deterministically tokenized. Audit access itself is governed and audited.

Turn input is sanitized before durable storage and before context capture. Text
credentials (`password=`, `token:`, API-key assignments, bearer values, credential
URIs, and private-key blocks) are replaced with redaction markers; structured payloads
use the same key-aware recursive redactor. The raw prompt is therefore not available
through the database, replay snapshot, Event payload, or model context.

## Scheduled identity and workflow integrity

Workflow versions are checksummed and database-guarded against content updates or
deletion. A schedule pins one published version and is only a timing mechanism: at fire
time Obsion reloads the accountable owner and requires the owner's current workspace
write access and `automation.trigger` permission. Invalid ownership disables the
schedule instead of falling back to a service administrator. Concurrency and
idempotency constraints prevent duplicate work, and review decisions require an
explicit permission and durable reason.

## Governed action integrity

Action requests never execute from model output or a generic capability call.
Preflight resolves real active execute and rollback capabilities, validates their
L3/idempotent HTTP contract, connector grants, environment, resource selector, egress
allowlist, owner permissions, and payload before creating an `ActionPlan`. The plan
pins the exact capability and connector versions and is protected from update or
deletion by a PostgreSQL trigger.

Execution requires an unexpired decision from a different principal for the exact
plan checksum. The worker re-authorizes the owner when it claims the request; approval
is not a substitute for current permission. Rollback requires a second approval and a
distinct provider idempotency key. Provider credentials are resolved only immediately
before the HTTP request, responses are schema-validated, size-capped, and redacted,
and every attempt persists a policy decision, safe event stream, audit record, and
notification. Lost responses are recovered with the same provider key rather than by
creating a new write.

No-Run L2 Knowledge writes use a separate principal-scoped idempotency ledger. The
claim commits before credential resolution; terminal replay never resolves a secret
or executes a connector. The ledger stores only hashes, pins, a safe terminal result,
and bounded error metadata. PostgreSQL rejects identity/terminal rewrites and early
deletion. An expired in-progress lease becomes UNKNOWN and cannot be automatically
retried. Its content-free admin projection supports investigation without exposing
the request or result payload. This control-plane ledger is not a Run/Event history.

## Plugin supply chain

Plugin promotion follows develop, scan, sign, register, approve, and deploy. Manifests declare network, filesystem, capability, secret, and risk requirements. Registry versions are immutable, signatures are verified before load, and production cannot install directly from an arbitrary URL. V1 Connector SDK adapters are in-process registrations of `health`/`discover`/`execute`; pip install, importlib, and remote URL load remain unimplemented. V1 scan is a static declaration policy. V1 signature is HMAC-SHA256 of the canonical plugin JSON with `OBSION_CONNECTOR_MANIFEST_KEY`. L5 is denied. L3+ require `approval.decide` before ACTIVE. This is not GPG, cosign, or a malware sandbox.

## Threat-model verification

Required security tests cover tenant isolation, policy precedence, approval replay and
self-approval, immutable action plans, production/deferred-action bypass attempts,
provider idempotency recovery, SQL bypass attempts, ACL retrieval leakage, prompt
injection through every evidence source, secret redaction, event tampering, connector
timeout/cancellation, SSRF/egress, and malicious plugin manifests.

See also the V1 [threat model](threat-model.md).
