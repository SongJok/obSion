# Phase 42 WORKFLOW in-process transport review

## Review question

Can a WORKFLOW capability execute only through the Capability Gateway as an
in-process invocation envelope, produce Evidence, and fail closed on Temporal,
Airflow, remote URLs, and non-empty egress—without a second orchestrator or nested
Harness loop?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `DevelopmentWorkflowExecutor` is registered for `CapabilityTransport.WORKFLOW`.
- In-process `workflow-development` handles `obsion.development.echo`.
- Connector `endpoint`, `allowed_egress`, and temporal/airflow/url configuration
  fail closed with `capability_transport_unavailable`.
- WORKFLOW manifests cannot declare remote engine shapes.
- Credentials are not copied into the invocation envelope.
- Harness and AutomationWorker do not import the executor. No shipped AgentSpec
  declares it.
- Existing INTERNAL/HTTP/MCP/SDK/GRPC/SQL_PROXY transports are unchanged.

## Automated acceptance map

- `test_phase42_workflow_transport.py` covers envelope encoding, echo round-trip,
  remote fail-closed, unknown connector/operation, Gateway invocation, seeded
  catalog, AgentSpec exclusion, and AST import bans.
- Registry tests reject Temporal/Airflow WORKFLOW manifest shapes.
- Error origin sinks in `error_producer_manifest.py` cover `workflow.py`.

## Human review checklist

- Confirm operators do not treat `workflow.development.echo` as the automation
  engine. Published workflows still run through `/workflows` and AutomationWorker.
- Confirm Temporal/Airflow remain absent.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
