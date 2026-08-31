# ADR 0020: gRPC is an in-process Capability Gateway transport

- Status: Accepted
- Date: 2026-08-29

## Context

`CapabilityTransport.GRPC` existed in the enum and registry with no executor.
Invoking a gRPC capability returned `capability_transport_unavailable`. goal.txt
lists gRPC beside MCP, HTTP, SDK, and SQL Proxy as a protocol behind the Gateway.
Opening a `grpcio` channel to a remote host, generating protobuf stubs, or spawning
a sidecar would fake an integration and bypass the connector allowlist. A Java
control plane is forbidden.

## Decision

Register `DevelopmentGrpcExecutor` for `CapabilityTransport.GRPC`. It encodes a
unary invocation envelope `{service, method, message}` and dispatches to in-process
handlers keyed by `connector_type`. The development adapter exposes
`obsion.development.Echo/Ping` on connector type `grpc-development`.

Connector endpoint, non-empty egress, and configuration keys that imply a remote
channel (`host`, `port`, `tls`, `channel`, `stub`, `proto`, `grpcio`, and similar)
fail closed with `capability_transport_unavailable`. Registry manifests with
`transport: GRPC` are rejected if they declare those shapes. Connector credentials
are not copied into the invocation envelope. Harness does not import the executor.
No AgentSpec declares the development echo capability.

This is not a vendor gRPC mesh and not HTTP/2 to a remote stub. Existing INTERNAL,
HTTP, MCP, SDK, and SQL_PROXY capabilities stay on their transports. AGENT and
WORKFLOW transports remain uninstalled.

## Consequences

Operators can bind a versioned gRPC capability through the same Gateway path as
other transports. Remote channels, protobuf codecs, and `grpcio` remain
unimplemented until a later ADR with a real tenant endpoint and allowlist. Vendor
IM HTTP remains unimplemented.
