# ADR 0017: MCP is an in-process Capability Gateway transport

- Status: Accepted
- Date: 2026-08-29

## Context

`CapabilityTransport.MCP` has been in the enum, OpenAPI, and registry since the
initial schema, but the Gateway had no executor. Invoking an MCP capability returned
`capability_transport_unavailable`. goal.txt requires MCP as one protocol behind
Capability Gateway (`Agent → Capability → Gateway → MCP/HTTP/gRPC/SDK/SQL Proxy`)
and forbids turning the whole platform into MCP. Spawning `npx`/stdio servers or
POSTing to remote MCP URLs without a tenant-owned allowlisted endpoint would fake an
external integration and open a subprocess/SSRF path.

## Decision

Register `DevelopmentMcpExecutor` for `CapabilityTransport.MCP`. It encodes
`tools/call` as JSON-RPC 2.0 (`jsonrpc: "2.0"`, protocol version `2024-11-05`) and
dispatches to in-process handlers keyed by `connector_type`. The development adapter
exposes `obsion.echo` on connector type `mcp-development`.

Connector endpoint, non-empty `allowed_egress`, and configuration keys that imply
process spawn or remote transport (`command`, `args`, `cwd`, `env`, `url`, `stdio`,
`npx`, `docker`, and similar) fail closed with `capability_transport_unavailable`.
Registry manifests with `transport: MCP` are rejected if they declare those shapes.
Connector credentials are resolved by the Gateway and are not copied into JSON-RPC
params or tool results. Harness still does not import the executor; Agents still do
not receive credentials.

This is not whole-system MCP. Existing INTERNAL, HTTP, and SQL_PROXY capabilities
stay on their transports. GRPC and SDK remain uninstalled and fail closed.

## Consequences

Operators can bind a versioned MCP capability and get Evidence, policy, audit, and
timeouts through the same Gateway path as other transports. Remote MCP servers,
stdio process supervisors, and vendor HTTP MCP remain unimplemented until a later
ADR with a real tenant endpoint and egress allowlist.
