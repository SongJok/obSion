# ADR 0021: WORKFLOW is an in-process Capability Gateway transport

- Status: Accepted
- Date: 2026-08-29

## Context

`CapabilityTransport.WORKFLOW` existed in the enum and registry with no executor.
Invoking a workflow capability returned `capability_transport_unavailable`. goal.txt
lists Workflow beside Tool, MCP, API, SDK, and Agent as a capability kind. Pointing
the Gateway at Temporal, Airflow, or a subprocess orchestrator would fake an
integration and create a second runtime. The existing AutomationService already
executes governed workflows on a separate API path.

## Decision

Register `DevelopmentWorkflowExecutor` for `CapabilityTransport.WORKFLOW`. It encodes
`{workflow, operation, input}` and dispatches to in-process handlers keyed by
`connector_type`. The development adapter exposes `obsion.development.echo` on
connector type `workflow-development`.

Connector endpoint, non-empty egress, and configuration keys that imply a remote
engine (`temporal`, `airflow`, `prefect`, `dagster`, `n8n`, `url`, `host`, and
similar) fail closed with `capability_transport_unavailable`. Registry manifests
with `transport: WORKFLOW` are rejected if they declare those shapes. Connector
credentials are not copied into the invocation envelope. Harness and
`AutomationWorker` do not import the executor. No AgentSpec declares the
development echo capability.

This is not a second orchestrator. The automation API remains the engine for
published WorkflowSpec executions. AGENT transport remains uninstalled.

## Consequences

Operators can bind a versioned WORKFLOW capability through the same Gateway path as
other transports. Remote workflow engines remain unimplemented until a later ADR
with a real tenant engine and allowlist. Binding Gateway dispatch to
`AutomationService.trigger_workflow` is deferred so a Capability Step cannot start
nested ANALYSIS Runs without an explicit recursion budget. Vendor IM HTTP remains
unimplemented.
