# ADR 0024: Java SDK is a REST client of the Python control plane

- Status: Accepted
- Date: 2026-08-29

## Context

goal.txt requires Obsion Java, Python, and TypeScript SDKs so company teams can
create Capability, Skill, Agent, and Connector records. Python and TypeScript clients
already wrap REST and the App Server. Java teams had no first-party client. A Java
control plane, Spring service, or second Harness would violate the one-Python-backend
invariant.

Capability *definitions* remain seeded and versioned in the Python registry. Creating
a Capability in V1 means publishing an Agent/Skill (Studio) or binding an existing
CapabilityVersion to a Connector (`POST /api/v1/admin/capabilities/{id}/bindings`).
Connectors are created through `POST /api/v1/admin/connectors`. Execution still enters
the Capability Gateway.

## Decision

`packages/sdk-java` is a JDK 21 REST client of the existing Python control plane. It
uses `java.net.http.HttpClient` and a stdlib JSON codec. It does not embed Harness,
App Server WebSocket, Spring, gRPC, Kafka, or ClickHouse. Studio routes create Agent
and Skill versions. Admin routes create Connectors and bind Capabilities. Invoke
remains `POST /api/v1/capabilities/{name}/invoke`.

Python and TypeScript SDKs gain the same Connector create and Capability bind wrappers
so all three languages share the registry surface. The Java SDK does not implement a
Connector SPI in this phase.

## Consequences

Java services can register connectors and bind capabilities without a second backend.
App Server streaming stays on Python/TypeScript. Vendor IM HTTP remains unimplemented.
A developer Connector SDK (health/discover/execute with automatic Auth/Audit/Timeout)
is a later phase; Gateway already applies those controls to installed executors.
