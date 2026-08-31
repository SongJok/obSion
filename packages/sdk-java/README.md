# Obsion Java SDK

REST client for the Obsion Python control plane. This is not a second Harness, App
Server, or backend runtime.

## Requirements

- JDK 21 or later (`javac --release 21`)
- Maven 3.9+ (or `./mvnw`)

## Usage

```java
try (dev.obsion.sdk.ObsionClient client =
        new dev.obsion.sdk.ObsionClient("http://127.0.0.1:8080", token)) {
  client.createWorkspace("platform");
  client.publishStudioAgent(agentYaml);
  client.publishStudioSkill(skillYaml);
  client.createConnector(Map.of(
      "name", "obsion-workflow-dispatch-test",
      "connector_type", "workflow-development",
      "environment", "development",
      "status", "ACTIVE",
      "declared_grants", List.of("automation.trigger"),
      "allowed_egress", List.of()));
  client.bindCapability(capabilityId, connectorId, "development");
  client.listOperatorInvocations("UNKNOWN", 25);
}
```

Capability *definitions* are seeded in the control plane. Creating a Capability here
means binding an existing definition to a Connector. Invoke still enters the Python
Capability Gateway. The Operator invocation listing is reconciliation metadata only;
the control plane never returns stored input or terminal result content through it.

## Tests

```bash
./mvnw -B test
```
