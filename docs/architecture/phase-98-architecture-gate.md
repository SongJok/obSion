# Phase 98 architecture review: local operator access and Yunxiao readiness

## 2026-09-11 connection increment

Decision: [ADR 0105](../adr/0105-pat-catalog-and-stream-trust.md). The increment
preserves the single Python control plane, existing PostgreSQL schema, explicit
credential references, Policy/Gateway authorization and immutable delivery
lineage. PAT-wide metadata has a dedicated operator scope with current identity
and Connector checks before and after I/O; it does not automatically grant
repository or Agent access. File normalization still enforces commit/path, size
and blob integrity. Repeated binding cannot change scope or reactivate a disabled
binding. Stream initializes trusted CAs without disabling TLS and emits only
content-free receive diagnostics.

Real test evidence now includes the requested administrator's login, point-to-point
DingTalk Inbox/Run/reply/SUCCESS/READ, complete PAT catalog enumeration (two
organizations, 129 repositories), 129 successful repository reads, and one
successful pinned-commit/file read chain per organization. The earlier
pending lists below are historical requirements; current outcomes and remaining
items are in [M1c validation](../phases/productization-m1c-validation.md).
No schema migration is needed for this increment; isolated PostgreSQL migration
round-trip evidence is included there. Phase 99/production promotion remains closed.

**Status: PASS for repository, local development and the documented test-tenant
connection slice.** Broader UAT and Alpha.1 production promotion remain **PENDING**.

This is the single architecture gate for [Phase 98](../phases/PHASE-98-REPORT.md).
It consolidates the local password credential review and the Yunxiao readiness
review. Their acceptance boundaries and evidence remain separate: local password
validation does not satisfy Yunxiao live validation or production approval.

## Local password credentials

## Review question

Can Obsion establish the first local administrator and a usable Alpha.1 development
session without adding a second identity/authorization path, exposing plaintext,
weakening OIDC, or sending sensitive traffic to an unapproved model endpoint?

**Status: PASS for repository and local-development scope. Alpha.1 production
promotion remains PENDING on the existing operator-owned gates.**

### Delivery contract

- `POST /api/v1/auth/password-session` is a public credential-exchange endpoint only.
  It validates browser origin, resolves an already provisioned identity, rotates the
  ordinary revocable session, and returns the existing `AuthSessionView`.
- `obsion provision-user` is the sole bootstrap enrollment path. It is local CLI only,
  organization-scoped and idempotent, grants only a declared system role, never accepts
  a password argument, and refuses the weak-password override outside development/test.
- `user_credentials` stores only a salted scrypt envelope and bounded lockout metadata.
  PostgreSQL row locking serializes counter changes; rejected attempts commit.
- Unknown, ambiguous, missing-credential, wrong-password, and malformed-credential
  paths fail closed. Nonexistent identities still perform a bounded sentinel scrypt
  derivation so equal response bodies do not hide a timing oracle.
- Workbench password and access-token tabs converge on the same Principal/session.
  Mode changes and failed exchanges remove secrets from component state.
- `.env.example` covers every `Settings` field plus local Compose/operator variables.
  When an ignored `.env` exists, its key set must match the example exactly. Optional
  Compose loading applies only to the API; internal service URLs override host values.
- Model credentials resolve only inside Model Gateway through
  `env://OBSION_AI_API_KEY`. A local non-private endpoint may serve PUBLIC/INTERNAL
  Runs through logical profiles; the private profile is deliberately unbound.

### Automated acceptance map

- `test_phase98_password_authentication.py` covers derivation, normalization, policy,
  parameter bounds, equivalent unknown-user work, enrollment/rotation, lockout,
  origin checks, browser exchange, CLI argument safety, environment completeness,
  and Compose credential scope.
- `session-gate-interactions.test.tsx` drives the password/token tabs, submission,
  rejection, lockout guidance, and secret clearing through the mounted component.
- `test_postgres_phase98_user_credential_migration.py` runs upgrade, downgrade, and
  re-upgrade in a dedicated disposable PostgreSQL database and verifies the complete
  column/constraint/index contract.
- Static Error/OpenAPI gates cover the new public route and error codes. The full
  repository quality, migration, SDK, and candidate-contract gates remain mandatory.

