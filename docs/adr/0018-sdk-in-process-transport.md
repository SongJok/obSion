# ADR 0018: SDK is an in-process Capability Gateway transport

- Status: Accepted
- Date: 2026-08-29

## Context

`CapabilityTransport.SDK` existed in the enum and registry with no executor.
Invoking an SDK capability returned `capability_transport_unavailable`. goal.txt
lists SDK beside MCP, HTTP, gRPC, and SQL Proxy as a protocol behind the Gateway.
Dynamically importing tenant-supplied modules or running `pip install` would fake
an integration and bypass the connector allowlist.

## Decision

Register `DevelopmentSdkExecutor` for `CapabilityTransport.SDK`. It encodes a
typed invocation envelope `{sdk, method, arguments}` and dispatches to in-process
handlers keyed by `connector_type`. The development adapter exposes
`obsion.development.echo` on connector type `sdk-development`.

Connector endpoint, non-empty egress, and configuration keys that imply package
install or dynamic import (`pip`, `wheel`, `module`, `package`, `import`,
`entrypoint`, `url`, and similar) fail closed with
`capability_transport_unavailable`. Registry manifests with `transport: SDK` are
rejected if they declare those shapes. Connector credentials are not copied into
the invocation envelope. Harness does not import the executor.

This is not a vendor SDK marketplace. Existing INTERNAL, HTTP, MCP, and SQL_PROXY
capabilities stay on their transports. GRPC remains uninstalled.

## Consequences

Operators can bind a versioned SDK capability through the same Gateway path as
other transports. Third-party package install and remote SDK HTTP remain
unimplemented until a later ADR with a real tenant artifact and allowlist.
