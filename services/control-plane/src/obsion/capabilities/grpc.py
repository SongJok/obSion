"""In-process gRPC transport for the Capability Gateway.

gRPC is a protocol behind the gateway, not a remote channel supervisor. This adapter
encodes a unary invocation envelope and dispatches to registered in-process handlers.
Remote hosts, TLS channels, protobuf codecs, and process spawn are not implemented.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime
from typing import Any

from obsion.capabilities.connectors import ConnectorContext, ConnectorResult
from obsion.common.errors import ObsionError, ValidationError
from obsion.db.models import Connector

DEVELOPMENT_CONNECTOR_TYPE = "grpc-development"
DEVELOPMENT_SERVICE = "obsion.development.Echo"
DEVELOPMENT_METHOD = "Ping"
REMOTE_UNAVAILABLE_MESSAGE = "gRPC remote channels and process spawn are not implemented"

_REMOTE_CONFIG_KEYS = frozenset(
    {
        "address",
        "authority",
        "cert",
        "certificate",
        "channel",
        "docker",
        "grpclib",
        "grpcio",
        "grpcurl",
        "host",
        "hostname",
        "insecure",
        "interceptors",
        "keepalive",
        "listen",
        "port",
        "proto",
        "protobuf",
        "protoc",
        "server",
        "socket",
        "ssl",
        "stub",
        "target",
        "tls",
        "unix",
        "uri",
        "url",
    }
)

GrpcMethodHandler = Callable[
    [dict[str, Any], Connector, ConnectorContext], Awaitable[dict[str, Any]]
]


def encode_grpc_call(
    *,
    service: str,
    method: str,
    message: Mapping[str, Any],
) -> dict[str, Any]:
    if not service.strip() or not method.strip():
        raise ValidationError("capability_input_invalid", "gRPC service and method are required")
    if not isinstance(message, Mapping):
        raise ValidationError("capability_input_invalid", "gRPC message must be an object")
    return {"service": service, "method": method, "message": dict(message)}


def create_development_echo_handler() -> GrpcMethodHandler:
    async def handler(
        message: dict[str, Any],
        connector: Connector,
        context: ConnectorContext,
    ) -> dict[str, Any]:
        del connector
        return {
            "protocol": "grpc",
            "adapter": "in-process",
            "service": DEVELOPMENT_SERVICE,
            "method": DEVELOPMENT_METHOD,
            "echo": dict(message),
            "run_id": str(context.run_id),
        }

    return handler


class DevelopmentGrpcExecutor:
    def __init__(self) -> None:
        self._handlers: dict[str, GrpcMethodHandler] = {}

    def register(self, connector_type: str, handler: GrpcMethodHandler) -> None:
        if connector_type in self._handlers:
            raise ValueError(f"gRPC connector handler already registered: {connector_type}")
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
                "No gRPC handler is registered for this connector type",
                connector_type=connector.connector_type,
            )
        service = payload.get("service")
        if not isinstance(service, str) or not service.strip():
            configured = configuration.get("service")
            service = (
                configured
                if isinstance(configured, str) and configured.strip()
                else DEVELOPMENT_SERVICE
            )
        method = payload.get("method")
        if not isinstance(method, str) or not method.strip():
            configured_method = configuration.get("method")
            method = (
                configured_method
                if isinstance(configured_method, str) and configured_method.strip()
                else DEVELOPMENT_METHOD
            )
        if service != DEVELOPMENT_SERVICE or method != DEVELOPMENT_METHOD:
            raise ValidationError(
                "capability_input_invalid",
                "The in-process gRPC adapter only exposes obsion.development.Echo/Ping",
            )
        message = payload.get("message")
        if message is None:
            message = {
                key: value for key, value in payload.items() if key not in {"service", "method"}
            }
        if not isinstance(message, dict):
            raise ValidationError("capability_input_invalid", "gRPC message must be an object")
        request = encode_grpc_call(service=service, method=method, message=message)
        data = await handler(request["message"], connector, context)
        return ConnectorResult(
            data=data,
            source=connector.name,
            resource=f"grpc://{connector.name}/{DEVELOPMENT_SERVICE}/{DEVELOPMENT_METHOD}",
            observed_at=datetime.now().astimezone(),
        )