### Local validation evidence

- The requested administrator is active in the local PostgreSQL organization, holds
  the admin role, and has one unlocked scrypt credential row; no plaintext is stored.
- A local endpoint was created through the audited admin API and bound to `fast`,
  `reasoning-high`, and `coding-high` only. A Harness Run completed through the durable
  Workspace → Thread → Turn → Run → Step → Event model with a successful ModelCall,
  `answer.delta`, and `run.completed`.
- The opt-in, non-sending live Feishu suite passed with process-injected operator
  credentials. That original validation did not cover DingTalk or WeCom;
  DingTalk test-tenant results were subsequently added in the increment above.

### Remaining gates

Clean staging deploy/UAT, staging-scoped timed restore, HIGH/CRITICAL registry policy,
artifact signatures, live OIDC/secret manager/read replica, security/data-owner
approval, and maintainer publication authority remain PENDING. No local test or CI
result is allowed to fabricate those approvals or publish with `ci.txt`.

## Yunxiao readiness

## Review question

Can a Yunxiao personal access token list every repository it can see without
giving an Agent the token, a remote MCP session, or a write-capable tool catalog?

**Current status: test-tenant catalog/repository and sampled source reads PASS.**
The original contract-only review was pending. Secret-manager injection, complete
scope inventory, staging deployment, security review and accountable production
approval remain operator-owned gates; local PAT evidence does not satisfy them.

### Contract

```text
active binding -> Principal -> Policy -> connector grant -> schema/rate limit
  -> CredentialBroker -> X-Yunxiao-Token REST GET -> normalized repositories
  -> output validation -> masking/Evidence -> audit and telemetry
```

- The only capability is `yunxiao.repositories.list` (`code.read`, `CODE`, L1,
  HTTP, `NONE`). Its input permits only operation, page, per-page, and search.
- It is first-party HTTP, rather than a remote Streamable HTTP or stdio MCP
  integration. Remote MCP violates the in-process MCP contract and the official
  default tool set includes writes.
- The PAT is an opaque secret-manager value. It is sent only as
  `X-Yunxiao-Token`, never as a query parameter or generic Bearer credential.
- The central endpoint is `https://openapi-rdc.aliyuncs.com`; the connector first
  enumerates PAT-visible organizations and then reads each fixed Codeup repository
  path. A regional endpoint must be HTTPS, explicitly allowlisted, and declares the
  organizations it is permitted to read.
- Repository output is allowlisted metadata with bounded descriptions and sanitized
  URLs. Vendor error text, avatars, unrecognized fields, credentials, and response
  bodies are not exposed.
- `connectors/examples/yunxiao-read-only.yaml` is a `DRAFT` declaration. Builtin
  registration creates no Yunxiao Connector or CapabilityBinding.

### Automated acceptance map

- `services/control-plane/tests/test_phase98_yunxiao_read_only.py` verifies the
  fixed PAT request header, no Bearer/request body/query secret, all visible
  organization discovery, bounded normalization, exact egress, opaque credential
  contract, malformed response rejection, no inline configuration credential, and
  Policy denial before credential resolution or executor invocation.
- Existing Gateway tests continue to pin Policy, grants, schema validation,
  rate-limiting, output validation, audit, telemetry, masking, and Evidence around
  all HTTP connector execution.

### Pending operator evidence

The repository provides `make validate-yunxiao-live` and the operator procedure
in `docs/operators/yunxiao-live-validation.md`. This bounded PAT-visible
repository smoke test is readiness input, not a promotion gate by itself. It
must be run with an operator-injected secret before claiming live validation.

- Store a least-privilege PAT under a `secret://` reference without committing it.
- Create a DRAFT connector using the example, approve its exact endpoint/egress,
  then explicitly activate and bind it in a staging organization.
- Demonstrate allowed and denied Principals, PAT-visible organization enumeration,
  empty-result behavior, pagination, audit records, and token redaction.
- Attach staging/UAT, secret-manager, OIDC, recovery, signing, vulnerability, and
  human security/data-owner evidence to the Phase 98 promotion record. Local mocks
  do not satisfy these gates.
