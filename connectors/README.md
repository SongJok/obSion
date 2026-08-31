# Connector manifests

Connector manifests describe deployment-specific transports and grants. They never
contain credential values: `credentialRef` points to a process secret injected by a
deployment secret manager, and the Capability Gateway resolves it only inside connector
execution. Additional credential providers can implement the broker contract.

The repository includes the internal knowledge index (including ACL-filtered
ticket search), Feishu docs (`feishu-docs`), DingTalk docs (`dingtalk-docs`), WeCom docs (`wecom-docs`) and Confluence Cloud (`confluence`) Knowledge sources, the internal Code Graph index, in-process MCP, SDK, gRPC, WORKFLOW,
AGENT, and Connector SDK development adapters, and contract examples for a read-only PostgreSQL source, an
OpenTelemetry-compatible observability API, and a read-only engineering metadata
gateway spanning source control, delivery, configuration, and Kubernetes status.
Import or provision manifests through the administration API, bind only the
required capability versions, and restrict egress at both connector and
network-policy layers.

Import examples live under `connectors/examples/`. Knowledge and Code Graph indexes are
INTERNAL. `mcp-development.yaml`, `sdk-development.yaml`, `grpc-development.yaml`,
`workflow-development.yaml`, `workflow-automation.yaml`, `agent-development.yaml`,
and `connector-sdk-development.yaml`
are in-process only: command, module, pip, host, temporal, harness, URL, and
non-empty egress fail closed. `connector-sdk-development.yaml` hosts the Python
Connector SPI (`health` / `discover` / `execute`); it is not a package installer. Plugin manifests
must declare network, filesystem, capabilities, secrets, and risk. Production
signatures are HMAC-SHA256; pip, importlib, and remote URL load fail closed.
`workflow-automation.yaml` binds a published
WorkflowDefinition UUID through `AutomationService`; it is not Temporal/Airflow.
Observability and engineering HTTP examples are
read-only contracts; they are not live vendor adapters until a tenant binds an
egress-allowlisted endpoint and a secret reference. See the
[administrator guide](../docs/operators/administrator.md).
The consolidated vendor origin, credential-name, rollout, and rollback matrix is in
the [0.75.0-dev release notes](../docs/release/0.75.0-dev.md).

All query identities must be read-only. V1 rejects mutating SQL and all L3-L5 actions
even when a connector or external system would otherwise allow them.
