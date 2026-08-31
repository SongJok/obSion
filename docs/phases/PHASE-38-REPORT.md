# PHASE-38-REPORT — MCP in-process Gateway transport

## What was implemented

Phase 38 installs MCP as a Capability Gateway transport. It is a protocol behind
the gateway, not a second runtime and not a conversion of every connector to MCP.

- `DevelopmentMcpExecutor` encodes `tools/call` as JSON-RPC 2.0 and dispatches to
  in-process handlers.
- Development connector `obsion-mcp-development` / capability `mcp.development.echo`
  (`obsion.echo`) is seeded for local catalog use. No AgentSpec declares it, so
  Harness will not plan it.
- Remote MCP URLs, stdio/`npx`/`command` spawn, and non-empty egress fail closed
  (`capability_transport_unavailable`). Registry manifests with those shapes are
  rejected.
- Connector credentials are not copied into JSON-RPC params or results.
- ADR 0017 records the in-process boundary. No schema migration.

## Architecture decisions

MCP stays behind Policy, grants, rate limits, schema validation, Evidence, and
audit. GRPC and SDK transports remain uninstalled. Vendor IM HTTP is still not
implemented.

## Validation

- `uv run pytest --no-cov` — 509 passed, 18 opt-in PostgreSQL tests skipped,
  including `test_phase38_mcp_transport.py`.
- `uv run ruff check .` on changed Python.
- Architecture AST: `capabilities/mcp.py` does not import subprocess, HTTP clients,
  or dynamic imports. `harness/runtime.py` does not import the MCP executor.
- Workbench at `http://localhost:3000` 治理控制台 catalog copy includes
  `MCP 为进程内适配器`. Connector health lists `obsion-mcp-development` and states
  that command/url/endpoint fail closed. Composer still has one prompt
  (`向 Obsion 提问`) and no Agent picker.

## Remaining risks

- Remote MCP servers and process supervisors require a tenant endpoint and a later
  ADR; they are not implemented.
- Public IM webhook hosting, WeCom AES decrypt, and vendor HTTP POST still require
  a real tenant application.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
- Signed `1.0.0` remains operator-owned.
