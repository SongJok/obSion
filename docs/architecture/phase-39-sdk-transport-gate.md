# Phase 39 SDK in-process transport review

## Review question

Can an SDK capability execute only through the Capability Gateway as an in-process
invocation envelope, produce Evidence, and fail closed on package install, dynamic
import, remote URLs, and non-empty egress—without a vendor SDK marketplace?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `DevelopmentSdkExecutor` is registered for `CapabilityTransport.SDK`.
- In-process `sdk-development` handles `obsion.development.echo`.
- Connector `endpoint`, `allowed_egress`, and pip/module/url configuration fail
  closed with `capability_transport_unavailable`.
- SDK manifests cannot declare package install or remote shapes.
- Credentials are not copied into the invocation envelope.
- Harness does not import the SDK executor. GRPC remains uninstalled.

## Automated acceptance map

- `test_phase39_sdk_transport.py` covers envelope encoding, echo round-trip, remote
  fail-closed, unknown connector/method, Gateway invocation, seeded catalog, and
  AST import bans.
- Registry tests reject pip/module SDK manifest shapes.

## Human review checklist

- Confirm operators do not treat `sdk.development.echo` as a production SDK.
- Confirm pip/importlib installs remain absent.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
