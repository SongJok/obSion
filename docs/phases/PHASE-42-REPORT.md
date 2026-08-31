# PHASE-42-REPORT — WORKFLOW in-process Gateway transport

## What was implemented

Phase 42 installs WORKFLOW as a Capability Gateway transport. It is a protocol
behind the gateway, not a second orchestrator and not Temporal/Airflow.

- `DevelopmentWorkflowExecutor` encodes `{workflow, operation, input}` and
  dispatches to in-process handlers.
- Development connector `obsion-workflow-development` / capability
  `workflow.development.echo` is seeded for local catalog use. No AgentSpec
  declares it.
- Remote engines (Temporal, Airflow, Prefect, Dagster, n8n), URLs, and non-empty
  egress fail closed (`capability_transport_unavailable`). Registry manifests with
  those shapes are rejected.
- Connector credentials are not copied into the invocation envelope.
- ADR 0021 records the in-process boundary. Published WorkflowSpec executions still
  run through the automation API. No schema migration.

## Architecture decisions

WORKFLOW stays behind Policy, grants, rate limits, schema validation, Evidence, and
audit. This is not a nested Harness loop. AGENT transport remains uninstalled.
Vendor IM HTTP is still not implemented.

## Validation

- `uv run pytest --no-cov` — 551 passed, 18 opt-in PostgreSQL tests skipped,
  including `test_phase42_workflow_transport.py`.
- Architecture AST: `capabilities/workflow.py` does not import Harness,
  AutomationWorker, HTTP clients, or subprocess. `harness/runtime.py` and
  `automation/worker.py` do not import the workflow executor.
- Workbench 治理控制台 catalog copy includes `MCP/SDK/gRPC/Workflow 为进程内适配器`.
  Connector health lists `obsion-workflow-development`. Composer still has one
  prompt and no Agent picker.

## Remaining risks

- Binding Gateway dispatch to `AutomationService.trigger_workflow` would let a
  Capability Step start nested ANALYSIS Runs and needs an explicit recursion budget;
  it is not implemented here.
- Public IM webhook hosting, WeCom AES decrypt, and vendor HTTP POST still require
  a real tenant application.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
- Signed `1.0.0` remains operator-owned.
