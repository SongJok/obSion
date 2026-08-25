# Connector manifests

Connector manifests describe deployment-specific transports and grants. They never
contain credential values: `credentialRef` points to a process secret injected by a
deployment secret manager, and the Capability Gateway resolves it only inside connector
execution. Additional credential providers can implement the broker contract.

The repository includes the internal knowledge index and contract examples for a
read-only PostgreSQL source, an OpenTelemetry-compatible observability API, and a
read-only engineering metadata gateway spanning source control, delivery,
configuration, and Kubernetes status. Import or provision manifests through the
administration API, bind only the required capability versions, and restrict egress at
both connector and network-policy layers.

All query identities must be read-only. V1 rejects mutating SQL and all L3-L5 actions
even when a connector or external system would otherwise allow them.
