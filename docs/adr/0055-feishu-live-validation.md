# ADR 0055: Feishu live validation is explicit, non-sending, and fail-closed

- Status: Accepted
- Date: 2026-08-30

## Context

Feishu app credentials were available for operator-owned testing, while default CI
correctly skipped all live calls. The existing live tests had no single safe command,
and Knowledge client probes assumed all business errors arrived with HTTP 200. The
real tenant returned HTTP 400 with structured Feishu codes `99992402` for the fixed
nonexistent document and `99991672` for wiki-space permission. The client classified
both as upstream availability failures before parsing the JSON envelope.

## Decision

Register a strict pytest `live` marker and add `make validate-feishu-live`. The target
requires explicit opt-in plus environment-provided app id/secret, and runs exactly
three non-sending probes: IM authentication, nonexistent document failure closure,
and wiki-space read/denial. It never reads a repository credential file.

`FeishuDocsClient` now size-bounds and parses a JSON error envelope before generic
non-2xx classification. HTTP 401/403 and known inaccessible/missing resource business
codes are mapped to `FeishuDocsDeniedError`. Missing and inaccessible documents share
one result so the connector does not expose resource existence across ACL boundaries.
Unknown nonzero codes remain typed response failures; invalid/non-JSON HTTP failures
remain upstream failures. Secrets and tenant tokens remain redacted.

## Consequences

Operators have a reproducible live smoke command with no outbound message or resource
write. Real Feishu behavior is covered without enabling it in default CI. This target
validates connector adapters only; it does not create a second Capability path and
does not count as a tenant's end-to-end Knowledge acceptance. Actual ingestion still
requires the Control Plane, Capability Gateway, Policy, explicit ACL, Evidence, and
Audit.

Phase 76 adds no migration, Event, API, Agent, or model contract.
