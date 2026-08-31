# ADR 0056: Operator Knowledge writes use a no-Run Capability Gateway entry

- Status: Accepted
- Date: 2026-08-30

## Context

Feishu, DingTalk, WeCom, and Confluence REST source-management routes resolved
connectors, credentials, rate limits, and vendor clients directly. Agent execution used
`CapabilityGateway`. The REST routes shared the rate-limit key but did not persist an
equivalent Policy decision or capability Audit. Calling the existing Run entry with a
random UUID would be incorrect: Runtime Events, Evidence, and Approvals have durable
Run foreign keys and the Harness model must never contain fabricated Runs.

## Decision

`CapabilityGateway` exposes a separate `invoke_operator` entry for authenticated
control-plane operations. It resolves the same active CapabilityVersion, binding,
resource selector, and Connector; evaluates Policy; enforces connector grants, input
and output schemas, the exact capability/connector rate key, credential brokering,
timeout, executor, masking obligations, telemetry, and append-only Audit.

The operator entry is closed to every contract except `knowledge.ingest` and
`knowledge.sync` with permission `knowledge.write`, risk L2, and idempotent-write side
effect. It uses `ResourcePolicyInput` because the generic Agent policy entry is and
remains read-only. A Policy `ASK` result is denied: without a real Run there is no
legal Approval aggregate. The operator entry never emits Run Events or Evidence and
passes `run_id=None` to connector context. Its HTTP request UUID is the audit and
telemetry correlation id.

All four vendor REST ingest/sync endpoints now call this entry and preserve their
paths, request schemas, response schemas, HTTP status, explicit ACL behavior, and
source-specific validation codes. The shared capability output contract is vendor
neutral and includes immutable `version_id` lineage.

## Consequences

Operator and Agent writes share one capability/connector execution boundary without
corrupting Workspace → Thread → Turn → Run → Step → Event. Operator ingestion creates
Organization Knowledge, not Run Evidence. A later Agent retrieval produces normal
Evidence and citations.

Vendor space/document browsing GET routes are still direct control-plane reads. They
remain permission-checked but require a later read-gateway phase for identical Policy,
rate, credential, and Audit treatment. Phase 77 adds no database migration or new API.
