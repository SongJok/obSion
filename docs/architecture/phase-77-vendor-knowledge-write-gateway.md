# Phase 77 Vendor Knowledge write-gateway review

## Review question

Do all vendor REST ingest/sync writes traverse the governed Capability execution
boundary without fabricating a Harness Run or weakening the generic read-only Agent
Gateway?

**Status: PENDING — automated checks do not constitute tenant data-owner, staging, or
security approval.**

## Delivery contract

- Eight Feishu/DingTalk/WeCom/Confluence ingest/sync endpoints call
  `CapabilityGateway.invoke_operator`.
- Active CapabilityVersion, resource selector, environment, connector grant, schema,
  Policy, rate key, credential broker, executor, timeout, masking, telemetry, and
  Audit are enforced.
- Only L2 idempotent `knowledge.ingest` / `knowledge.sync` with `knowledge.write` may
  use the no-Run entry. Every other contract is forced through a high-risk deny.
- Policy DENY and ASK occur before credential resolution. ASK creates no Approval.
- Operator writes use `ActorType.USER`, the HTTP request UUID as correlation id, and a
  durable PolicyDecision/Audit record.
- No Run, Step, Event, Evidence, or Agent version is invented.
- Capability output schemas are vendor-neutral and carry document version ids.
- Test applications alone map `test` to seeded development connectors; staging and
  production require exact environment matches.
- Existing REST inputs, outputs, error codes, and status codes remain compatible.

## Automated acceptance map

- `test_phase77_vendor_knowledge_write_gateway.py` proves success Audit/Policy,
  permission denial, rate-before-secret, ASK-without-Approval, and eight-route
  architecture coverage.
- Phase 64/65/66/71/72/73 tests prove all four vendor response and ACL contracts.
- Contract quality gates explicitly review every new error origin, forwarding sink,
  and helper call.

## Migration review

No relational or Event schema changes are required. Builtin capability checksums
publish a new immutable version because the output schema is generalized and adds
`version_id`; existing versions remain replayable.

## Remaining gate

Vendor browsing GET routes still resolve credentials and call clients directly. They
are not claimed complete under this write-only phase and are the next architecture
gap.
