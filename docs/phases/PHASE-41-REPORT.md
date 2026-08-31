# PHASE-41-REPORT — gRPC in-process Gateway transport

## What was implemented

Phase 41 installs gRPC as a Capability Gateway transport. It is a protocol behind
the gateway, not a remote channel supervisor and not a second control-plane language.

- `DevelopmentGrpcExecutor` encodes `{service, method, message}` and dispatches to
  in-process handlers.
- Development connector `obsion-grpc-development` / capability `grpc.development.echo`
  is seeded for local catalog use. No AgentSpec declares it.
- Remote hosts, TLS channels, protobuf/grpcio, and non-empty egress fail closed
  (`capability_transport_unavailable`). Registry manifests with those shapes are
  rejected.
- Connector credentials are not copied into the invocation envelope.
- ADR 0020 records the in-process boundary. No schema migration.

## Architecture decisions

gRPC stays behind Policy, grants, rate limits, schema validation, Evidence, and
audit. This is not HTTP/2 to a remote stub. Vendor IM HTTP is still not
implemented.

## Validation

- `uv run pytest --no-cov` — 542 passed, 18 opt-in PostgreSQL tests skipped,
  including `test_phase41_grpc_transport.py`.
- Architecture AST: `capabilities/grpc.py` does not import grpcio, protobuf, HTTP
  clients, or subprocess. `harness/runtime.py` does not import the gRPC executor.
- Workbench 治理控制台 catalog copy includes `MCP/SDK/gRPC 为进程内适配器`. Connector
  health lists `obsion-grpc-development`. Composer still has one prompt and no Agent
  picker.

## Remaining risks

- Remote gRPC channels and protobuf codecs require a tenant endpoint and a later
  ADR; they are not implemented.
- Public IM webhook hosting, WeCom AES decrypt, and vendor HTTP POST still require
  a real tenant application.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
- Signed `1.0.0` remains operator-owned.
