# PHASE-44-REPORT — WORKFLOW Gateway dispatch

## What was implemented

Phase 44 binds the in-process WORKFLOW transport to the existing automation engine.

- A `workflow-development` connector with `workflow_id` calls
  `AutomationService.trigger_workflow` (`AutomationTrigger.CAPABILITY`) on the
  Capability Gateway session.
- Capability `workflow.automation.trigger` is seeded for catalog use. No AgentSpec
  declares it. Builtin `general-agent` also excludes in-process adapter capabilities.
- Dispatch from an automation ANALYSIS child Run fails closed (`budget_exceeded`,
  budget `workflow_dispatch_depth`).
- Connectors without `workflow_id` remain `obsion.development.echo`.
- Remote engines, URLs, and non-empty egress still fail closed.
- ADR 0023 records the recursion budget. No schema migration: `AutomationTrigger` is
  a non-native VARCHAR enum.

## Architecture decisions

This is not a second orchestrator. Published WorkflowSpec executions still run
through AutomationWorker. Nested workflow graphs and Temporal/Airflow are not
implemented. Vendor IM HTTP is still not implemented.

## Validation

- `uv run pytest --no-cov` — 570 passed, 18 opt-in PostgreSQL tests skipped,
  including `test_phase44_workflow_dispatch.py`.
- Architecture AST: `capabilities/workflow.py` does not import Harness,
  AutomationWorker, HTTP clients, or subprocess.
- Workbench composer still has one prompt and no Agent picker.

## Remaining risks

- Gateway dispatch and a scheduled trigger can still overlap under `ALLOW`
  concurrency; that is the existing workflow policy.
- Public IM webhook hosting, WeCom AES decrypt, and vendor HTTP POST still require
  a real tenant application.
- Staging deploy and human security sign-off remain operator-owned from Phase 25.
- Signed `1.0.0` remains operator-owned.
