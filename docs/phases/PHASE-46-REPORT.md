# PHASE-46-REPORT — Connector SDK (Python SPI)

## What was implemented

Phase 46 adds the developer Connector SPI from goal.txt: `health`, `discover`, and
`execute`. Authors implement `obsion_sdk.connector.ConnectorAdapter`. The control plane
hosts registered in-process adapters and wraps execute with Gateway Auth, Policy,
Audit, timeout, Evidence, plus runtime retry, metrics, and tracing.

- Development connector `obsion-connector-sdk-development` / capability
  `connector.sdk.echo` is seeded on INTERNAL transport. No AgentSpec declares it.
- Admin `POST /api/v1/admin/connectors/{id}/health` updates `last_health`.
  `POST /api/v1/admin/connectors/{id}/discover` returns advertised operations and the
  current binding count. Discover does not bind capabilities.
- Remote URLs, pip/module import, and non-empty egress fail closed
  (`capability_transport_unavailable`). Registry manifests with those shapes are
  rejected for `connector-sdk-development`.
- Credentials are injected only into execute and stripped from results.
- Python and TypeScript SDKs wrap health/discover. Workbench 治理控制台 can probe and
  inspect discovery for SPI connectors. This is not a Java SPI.
- ADR 0025 records the in-process authoring boundary. No schema migration.

## Architecture decisions

Connector SDK is an authoring contract, not a new wire protocol. MCP/SDK/gRPC remain
transports. Execute stays behind Policy, grants, rate limits, schema validation,
Evidence, and audit. Vendor IM HTTP is still not implemented. Plugin signature
lifecycle is delivered in Phase 47. Remote connector processes remain later phases.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 589 passed, 18 opt-in PostgreSQL tests
  skipped, including `test_phase46_connector_sdk.py`.
- Architecture AST: `capabilities/connector_spi.py` does not import subprocess, HTTP
  clients, or importlib. `harness/runtime.py` does not import the Connector SDK runtime.
- Registry: 8 agents, 12 connectors, 14 skills. `connector-sdk-development` is in-process.
- Workbench catalog copy states Connector SDK is an SPI, not a package installer.
  Composer still has one prompt and no Agent picker.

## Remaining risks

- Third-party package install and remote connector processes require a tenant artifact
  and a later ADR; they are not implemented.
- Public IM webhook hosting, WeCom AES decrypt, and vendor HTTP POST still require
  a real tenant application.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
- Signed `1.0.0` remains operator-owned.
