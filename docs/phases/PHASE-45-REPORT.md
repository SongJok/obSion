# PHASE-45-REPORT — Core SDKs (Java REST client)

## What was implemented

Phase 45 adds a first-party Java SDK and closes the Connector/Capability-bind gap in
the existing Python and TypeScript clients.

- `packages/sdk-java` (`dev.obsion.sdk.ObsionClient`) calls the Python control plane
  over REST. JDK `HttpClient`, stdlib JSON, JUnit 5, `--release 21`.
- Surfaces: workspaces/threads/turns, Studio Agent/Skill, list/create Connector,
  list/bind Capability, list/invoke Capability. Errors are `ObsionApiException`.
- Python `AsyncObsionClient` and TypeScript `ObsionClient` wrap the same admin
  Connector and Capability-binding routes.
- ADR 0024 records that this is not a second Harness or Java control plane.
- CI runs Maven tests on Temurin 21. Vendor IM HTTP is still not implemented.

## Architecture decisions

Creating a Capability in V1 means binding a seeded CapabilityVersion to a Connector.
Studio YAML still creates Agent and Skill versions. The Java SDK does not speak the
App Server WebSocket protocol. A developer Connector SPI remains a later phase;
Gateway already supplies Auth, Audit, Timeout, Retry, Metrics, and Tracing to
installed executors.

## Validation

- Architecture AST: Java sources contain no Spring, servlet, gRPC, Kafka, ClickHouse,
  WebSocket, or Harness tokens.
- JUnit loopback tests cover bearer tokens, structured errors, Studio, Connector
  create, Capability bind, and invoke routes.
- Python and TypeScript SDK tests cover the new admin wrappers.
- Workbench composer still has one prompt and no Agent picker.

## Remaining risks

- Public IM webhook hosting, WeCom AES decrypt, and vendor HTTP POST still require
  a real tenant application.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
- Signed `1.0.0` remains operator-owned.
- A Connector SDK (health/discover/execute for third-party authors) is not this
  phase.
