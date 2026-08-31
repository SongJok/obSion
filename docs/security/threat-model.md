# Threat model (V1)

## Assets

- Tenant identity, roles, sessions, and policy decisions
- Connector grants, egress allowlists, and secret references
- Knowledge/ticket ACL indexes, semantic metrics, and Code Graph snapshots
- Run evidence, claims, artifacts, and audit records
- Model provider credentials resolved only inside the Model Gateway

## Actors

| Actor | Trust | Notes |
| --- | --- | --- |
| Interactive user | Authenticated principal | Cannot select specialist agents |
| Model provider | Untrusted | Output is data until schema + Policy |
| Connector / ITSM / warehouse | Untrusted transport | Egress allowlist, TLS, circuit breaker |
| Retrieved documents and tickets | Untrusted content | Cannot grant tools or permissions |
| Operator | Privileged | Still cannot inject secrets into Agent specs; Studio promote is explicit; Eval cannot accept fixtures.actual |

## Top risks and controls

1. **Prompt injection / confused deputy** — capability calls re-enter the Capability Gateway and Policy Engine. Planning text cannot register tools, raise risk, or skip ACL.
2. **SSRF** — HTTP connectors and model endpoints must match an exact egress authority. Redirects are disabled. Non-development HTTP is rejected. MCP, SDK, gRPC, WORKFLOW, and AGENT connectors cannot declare an endpoint, spawn a process, install a package, open a remote channel or workflow engine, start a nested Harness, or use non-empty egress; they stay in-process. WORKFLOW `workflow_id` dispatch reuses AutomationService and cannot recurse from an ANALYSIS child Run. Connector SDK plugins cannot declare unrestricted network or in-process filesystem mounts; production requires HMAC verification of the declaration.
3. **SQL injection / write SQL** — AST policy allows only bounded SELECT/WITH/EXPLAIN against authorized tables. UNION to unauthorized tables, stacked statements, and blocked functions fail closed.
4. **Privilege escalation** — Support and Viewer roles have no `action.execute` or wildcard. SupportAgent cannot plan ticket or cluster writes. IM nicknames cannot bind or impersonate a User; unmapped senders fail closed. Vendor IM inbound is envelope translation with optional signature verification. Feishu outbound HTTP is an Experience delivery after `im.reply.deliver`; generic HTTP, DingTalk, and WeCom delivery are rejected. The IM webhook listener may bind 127.0.0.1 only; WeCom AES ciphertext fails closed.
5. **Secret leakage** — credentials stay in `credential_ref`. Redaction strips secrets from logs, events, model payloads, and audit metadata. `obsion scan-secrets` fails CI on literal DSNs and key blocks outside tests. Studio validation rejects credential fields and provider model IDs in Agent/Skill specs. Evaluation cases cannot self-report `fixtures.actual`.
6. **Sandbox escape** — Agents declare `sandbox.network: gateway-only` or `deny`.
   Missing sandbox defaults to gateway-only; unrestricted networks, privileged flags,
   and host mounts fail closed at registry load. The policy is pinned on
   `run.plan.sandbox` and re-checked at the Capability Gateway. This process does
   not start Docker/gVisor. Agents never receive connector credentials.
7. **Dependency compromise** — CycloneDX SBOM is generated from `uv.lock`. Container builds run in CI without pushing.
8. **Duplicate or uncertain operator writes** — no-Run L2 idempotent calls commit a
   principal/request claim before credentials or connector execution. Exact retries
   replay immutable terminal state; input changes conflict. A lost lease becomes
   UNKNOWN and requires connector-specific reconciliation, never automatic retry.
   PostgreSQL guards terminal mutation and retention deletion.

## Residual risk

Staging penetration tests, HIGH/unfixed CVE policy against a private registry, live
OIDC, and live ITSM/warehouse adapters remain operator-owned. CI fails on unfixed
CRITICAL findings in the source tree and CI images. This document does not constitute
a human security sign-off.
