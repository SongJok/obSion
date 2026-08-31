# Phase 44 WORKFLOW Gateway dispatch review

## Review question

Can a WORKFLOW capability with connector `workflow_id` create an
`AutomationExecution` through the existing AutomationService, produce Evidence, and
fail closed on nested ANALYSIS dispatch and remote engines—without Temporal/Airflow
or a second orchestrator?

**Status: PENDING — automated checks do not constitute production, staging, or
security approval.**

## Delivery contract

- `create_automation_dispatch_handler` is registered for `workflow-development`.
- Connector `workflow_id` calls `AutomationService.trigger_workflow` with
  `trigger=CAPABILITY` on the Gateway session.
- Dispatch from an automation ANALYSIS child Run returns `budget_exceeded`.
- Connectors without `workflow_id` still echo `obsion.development.echo`.
- Remote engines, URLs, and non-empty egress still fail closed.
- No shipped AgentSpec declares `workflow.automation.trigger`.
- Harness and AutomationWorker do not import the executor.

## Automated acceptance map

- `test_phase44_workflow_dispatch.py` covers envelope encoding, mock dispatch,
  missing session, nested budget, echo fallback, seeded catalog, AgentSpec
  exclusion, HTTP invoke creating an execution, and AST import bans.
- Error origin sinks in `error_producer_manifest.py` cover new workflow.py sites.

## Human review checklist

- Confirm operators do not treat Gateway dispatch as Temporal/Airflow.
- Confirm conversational agents still cannot pick this capability.
- Staging deploy and security sign-off remain operator-owned from Phase 25.
