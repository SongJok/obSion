"""Author-facing Connector SPI.

Company teams implement ``health``, ``discover``, and ``execute``. The Obsion
control plane hosts registered in-process adapters behind the Capability Gateway,
which supplies Auth, Audit, Timeout, Retry, Metrics, and Tracing. This module is
not a second Harness, not a package installer, and not a remote plugin loader.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Protocol, runtime_checkable

ConnectorHealthStatus = Literal["ready", "degraded", "unavailable"]
PluginNetworkMode = Literal["deny", "gateway-only"]
PluginRiskLevel = Literal["L0", "L1", "L2", "L3", "L4", "L5"]

PLUGIN_RISK_LEVELS: tuple[PluginRiskLevel, ...] = ("L0", "L1", "L2", "L3", "L4", "L5")
PLUGIN_NETWORK_MODES: tuple[PluginNetworkMode, ...] = ("deny", "gateway-only")
PLUGIN_FILESYSTEM_MOUNTS: tuple[str, ...] = ("/workspace", "/repo", "/artifacts", "/tmp")  # noqa: S108
PLUGIN_SECRET_REF_PATTERN = re.compile(r"(?:env|secret)://[A-Za-z][A-Za-z0-9_.-]*")
PLUGIN_DECLARATION_FIELDS = frozenset({"capabilities", "filesystem", "network", "risk", "secrets"})

_SECRET_KEY_FRAGMENTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "dsn",
)
_REMOTE_KEY_FRAGMENTS = ("endpoint", "url", "host", "baseurl")


class ConnectorSdkError(Exception):
    """Adapter-raised failure with a stable machine code.

    The control plane maps this onto the frozen error catalog. Authors must not
    embed secrets in ``message``.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ConnectorInvocationContext:
    """Public invocation identity. Health and discover never receive credentials."""

    connector_name: str
    connector_type: str
    environment: str
    operation: str | None = None
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class ConnectorExecuteContext:
    """Execute-only context. ``credential`` is a temporary Gateway-resolved secret.

    Authors must not log, persist, or return it. The runtime destroys the handle
    after ``execute`` returns.
    """

    connector_name: str
    connector_type: str
    environment: str
    operation: str
    run_id: str | None = None
    credential: str | None = None


@dataclass(frozen=True, slots=True)
class ConnectorHealth:
    status: ConnectorHealthStatus
    checked_at: datetime | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DiscoveredOperation:
    name: str
    capability: str
    description: str
    risk: str
    side_effect: bool
    permission: str
    input_schema: Mapping[str, Any] = field(default_factory=dict)
    output_schema: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConnectorDiscovery:
    connector_type: str
    operations: tuple[DiscoveredOperation, ...]
    resources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConnectorExecuteRequest:
    operation: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ConnectorPluginDeclaration:
    """Author-declared plugin boundary. This is not a loadable package.

    Network is ``deny`` or ``gateway-only``. Filesystem mounts must be sandbox
    paths. Secrets are ``env://`` or ``secret://`` references. Risk L5 is never
    promotable. The control plane scans and HMAC-signs this object; it does not
    pip-install, importlib, or fetch a remote plugin.
    """

    risk: PluginRiskLevel
    network: PluginNetworkMode
    filesystem: tuple[str, ...]
    secrets: tuple[str, ...]
    capabilities: tuple[str, ...]


DEVELOPMENT_PLUGIN: dict[str, Any] = {
    "risk": "L1",
    "network": "deny",
    "filesystem": [],
    "secrets": [],
    "capabilities": ["connector.sdk.echo"],
}


@runtime_checkable
class ConnectorAdapter(Protocol):
    async def health(self, context: ConnectorInvocationContext) -> ConnectorHealth: ...

    async def discover(self, context: ConnectorInvocationContext) -> ConnectorDiscovery: ...

    async def execute(
        self,
        request: ConnectorExecuteRequest,
        context: ConnectorExecuteContext,
    ) -> Mapping[str, Any]: ...


