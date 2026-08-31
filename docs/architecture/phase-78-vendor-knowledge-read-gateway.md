# Phase 78 Vendor Knowledge read-gateway review

## Review question

Do all vendor Knowledge browsing GET routes traverse the versioned Capability
Gateway boundary while preserving source-management authorization and REST response
compatibility, without fabricating Harness state or Evidence?

**Status: PENDING — automated checks do not constitute tenant data-owner, staging, or
security approval.**

## Delivery contract

- Eight Feishu/DingTalk/WeCom/Confluence browsing GET routes call
  `CapabilityGateway.invoke_operator` through one REST adapter.
- `knowledge.source.containers` and `knowledge.source.items` are immutable L1,
  side-effect-free, HTTP Capability contracts with strict bounded input/output
  schemas.
- Existing `knowledge.write` authorization is preserved; source inventory is not
  exposed to ordinary `knowledge.read` principals.
- Active CapabilityVersion, source binding, environment, connector grant, Policy,
  schema, rate key, credential broker, executor, timeout, masking, telemetry, and
  Audit are enforced before REST response projection.
- DENY/ASK and rate rejection occur before credential resolution. ASK creates no
  Approval because the operation has no durable Run.
- PolicyDecision and Audit retain the selected CapabilityVersion and request
  correlation id with `ActorType.USER`.
- Connector knowledge budgets bound pagination and recursive source walks.
- No Run, Step, Event, Evidence, Approval, or Agent version is created.
- Existing REST paths, status codes, list/object shapes, and vendor field names remain
  compatible.

## Automated acceptance map

- `test_phase78_vendor_knowledge_read_gateway.py` covers all eight responses,
  Policy/Audit, permission-before-secret, rate-before-secret, immutable descriptor
  risk/side-effect contracts, and the absence of REST credential/resolver bypasses.
- Phase 64/65/66/71/72/73/77 suites preserve vendor client, ACL, sync, provenance,
  hardening, and write-gateway behavior.
- Registry/API/contract quality gates validate strict schemas, bindings, reviewed
  error forwarding, and compatibility.

## Migration review

No Alembic or Event migration is required. Builtin seeding publishes two new
Capability definitions and immutable versions, each bound to the four existing vendor
connectors. Existing Run pins and REST clients remain valid.

## Human review checklist

- Confirm each tenant's vendor app scopes permit only the intended source inventory.
- Confirm connector-specific pagination budgets and upstream quotas before rollout.
- Inspect PolicyDecision/Audit records for allowed and denied source browsing.
- Validate a tenant-approved Feishu document end to end separately; this phase does
  not claim permitted-document ingest/search/citation acceptance.
