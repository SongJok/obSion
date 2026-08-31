# ADR 0023: WORKFLOW Gateway dispatch reuses AutomationService

- Status: Accepted
- Date: 2026-08-29

## Context

Phase 42 installed WORKFLOW as an in-process Capability Gateway transport that only
echoed `obsion.development.echo`. ADR 0021 deferred binding to
`AutomationService.trigger_workflow` because a Capability Step could start nested
ANALYSIS Runs without a recursion budget. goal.txt lists Workflow as a capability
kind. Pointing the Gateway at Temporal or Airflow would fake an integration.

## Decision

A `workflow-development` connector may set `workflow_id` to a published
`WorkflowDefinition` UUID. The in-process executor then encodes
`obsion.automation.trigger` and calls `AutomationService.trigger_workflow` on the
Capability Gateway session with `AutomationTrigger.CAPABILITY`. Idempotency is
`capability:{run_id}:{step_id}:{workflow_id}`. Policy still requires
`automation.trigger` and workflow ownership.

If the current Run is already an automation ANALYSIS child
(`automation_step_executions.run_id`), dispatch raises `budget_exceeded`
(`workflow_dispatch_depth`). Depth is one: Gateway → AutomationExecution, never
Gateway → ANALYSIS Run → Gateway → AutomationExecution.

Connectors without `workflow_id` remain the development echo. Remote engines,
non-empty egress, and Temporal/Airflow configuration keys still fail closed.
No shipped AgentSpec declares `workflow.automation.trigger`. This is not a second
orchestrator.

## Consequences

Operators can bind a versioned WORKFLOW capability to a specific published workflow
through the same Gateway path as other transports. Nested workflow graphs and remote
engines remain unimplemented. Vendor IM HTTP remains unimplemented.