def parse_plugin_declaration(raw: Mapping[str, Any]) -> ConnectorPluginDeclaration:
    extra = set(raw) - PLUGIN_DECLARATION_FIELDS
    if extra:
        raise ConnectorSdkError(
            "capability_input_invalid",
            "Plugin declaration cannot include fields beyond network, filesystem, "
            "capabilities, secrets, and risk",
        )
    risk = raw.get("risk")
    if risk not in PLUGIN_RISK_LEVELS:
        raise ConnectorSdkError("capability_input_invalid", "Plugin risk must be L0-L5")
    network = raw.get("network")
    if network not in PLUGIN_NETWORK_MODES:
        raise ConnectorSdkError(
            "capability_input_invalid",
            "Plugin network must be deny or gateway-only",
        )
    filesystem = _string_tuple(raw.get("filesystem"), "filesystem")
    secrets = _string_tuple(raw.get("secrets"), "secrets")
    capabilities = _string_tuple(raw.get("capabilities"), "capabilities")
    if not capabilities:
        raise ConnectorSdkError("capability_input_invalid", "Plugin capabilities are required")
    for mount in filesystem:
        if mount not in PLUGIN_FILESYSTEM_MOUNTS:
            raise ConnectorSdkError(
                "capability_input_invalid",
                "Plugin filesystem mounts are limited to /workspace, /repo, /artifacts, and /tmp",
            )
    for reference in secrets:
        if PLUGIN_SECRET_REF_PATTERN.fullmatch(reference) is None:
            raise ConnectorSdkError(
                "capability_input_invalid",
                "Plugin secrets must be env:// or secret:// references",
            )
    return ConnectorPluginDeclaration(
        risk=risk,
        network=network,
        filesystem=filesystem,
        secrets=secrets,
        capabilities=capabilities,
    )


def plugin_declaration_as_dict(declaration: ConnectorPluginDeclaration) -> dict[str, Any]:
    return {
        "risk": declaration.risk,
        "network": declaration.network,
        "filesystem": list(declaration.filesystem),
        "secrets": list(declaration.secrets),
        "capabilities": list(declaration.capabilities),
    }


