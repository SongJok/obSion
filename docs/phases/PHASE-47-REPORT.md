# PHASE-47-REPORT — Connector plugin governance

## What was implemented

Phase 47 adds the plugin supply-chain gate from goal.txt: Develop → Security Scan →
Signature → Registry → Approval → Production. Connector SDK adapters declare Network,
Filesystem, Capabilities, Secrets, and Risk. The control plane scans that declaration
statically, HMAC-signs it, and gates ACTIVE / execute.

- `obsion_sdk.connector.ConnectorPluginDeclaration` is the author contract. Canonical
  JSON plus HMAC-SHA256 uses `OBSION_CONNECTOR_MANIFEST_KEY`. This is not GPG/cosign
  and not a binary malware scanner.
- `ConnectorSdkRuntime` enforces the scan on health, discover, and execute. In-process
  plugins cannot declare mounts or egress. L5 is `v1_production_action_boundary`.
- Admin `POST /api/v1/admin/connectors/{id}/scan` writes `last_health.scan`.
  `POST /api/v1/admin/connectors/{id}/promote` requires `connectors.write`; L3+ also
  requires `approval.decide`. Discover still does not bind Capabilities.
- Seeded `obsion-connector-sdk-development` carries an L1 `deny` plugin. Manifests
  without `spec.plugin` are rejected. No schema migration.
- Python/TypeScript SDKs and Workbench 治理台 expose 扫描 / 晋升. This is not Java
  and not a plugin marketplace.

## Architecture decisions

Plugin promotion is an operator registry action, not a Harness Run Approval. First-party
HTTP/MCP/SDK/gRPC/WORKFLOW/AGENT connectors are `not_applicable`. Vendor IM HTTP is
still not implemented. Remote connector processes remain unimplemented.

## Validation

- `uv run pytest --no-cov -k "not maven"` — 599 passed, 18 opt-in PostgreSQL tests
  skipped, including `test_phase47_connector_plugin.py`.
- Architecture AST: `plugin_governance.py` does not import subprocess, HTTP clients, or
  importlib. `harness/runtime.py` does not import plugin governance.
- Registry: 8 agents, 12 connectors, 14 skills. `connector-sdk-development` declares
  plugin L1 / network deny.
- Workbench catalog copy states scan → sign → promote. Composer still has one prompt
  and no Agent picker.

## Remaining risks

- HMAC is a control-plane integrity check, not a public artifact signature program.
- Third-party package install and remote connector processes require a tenant artifact
  and a later ADR; they are not implemented.
- Public IM webhook hosting, WeCom AES decrypt, and vendor HTTP POST still require
  a real tenant application.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
- Signed `1.0.0` remains operator-owned.
