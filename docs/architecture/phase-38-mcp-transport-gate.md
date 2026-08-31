# Phase 38 MCP in-process transport review

## Review question

Can an MCP capability execute only through the Capability Gateway as in-process
JSON-RPC `tools/call`, produce Evidence, and fail closed on process spawn, remote
URLs, and non-empty egress—without converting the platform to MCP or bypassing
Policy?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `DevelopmentMcpExecutor` is registered for `CapabilityTransport.MCP`.
- In-process `mcp-development` handles `obsion.echo` via JSON-RPC 2.0.
- Connector `endpoint`, `allowed_egress`, and spawn/URL configuration fail closed
  with `capability_transport_unavailable`.
- MCP manifests cannot declare `command`/`args`/`url`/`baseUrl` or non-empty egress.
- Credentials are not copied into JSON-RPC params or results.
- Harness does not import the MCP executor. GRPC and SDK remain uninstalled.
- Existing INTERNAL/HTTP/SQL_PROXY transports are unchanged.

## Automated acceptance map

- `test_phase38_mcp_transport.py` covers JSON-RPC encoding, echo round-trip, remote
  fail-closed, unknown connector/tool, Gateway invocation, seeded catalog, and AST
  import bans.
- Registry tests reject remote MCP manifest shapes.
- Error origin sinks in `error_producer_manifest.py` cover `mcp.py`.

## Human review checklist

- Confirm operators do not treat `mcp.development.echo` as a production integration.
- Confirm remote MCP / stdio supervisors remain absent until a tenant endpoint exists.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
