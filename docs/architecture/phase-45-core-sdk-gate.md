# Phase 45 core SDK review

## Review question

Can company teams create Agent, Skill, Connector, and Capability bindings through
first-party Java, Python, and TypeScript SDKs without a second Harness or a Java
control plane?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `packages/sdk-java` is a JDK 21 REST client (`dev.obsion.sdk.ObsionClient`).
- Java sources must not contain Spring, servlet, gRPC, Kafka, ClickHouse, WebSocket,
  or Harness tokens.
- Studio routes publish Agent/Skill. Admin routes create Connector and bind
  Capability. Invoke stays on the Python Capability Gateway.
- Python and TypeScript SDKs wrap `GET/POST /api/v1/admin/connectors` and
  `POST /api/v1/admin/capabilities/{id}/bindings`.
- Capability *definitions* remain seeded; V1 "create Capability" is bind, not a
  second registry.
- Maven tests run in CI on Temurin 21. `mvn` is not required for Python-only local
  `make check` besides the architecture assertions.

## Automated acceptance map

- `packages/sdk-java` JUnit tests use a loopback `HttpServer` (no live cluster).
- `packages/sdk-python/tests/test_client.py` and `packages/sdk-ts/tests/client.mjs`
  cover connector create and capability bind.
- `services/control-plane/tests/test_phase45_core_sdk.py` forbids a second control
  plane and optionally runs Maven when the toolchain is present.

## Human review checklist

- Confirm operators do not treat the Java SDK as a Java backend.
- Confirm conversational UI still has no Agent picker.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
