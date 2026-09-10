# Phase 98 Yunxiao readiness gate

## Review question

Can a Yunxiao personal access token list every repository it can see without
giving an Agent the token, a remote MCP session, or a write-capable tool catalog?

**Status: PENDING.** The contract and local test doubles are implemented. A real
Yunxiao tenant, secret-manager injection, live scope validation, staging deployment,
security review, and accountable approval remain operator-owned Phase 98 gates.

## Contract

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

## Automated acceptance map

- `services/control-plane/tests/test_phase98_yunxiao_read_only.py` verifies the
  fixed PAT request header, no Bearer/request body/query secret, all visible
  organization discovery, bounded normalization, exact egress, opaque credential
  contract, malformed response rejection, no inline configuration credential, and
  Policy denial before credential resolution or executor invocation.
- Existing Gateway tests continue to pin Policy, grants, schema validation,
  rate-limiting, output validation, audit, telemetry, masking, and Evidence around
  all HTTP connector execution.

## Pending operator evidence

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
