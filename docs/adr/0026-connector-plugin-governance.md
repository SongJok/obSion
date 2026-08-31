# ADR 0026: Connector plugin governance is a static supply-chain gate

- Status: Accepted
- Date: 2026-08-29

## Context

goal.txt requires a plugin lifecycle of Develop → Security Scan → Signature →
Registry → Approval → Production, and that every plugin declare Network, Filesystem,
Capabilities, Secrets, and Risk. Phase 46 added the in-process Connector SPI. Without
a promotion gate, an operator could mark an SPI connector ACTIVE with L5 risk, inline
secrets, or production unsigned configuration. Dynamic loading, pip install, binary
malware scanning, and GPG/cosign verification would fake a supply chain we cannot
operate in V1.

Harness Approvals are bound to Runs. Plugin promotion is an operator registry action,
not a conversational ASK.

## Decision

Connector SDK types (`connector-sdk-development`) must declare `spec.plugin` /
`configuration.plugin` with risk L0–L5, network `deny` or `gateway-only`, sandbox
filesystem mounts, `env://`/`secret://` secret references, and advertised capabilities.

Security scan is a static policy over that declaration. It does not inspect wheels,
containers, or remote URLs. In-process adapters cannot declare filesystem mounts or
egress. L5 is denied at create, scan, promote, and execute.

V1 signatures are HMAC-SHA256 over the canonical plugin JSON using
`OBSION_CONNECTOR_MANIFEST_KEY`. Development may omit a signature. Production requires
a verifiable digest; a missing key is fail-closed, not “unverified allow”.

Registry remains the Connector row plus git manifests. Discover still does not bind
Capabilities. L3–L4 cannot be created ACTIVE; `POST /api/v1/admin/connectors/{id}/promote`
requires `connectors.write` and `approval.decide`. L0–L2 development execute only
needs a passing live scan. Execute of L3+ while DRAFT returns `capability_denied`.

This is not a plugin marketplace, not OS isolation, and not a second Harness.

## Consequences

Authors can declare and HMAC-sign plugins locally via `obsion_sdk.connector`. Operators
see scan lifecycle in the Workbench 治理台. Vendor package install, remote connector
processes, and binary/GPG provenance remain unimplemented until a later ADR with a
real tenant artifact and allowlist.
