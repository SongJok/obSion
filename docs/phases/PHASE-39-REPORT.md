# PHASE-39-REPORT — SDK in-process Gateway transport

## What was implemented

Phase 39 installs SDK as a Capability Gateway transport. It is a protocol behind
the gateway, not a package installer and not a vendor marketplace.

- `DevelopmentSdkExecutor` encodes `{sdk, method, arguments}` and dispatches to
  in-process handlers.
- Development connector `obsion-sdk-development` / capability `sdk.development.echo`
  is seeded for local catalog use. No AgentSpec declares it.
- Remote URLs, pip/wheel/module import, and non-empty egress fail closed
  (`capability_transport_unavailable`). Registry manifests with those shapes are
  rejected.
- Connector credentials are not copied into the invocation envelope.
- ADR 0018 records the in-process boundary. No schema migration.

## Architecture decisions

SDK stays behind Policy, grants, rate limits, schema validation, Evidence, and
audit. GRPC remains uninstalled. Vendor IM HTTP is still not implemented.

## Validation

- `uv run pytest --no-cov` — 517 passed, 18 opt-in PostgreSQL tests skipped,
  including `test_phase39_sdk_transport.py`.
- Architecture AST: `capabilities/sdk.py` does not import subprocess, HTTP clients,
  or importlib. `harness/runtime.py` does not import the SDK executor.
- Workbench 治理控制台 catalog copy includes `MCP/SDK 为进程内适配器`. Connector
  health lists `obsion-sdk-development`. Composer still has one prompt and no Agent
  picker.

## Remaining risks

- Third-party SDK install and remote SDK HTTP require a tenant artifact and a later
  ADR; they are not implemented.
- Public IM webhook hosting, WeCom AES decrypt, and vendor HTTP POST still require
  a real tenant application.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
- Signed `1.0.0` remains operator-owned.
