"""In-process WORKFLOW transport for the Capability Gateway.

WORKFLOW is a protocol behind the gateway, not a second orchestrator. This adapter
encodes a workflow invocation envelope and dispatches to registered in-process
handlers. Remote engines (Temporal, Airflow, and similar) are not implemented.
A connector `workflow_id` binds to `AutomationService.trigger_workflow` with a
depth-1 recursion budget: automation ANALYSIS child Runs cannot start another
workflow execution.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select

from obsion.automation.service import AutomationService
from obsion.capabilities.connectors import ConnectorContext, ConnectorResult
from obsion.common.errors import BudgetExceededError, ObsionError, ValidationError
from obsion.db.models import AutomationStepExecution, Connector
from obsion.domain.enums import AutomationTrigger

DEVELOPMENT_CONNECTOR_TYPE = "workflow-development"
DEVELOPMENT_WORKFLOW = "obsion.development"
DEVELOPMENT_OPERATION = "echo"
DISPATCH_WORKFLOW = "obsion.automation"
DISPATCH_OPERATION = "trigger"
REMOTE_UNAVAILABLE_MESSAGE = "Remote workflow engines and process spawn are not implemented"
_UNKNOWN_OPERATION_MESSAGE = (
    "The in-process workflow adapter only exposes obsion.development.echo "
    "or a connector workflow_id trigger"
)

_REMOTE_CONFIG_KEYS = frozenset(
    {
        "airflow",
        "camunda",
        "command",
        "cron",
        "dagster",
        "docker",
        "endpoint",
        "host",
        "hostname",
        "n8n",
        "prefect",
        "server",
        "temporal",
        "url",
        "webhook",
        "zeebe",
    }
)

WorkflowHandler = Callable[[dict[str, Any], Connector, ConnectorContext], Awaitable[dict[str, Any]]]


def encode_workflow_call(
    *,
    workflow: str,
    operation: str,
    input_payload: Mapping[str, Any],
) -> dict[str, Any]:
    if not workflow.strip() or not operation.strip():
        raise ValidationError(
            "capability_input_invalid", "Workflow name and operation are required"
        )
    if not isinstance(input_payload, Mapping):
        raise ValidationError("capability_input_invalid", "Workflow input must be an object")
    return {"workflow": workflow, "operation": operation, "input": dict(input_payload)}


def configured_workflow_id(configuration: Mapping[str, Any]) -> UUID | None:
    raw = configuration.get("workflow_id")
    if raw is None or raw == "":
        return None
    try:
        return UUID(str(raw))
    except ValueError as exc:
        raise ValidationError(
            "capability_input_invalid", "Connector workflow_id must be a UUID"
        ) from exc


def create_development_echo_handler() -> WorkflowHandler:
    async def handler(
        input_payload: dict[str, Any],
        connector: Connector,
        context: ConnectorContext,
    ) -> dict[str, Any]:
        del connector
        return {
            "protocol": "workflow",
            "adapter": "in-process",
            "workflow": DEVELOPMENT_WORKFLOW,
            "operation": DEVELOPMENT_OPERATION,
            "echo": dict(input_payload),
            "run_id": str(context.run_id),
        }

    return handler


def create_automation_dispatch_handler(
    *,
    service: AutomationService | None = None,
) -> WorkflowHandler:
    automation = service or AutomationService()
    echo = create_development_echo_handler()

    async def handler(
        input_payload: dict[str, Any],
        connector: Connector,
        context: ConnectorContext,
    ) -> dict[str, Any]:
        configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
        workflow_id = configured_workflow_id(configuration)
        if workflow_id is None:
            return await echo(input_payload, connector, context)
        if context.session is None:
            raise ValidationError(
                "capability_input_invalid",
                "Workflow dispatch requires a Capability Gateway session",
            )
        nested = await context.session.scalar(
            select(AutomationStepExecution.id).where(
                AutomationStepExecution.organization_id == context.principal.organization_id,
                AutomationStepExecution.run_id == context.run_id,
            )
        )
        if nested is not None:
            raise BudgetExceededError("workflow_dispatch_depth", 1)
        step_token = context.step_id or context.run_id
        execution = await automation.trigger_workflow(
            context.session,
            context.principal,
            workflow_id,
            input_payload=dict(input_payload),
            idempotency_key=f"capability:{context.run_id}:{step_token}:{workflow_id}",
            trigger=AutomationTrigger.CAPABILITY,
        )
        return {
            "protocol": "workflow",
            "adapter": "in-process",
            "workflow": DISPATCH_WORKFLOW,
            "operation": DISPATCH_OPERATION,
            "dispatched": True,
            "execution_id": str(execution.id),
            "status": execution.status.value,
            "workflow_id": str(workflow_id),
            "run_id": str(context.run_id),
        }

    return handler


class DevelopmentWorkflowExecutor:
    def __init__(self) -> None:
        self._handlers: dict[str, WorkflowHandler] = {}

    def register(self, connector_type: str, handler: WorkflowHandler) -> None:
        if connector_type in self._handlers:
            raise ValueError(f"Workflow connector handler already registered: {connector_type}")
        self._handlers[connector_type] = handler

    async def invoke(
        self,
        connector: Connector,
        payload: dict[str, Any],
        credential: str | None,
        context: ConnectorContext,
    ) -> ConnectorResult:
        del credential
        if connector.endpoint or connector.allowed_egress:
            raise ObsionError("capability_transport_unavailable", REMOTE_UNAVAILABLE_MESSAGE)
        configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
        config_keys = {str(key).casefold() for key in configuration}
        if config_keys & _REMOTE_CONFIG_KEYS:
            raise ObsionError("capability_transport_unavailable", REMOTE_UNAVAILABLE_MESSAGE)
        handler = self._handlers.get(connector.connector_type)
        if handler is None:
            raise ValidationError(
                "connector_handler_missing",
                "No workflow handler is registered for this connector type",
                connector_type=connector.connector_type,
            )
        dispatch_id = configured_workflow_id(configuration)
        workflow = payload.get("workflow")
        if not isinstance(workflow, str) or not workflow.strip():
            configured = configuration.get("workflow")
            workflow = (
                configured
                if isinstance(configured, str) and configured.strip()
                else (DISPATCH_WORKFLOW if dispatch_id is not None else DEVELOPMENT_WORKFLOW)
            )
        operation = payload.get("operation")
        if not isinstance(operation, str) or not operation.strip():
            configured_operation = configuration.get("operation")
            operation = (
                configured_operation
                if isinstance(configured_operation, str) and configured_operation.strip()
                else (DISPATCH_OPERATION if dispatch_id is not None else DEVELOPMENT_OPERATION)
            )
        if dispatch_id is not None:
            workflow = DISPATCH_WORKFLOW
            operation = DISPATCH_OPERATION
        elif workflow != DEVELOPMENT_WORKFLOW or operation != DEVELOPMENT_OPERATION:
            raise ValidationError("capability_input_invalid", _UNKNOWN_OPERATION_MESSAGE)
        input_payload = payload.get("input")
        if input_payload is None:
            input_payload = {
                key: value for key, value in payload.items() if key not in {"workflow", "operation"}
            }
        if not isinstance(input_payload, dict):
            raise ValidationError("capability_input_invalid", "Workflow input must be an object")
        request = encode_workflow_call(
            workflow=workflow, operation=operation, input_payload=input_payload
        )
        data = await handler(request["input"], connector, context)
        resource_name = (
            f"{DISPATCH_WORKFLOW}.{DISPATCH_OPERATION}"
            if dispatch_id is not None
            else f"{DEVELOPMENT_WORKFLOW}.{DEVELOPMENT_OPERATION}"
        )
        return ConnectorResult(
            data=data,
            source=connector.name,
            resource=f"workflow://{connector.name}/{resource_name}",
            observed_at=datetime.now().astimezone(),
        )
