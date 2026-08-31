# Phase 46 Connector SDK review

## Review question

Can third-party authors implement `health`, `discover`, and `execute` against a
first-party Python Connector SPI hosted in-process by the Capability Gateway—with
automatic Auth, Audit, Timeout, Retry, Metrics, and Tracing—without pip install,
dynamic import, a Java SPI, or auto-binding Capabilities from discover?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `obsion_sdk.connector.ConnectorAdapter` is the author contract.
- `ConnectorSdkRuntime` registers in-process adapters by `connector_type`.
- Execute is INTERNAL behind Capability Gateway. Agent code never receives credentials.
- Admin `POST /api/v1/admin/connectors/{id}/health` and `/discover` require
  `admin.read`, write audit records, and never return credentials or endpoints.
- Discover does not insert CapabilityDefinition or CapabilityBinding rows.
- Remote URL, pip/module, and non-empty egress fail closed.
- Read-only execute retries bounded transient failures; side-effecting execute does not.
- No shipped AgentSpec declares `connector.sdk.echo`.
- Harness does not import the Connector SDK runtime.

## Automated acceptance map

- `packages/sdk-python/tests/test_connector.py` covers the SPI contract.
- `test_phase46_connector_sdk.py` covers echo, fail-closed remote, retry, Gateway
  invoke, admin health/discover, credential leak rejection, and AST import bans.
- Python and TypeScript REST clients wrap health and discover.

## Human review checklist

- Confirm operators do not treat `connector.sdk.echo` as a production integration.
- Confirm discover is not used as an unsupervised Capability registry.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
