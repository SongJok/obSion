"""In-process AGENT transport for the Capability Gateway.

AGENT is a protocol behind the gateway, not a second Harness. This adapter encodes
an agent invocation envelope and dispatches to registered in-process handlers.
Remote agent runtimes, nested Harness loops, and process spawn are not implemented.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any

from obsion.capabilities.connectors import ConnectorContext, ConnectorResult
from obsion.common.errors import ObsionError, ValidationError
from obsion.db.models import Connector

DEVELOPMENT_CONNECTOR_TYPE = "agent-development"
DEVELOPMENT_AGENT = "obsion.development"
DEVELOPMENT_OPERATION = "echo"
REMOTE_UNAVAILABLE_MESSAGE = (
    "Remote agent runtimes, nested Harness loops, and process spawn are not implemented"
)

_REMOTE_CONFIG_KEYS = frozenset(
    {
        "autogen",
        "child_run",
        "command",
        "crewai",
        "docker",
        "endpoint",
        "harness",
        "host",
        "hostname",
        "langchain",
        "langgraph",
        "nested",
        "agents_sdk",
        "sidecar",
        "spawn",
        "subprocess",
        "url",
    }
)

AgentHandler = Callable[[dict[str, Any], Connector, ConnectorContext], Awaitable[dict[str, Any]]]


def encode_agent_call(
    *,
    agent: str,
    operation: str,
    input_payload: Mapping[str, Any],
) -> dict[str, Any]:
    if not agent.strip() or not operation.strip():
        raise ValidationError("capability_input_invalid", "Agent name and operation are required")
    if not isinstance(input_payload, Mapping):
        raise ValidationError("capability_input_invalid", "Agent input must be an object")
    return {"agent": agent, "operation": operation, "input": dict(input_payload)}


def create_development_echo_handler() -> AgentHandler:
    async def handler(
        input_payload: dict[str, Any],
        connector: Connector,
        context: ConnectorContext,
    ) -> dict[str, Any]:
        del connector
        return {
            "protocol": "agent",
            "adapter": "in-process",
            "agent": DEVELOPMENT_AGENT,
            "operation": DEVELOPMENT_OPERATION,
            "echo": dict(input_payload),
            "run_id": str(context.run_id),
        }

    return handler


class DevelopmentAgentExecutor:
    def __init__(self) -> None:
        self._handlers: dict[str, AgentHandler] = {}

    def register(self, connector_type: str, handler: AgentHandler) -> None:
        if connector_type in self._handlers:
            raise ValueError(f"Agent connector handler already registered: {connector_type}")
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
                "No agent handler is registered for this connector type",
                connector_type=connector.connector_type,
            )
        agent = payload.get("agent")
        if not isinstance(agent, str) or not agent.strip():
            configured = configuration.get("agent")
            agent = (
                configured
                if isinstance(configured, str) and configured.strip()
                else DEVELOPMENT_AGENT
            )
        operation = payload.get("operation")
        if not isinstance(operation, str) or not operation.strip():
            configured_operation = configuration.get("operation")
            operation = (
                configured_operation
                if isinstance(configured_operation, str) and configured_operation.strip()
                else DEVELOPMENT_OPERATION
            )
        if agent != DEVELOPMENT_AGENT or operation != DEVELOPMENT_OPERATION:
            raise ValidationError(
                "capability_input_invalid",
                "The in-process agent adapter only exposes obsion.development.echo",
            )
        input_payload = payload.get("input")
        if input_payload is None:
            input_payload = {
                key: value for key, value in payload.items() if key not in {"agent", "operation"}
            }
        if not isinstance(input_payload, dict):
            raise ValidationError("capability_input_invalid", "Agent input must be an object")
        request = encode_agent_call(agent=agent, operation=operation, input_payload=input_payload)
        data = await handler(request["input"], connector, context)
        return ConnectorResult(
            data=data,
            source=connector.name,
            resource=f"agent://{connector.name}/{DEVELOPMENT_AGENT}.{DEVELOPMENT_OPERATION}",
            observed_at=datetime.now().astimezone(),
        )
