# Phase 47 Connector plugin governance review

## Review question

Can Connector SDK plugins move through Develop → static Security Scan → HMAC
Signature → Registry → Approval → Production—declaring Network, Filesystem,
Capabilities, Secrets, and Risk—without dynamic loading, pip install, fake binary
scanning, or a Harness-bound Approval record?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- SPI connectors require `plugin` with risk, network, filesystem, secrets, and capabilities.
- Scan is static. L5, unrestricted network, in-process filesystem mounts, inline secrets,
  and capability names outside the connector set fail closed.
- Production requires HMAC-SHA256 of the canonical declaration with
  `OBSION_CONNECTOR_MANIFEST_KEY`. Development may omit a signature.
- `POST /api/v1/admin/connectors/{id}/scan` persists the scan onto `last_health.scan`.
- `POST /api/v1/admin/connectors/{id}/promote` activates L3+ only with `approval.decide`.
- L3+ cannot be created ACTIVE. Execute of an unpromoted L3+ plugin is `capability_denied`.
- Discover still does not bind Capabilities. Harness does not import plugin governance.
- No pip, importlib, remote URL load, GPG, or cosign.

## Automated acceptance map

- `packages/sdk-python/tests/test_connector.py` covers declaration parse and HMAC.
- `test_phase47_connector_plugin.py` covers scan, L5, production signature, L3 promote,
  admin scan/promote, first-party `not_applicable`, and AST import bans.
- Python and TypeScript REST clients wrap scan and promote.

## Human review checklist

- Confirm operators do not treat HMAC as a substitute for a real artifact signature
  program.
- Confirm L3 promote is not confused with Run Approval ASK.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
