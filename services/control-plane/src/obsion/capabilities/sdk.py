"""In-process SDK transport for the Capability Gateway.

SDK is a protocol behind the gateway, not a package installer. This adapter encodes
a typed invocation envelope and dispatches to registered in-process handlers.
Remote URLs, dynamic imports, and pip/wheel installs are not implemented.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any

from obsion.capabilities.connectors import ConnectorContext, ConnectorResult
from obsion.common.errors import ObsionError, ValidationError
from obsion.db.models import Connector

DEVELOPMENT_CONNECTOR_TYPE = "sdk-development"
DEVELOPMENT_SDK = "obsion.development"
DEVELOPMENT_METHOD = "echo"
REMOTE_UNAVAILABLE_MESSAGE = "SDK package install and remote endpoints are not implemented"

_REMOTE_CONFIG_KEYS = frozenset(
    {
        "args",
        "base_url",
        "baseurl",
        "class_name",
        "classname",
        "command",
        "entrypoint",
        "import",
        "module",
        "package",
        "pip",
        "pythonpath",
        "url",
        "wheel",
    }
)

SdkMethodHandler = Callable[
    [dict[str, Any], Connector, ConnectorContext], Awaitable[dict[str, Any]]
]


def encode_sdk_call(
    *,
    sdk: str,
    method: str,
    arguments: Mapping[str, Any],
) -> dict[str, Any]:
    if not sdk.strip() or not method.strip():
        raise ValidationError("capability_input_invalid", "SDK name and method are required")
    if not isinstance(arguments, Mapping):
        raise ValidationError("capability_input_invalid", "SDK arguments must be an object")
    return {"sdk": sdk, "method": method, "arguments": dict(arguments)}


def create_development_echo_handler() -> SdkMethodHandler:
    async def handler(
        arguments: dict[str, Any],
        connector: Connector,
        context: ConnectorContext,
    ) -> dict[str, Any]:
        del connector
        return {
            "protocol": "sdk",
            "adapter": "in-process",
            "sdk": DEVELOPMENT_SDK,
            "method": DEVELOPMENT_METHOD,
            "echo": dict(arguments),
            "run_id": str(context.run_id),
        }

    return handler


class DevelopmentSdkExecutor:
    def __init__(self) -> None:
        self._handlers: dict[str, SdkMethodHandler] = {}

    def register(self, connector_type: str, handler: SdkMethodHandler) -> None:
        if connector_type in self._handlers:
            raise ValueError(f"SDK connector handler already registered: {connector_type}")
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
                "No SDK handler is registered for this connector type",
                connector_type=connector.connector_type,
            )
        sdk = payload.get("sdk")
        if not isinstance(sdk, str) or not sdk.strip():
            configured = configuration.get("sdk")
            sdk = (
                configured
                if isinstance(configured, str) and configured.strip()
                else DEVELOPMENT_SDK
            )
        method = payload.get("method")
        if not isinstance(method, str) or not method.strip():
            configured_method = configuration.get("method")
            method = (
                configured_method
                if isinstance(configured_method, str) and configured_method.strip()
                else DEVELOPMENT_METHOD
            )
        if sdk != DEVELOPMENT_SDK or method != DEVELOPMENT_METHOD:
            raise ValidationError(
                "capability_input_invalid",
                "The in-process SDK adapter only exposes obsion.development.echo",
            )
        arguments = payload.get("arguments")
        if arguments is None:
            arguments = {
                key: value for key, value in payload.items() if key not in {"sdk", "method"}
            }
        if not isinstance(arguments, dict):
            raise ValidationError("capability_input_invalid", "SDK arguments must be an object")
        request = encode_sdk_call(sdk=sdk, method=method, arguments=arguments)
        data = await handler(request["arguments"], connector, context)
        return ConnectorResult(
            data=data,
            source=connector.name,
            resource=f"sdk://{connector.name}/{DEVELOPMENT_SDK}.{DEVELOPMENT_METHOD}",
            observed_at=datetime.now().astimezone(),
        )