def canonical_plugin_payload(declaration: ConnectorPluginDeclaration) -> bytes:
    payload = {
        "capabilities": sorted(declaration.capabilities),
        "filesystem": sorted(declaration.filesystem),
        "network": declaration.network,
        "risk": declaration.risk,
        "secrets": sorted(declaration.secrets),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def plugin_declaration_digest(declaration: ConnectorPluginDeclaration) -> str:
    return hashlib.sha256(canonical_plugin_payload(declaration)).hexdigest()


def sign_plugin_declaration(declaration: ConnectorPluginDeclaration, key: str) -> str:
    if not key.strip():
        raise ConnectorSdkError("capability_input_invalid", "Plugin signing key is required")
    return hmac.new(
        key.encode("utf-8"),
        canonical_plugin_payload(declaration),
        hashlib.sha256,
    ).hexdigest()


def verify_plugin_signature(
    declaration: ConnectorPluginDeclaration,
    signature: str,
    key: str,
) -> bool:
    expected = sign_plugin_declaration(declaration, key)
    return hmac.compare_digest(expected, signature.strip().casefold())


def _string_tuple(value: Any, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ConnectorSdkError(
            "capability_input_invalid",
            f"Plugin {field} must be an array of strings",
        )
    return tuple(item.strip() for item in value)


def parse_execute_request(
    payload: Mapping[str, Any],
    *,
    default_operation: str = "echo",
) -> ConnectorExecuteRequest:
    operation = payload.get("operation")
    if operation is None:
        operation = default_operation
    if not isinstance(operation, str) or not operation.strip():
        raise ConnectorSdkError("capability_input_invalid", "Connector operation is required")
    arguments = payload.get("arguments")
    if arguments is None:
        arguments = {key: value for key, value in payload.items() if key != "operation"}
    if not isinstance(arguments, Mapping):
        raise ConnectorSdkError("capability_input_invalid", "Connector arguments must be an object")
    return ConnectorExecuteRequest(operation=operation.strip(), arguments=dict(arguments))


def discovery_as_dict(discovery: ConnectorDiscovery) -> dict[str, Any]:
    return {
        "connector_type": discovery.connector_type,
        "operations": [
            {
                "name": item.name,
                "capability": item.capability,
                "description": item.description,
                "risk": item.risk,
                "side_effect": item.side_effect,
                "permission": item.permission,
                "input_schema": dict(item.input_schema),
                "output_schema": dict(item.output_schema),
            }
            for item in discovery.operations
        ],
        "resources": list(discovery.resources),
    }


def health_as_dict(health: ConnectorHealth, *, checked_at: datetime) -> dict[str, Any]:
    observed = health.checked_at or checked_at
    return {
        "status": health.status,
        "adapter": "connector-sdk",
        "checked_at": observed.isoformat(),
        "details": dict(health.details),
    }


def assert_no_forbidden_fields(value: Any, *, credential: str | None = None) -> None:
    """Reject credential material and remote targets in author-visible payloads."""
    _walk_forbidden(value, credential)


def _walk_forbidden(value: Any, credential: str | None) -> None:
    if credential and _contains_secret_text(value, credential):
        raise ConnectorSdkError(
            "capability_output_invalid",
            "Connector SDK results cannot include connector credentials",
        )
    if isinstance(value, Mapping):
        for key, item in value.items():
            lowered = str(key).casefold().replace("-", "_")
            if any(fragment in lowered for fragment in _SECRET_KEY_FRAGMENTS):
                raise ConnectorSdkError(
                    "capability_output_invalid",
                    "Connector SDK results cannot include secret fields",
                )
            if any(fragment in lowered for fragment in _REMOTE_KEY_FRAGMENTS):
                raise ConnectorSdkError(
                    "capability_output_invalid",
                    "Connector SDK results cannot include network targets",
                )
            _walk_forbidden(item, credential)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _walk_forbidden(item, credential)


def _contains_secret_text(value: Any, credential: str) -> bool:
    if isinstance(value, str):
        return credential in value
    if isinstance(value, Mapping):
        return any(_contains_secret_text(item, credential) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_secret_text(item, credential) for item in value)
    return False


class DevelopmentEchoConnector:
    """In-process reference adapter. Remote loading and pip install are not implemented."""

    CONNECTOR_TYPE = "connector-sdk-development"
    OPERATION = "echo"
    CAPABILITY = "connector.sdk.echo"
    PERMISSION = "connector.sdk.invoke"

    async def health(self, context: ConnectorInvocationContext) -> ConnectorHealth:
        del context
        return ConnectorHealth(status="ready", details={"protocol": "connector-sdk"})

    async def discover(self, context: ConnectorInvocationContext) -> ConnectorDiscovery:
        del context
        return ConnectorDiscovery(
            connector_type=self.CONNECTOR_TYPE,
            operations=(
                DiscoveredOperation(
                    name=self.OPERATION,
                    capability=self.CAPABILITY,
                    description="Echo arguments through the Connector SDK SPI.",
                    risk="L1",
                    side_effect=False,
                    permission=self.PERMISSION,
                ),
            ),
            resources=("obsion.development",),
        )

    async def execute(
        self,
        request: ConnectorExecuteRequest,
        context: ConnectorExecuteContext,
    ) -> Mapping[str, Any]:
        if request.operation != self.OPERATION:
            raise ConnectorSdkError(
                "capability_input_invalid",
                "The Connector SDK development adapter only exposes echo",
            )
        # Credential may be used for outbound I/O only; it is never copied into results.
        _ = context.credential
        return {
            "protocol": "connector-sdk",
            "adapter": "in-process",
            "operation": request.operation,
            "echo": dict(request.arguments),
            "run_id": context.run_id,
        }
