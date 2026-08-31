"""In-process MCP transport for the Capability Gateway.

MCP is a protocol behind the gateway, not an architecture and not a process
supervisor. This adapter encodes `tools/call` as JSON-RPC 2.0 and dispatches to
registered in-process handlers. Remote URLs, stdio servers, and command spawn are
not implemented.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any

from obsion.capabilities.connectors import ConnectorContext, ConnectorResult
from obsion.common.errors import ObsionError, ValidationError
from obsion.db.models import Connector

JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2024-11-05"
DEVELOPMENT_CONNECTOR_TYPE = "mcp-development"
DEVELOPMENT_TOOL = "obsion.echo"
REMOTE_UNAVAILABLE_MESSAGE = "MCP process spawn and remote endpoints are not implemented"

_REMOTE_CONFIG_KEYS = frozenset(
    {
        "args",
        "base_url",
        "baseurl",
        "command",
        "cwd",
        "docker",
        "env",
        "http_url",
        "httpurl",
        "npx",
        "server",
        "server_url",
        "serverurl",
        "sse",
        "stdio",
        "url",
    }
)

McpToolHandler = Callable[[dict[str, Any], Connector, ConnectorContext], Awaitable[dict[str, Any]]]


def encode_tools_call(
    *,
    request_id: str,
    name: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    if not name.strip():
        raise ValidationError("capability_input_invalid", "MCP tool name is required")
    if not isinstance(arguments, Mapping):
        raise ValidationError("capability_input_invalid", "MCP tool arguments must be an object")
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": dict(arguments)},
    }


def decode_tools_result(response: Mapping[str, Any]) -> dict[str, Any]:
    if response.get("jsonrpc") != JSONRPC_VERSION:
        raise ValidationError("capability_output_invalid", "MCP JSON-RPC version is invalid")
    if "error" in response:
        raise ValidationError("capability_output_invalid", "MCP tool call returned an error")
    result = response.get("result")
    if not isinstance(result, dict) or result.get("isError") is True:
        raise ValidationError("capability_output_invalid", "MCP tool result is invalid")
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return dict(structured)
    content = result.get("content")
    if not isinstance(content, list) or not content:
        raise ValidationError("capability_output_invalid", "MCP tool result is invalid")
    first = content[0]
    if not isinstance(first, dict) or not isinstance(first.get("text"), str):
        raise ValidationError("capability_output_invalid", "MCP tool result is invalid")
    return {"text": first["text"]}


def create_development_echo_handler() -> McpToolHandler:
    async def handler(
        arguments: dict[str, Any],
        connector: Connector,
        context: ConnectorContext,
    ) -> dict[str, Any]:
        del connector
        return {
            "protocol": "mcp",
            "protocol_version": MCP_PROTOCOL_VERSION,
            "adapter": "in-process",
            "tool": DEVELOPMENT_TOOL,
            "echo": dict(arguments),
            "run_id": str(context.run_id),
        }

    return handler


class DevelopmentMcpExecutor:
    def __init__(self) -> None:
        self._handlers: dict[str, McpToolHandler] = {}

    def register(self, connector_type: str, handler: McpToolHandler) -> None:
        if connector_type in self._handlers:
            raise ValueError(f"MCP connector handler already registered: {connector_type}")
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
                "No MCP handler is registered for this connector type",
                connector_type=connector.connector_type,
            )
        name = payload.get("name")
        if not isinstance(name, str) or not name.strip():
            configured = configuration.get("tool")
            name = (
                configured
                if isinstance(configured, str) and configured.strip()
                else DEVELOPMENT_TOOL
            )
        if name != DEVELOPMENT_TOOL:
            raise ValidationError(
                "capability_input_invalid",
                "The in-process MCP adapter only exposes obsion.echo",
            )
        arguments = payload.get("arguments")
        if arguments is None:
            arguments = {key: value for key, value in payload.items() if key != "name"}
        if not isinstance(arguments, dict):
            raise ValidationError(
                "capability_input_invalid",
                "MCP tool arguments must be an object",
            )
        request = encode_tools_call(
            request_id=str(context.run_id),
            name=name,
            arguments=arguments,
        )
        structured = await handler(request["params"]["arguments"], connector, context)
        response = {
            "jsonrpc": JSONRPC_VERSION,
            "id": request["id"],
            "result": {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(structured, default=str, sort_keys=True),
                    }
                ],
                "structuredContent": structured,
                "isError": False,
            },
        }
        return ConnectorResult(
            data=decode_tools_result(response),
            source=connector.name,
            resource=f"mcp://{connector.name}/{DEVELOPMENT_TOOL}",
            observed_at=datetime.now().astimezone(),
        )
