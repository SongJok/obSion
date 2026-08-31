# ADR 0025: Connector SDK is an in-process author SPI

- Status: Accepted
- Date: 2026-08-29

## Context

goal.txt requires a Connector SDK so authors implement `health()`, `discover()`, and
`execute()`, and so every connector automatically receives Auth, Audit, Timeout, Retry,
Metrics, and Tracing. Phase 39 installed SDK as a Gateway *transport* (in-process echo).
Phase 45 added REST clients that create Connector records. Neither gave third-party
authors a `health`/`discover`/`execute` contract. Dynamically importing tenant modules
or running `pip install` would fake an integration and bypass the connector allowlist.
A Java Connector SPI would be a second authoring runtime against the one-Python-backend
invariant.

## Decision

`packages/sdk-python` publishes `obsion_sdk.connector.ConnectorAdapter`. The Python
control plane hosts `ConnectorSdkRuntime`, which registers in-process adapters by
`connector_type` and adapts `execute` onto the existing INTERNAL `ConnectorExecutor`
path. Capability Gateway remains the only Agent execution boundary: Policy, Approval,
schema validation, credential resolution, Evidence, and Audit wrap `execute`.

Operator `POST /api/v1/admin/connectors/{id}/health` and `/discover` call the SPI
without a Run. Discover returns advertised operations and does not create Capability
definitions or bindings. Credentials are injected only into `execute` and never copied
into health, discover, results, events, or logs.

Remote URLs, non-empty egress, and configuration keys that imply package install or
dynamic import fail closed with `capability_transport_unavailable`. Read-only execute
retries bounded transient `OSError`/`TimeoutError`; side-effecting execute does not
retry. This is not a plugin marketplace and not a Java SPI.

## Consequences

Authors can implement connectors against a stable Python contract. HTTP, MCP, gRPC,
WORKFLOW, and AGENT transports stay on their executors until they are rewritten as
adapters. Vendor package install and remote connector processes remain unimplemented
until a later ADR with a real tenant artifact and allowlist.
