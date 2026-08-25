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

Secrets are stored in an external secret manager or encrypted credential store. Agent context holds capability and connector IDs only. The execution boundary resolves a short-lived credential after authorization, passes it directly to the connector, and discards it. Secret values are redacted from logs, events, exceptions, traces, and model payloads.

## Prompt injection and data exfiltration

- context segments have explicit trust labels and precedence;
- external text cannot register tools, grant permissions, or become instructions;
- capability calls are re-authorized independently of planning output;
- sandbox egress defaults to deny and permits only approved gateways;
- DLP scans outbound model requests and connector responses;
- sensitive resource references are scoped to the run and user authorization.

## Sandbox

Execution and coding agents run with CPU, memory, disk, process, filesystem, network, and duration limits. Workspaces expose only explicit `/workspace`, `/repo`, `/artifacts`, and `/tmp` mounts. The sandbox has no direct path to production networks or long-lived credentials.

## Audit and privacy

Audits record who, when, agent, model profile, capability, resource, policy, approval, result classification, risk, and latency. Sensitive query values, PII, passwords, tokens, and secrets are removed or deterministically tokenized. Audit access itself is governed and audited.

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

## Plugin supply chain

Plugin promotion follows develop, scan, sign, register, approve, and deploy. Manifests declare network, filesystem, capability, secret, and risk requirements. Registry versions are immutable, signatures are verified before load, and production cannot install directly from an arbitrary URL.

## Threat-model verification

Required security tests cover tenant isolation, policy precedence, approval replay and
self-approval, immutable action plans, production/deferred-action bypass attempts,
provider idempotency recovery, SQL bypass attempts, ACL retrieval leakage, prompt
injection through every evidence source, secret redaction, event tampering, connector
timeout/cancellation, SSRF/egress, and malicious plugin manifests.
