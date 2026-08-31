from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from obsion.capabilities.connectors import ConnectorContext
from obsion.capabilities.gateway import CapabilityGateway, GatewayRequest, GatewayStatus
from obsion.capabilities.grpc import (
    DEVELOPMENT_CONNECTOR_TYPE,
    DEVELOPMENT_METHOD,
    DEVELOPMENT_SERVICE,
    DevelopmentGrpcExecutor,
    create_development_echo_handler,
    encode_grpc_call,
)
from obsion.common.errors import ObsionError, ValidationError
from obsion.db.models import CapabilityDefinition, CapabilityVersion, Connector
from obsion.domain.enums import (
    CapabilityTransport,
    Classification,
    ConnectorStatus,
    DecisionEffect,
    RegistryStatus,
    RiskLevel,
    SideEffect,
)
from obsion.registry.manifests import RegistryManifestError, parse_loaded_document
from obsion.security.identity import Principal
from obsion.security.policy import Decision, PolicyEngine

_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "obsion"
_REPOSITORY_ROOT = Path(__file__).parents[3]
_FORBIDDEN_GRPC_IMPORTS = (
    "grpc",
    "grpcio",
    "grpclib",
    "google.protobuf",
    "subprocess",
    "multiprocessing",
    "httpx",
    "requests",
    "aiohttp",
    "socket",
    "http.client",
    "urllib",
    "importlib",
)


def _principal() -> Principal:
    return Principal(
        id=uuid4(),
        organization_id=uuid4(),
        external_id="phase41-user",
        display_name="Phase 41 User",
        roles=frozenset({"engineer"}),
        permissions=frozenset({"grpc.invoke"}),
    )


def _connector(**overrides: object) -> Connector:
    values: dict[str, object] = {
        "id": uuid4(),
        "organization_id": uuid4(),
        "name": "obsion-grpc-development",
        "connector_type": DEVELOPMENT_CONNECTOR_TYPE,
        "status": ConnectorStatus.ACTIVE,
        "environment": "development",
        "configuration": {"service": DEVELOPMENT_SERVICE, "method": DEVELOPMENT_METHOD},
        "declared_grants": ["grpc.invoke"],
        "allowed_egress": [],
        "last_health": {"status": "ready"},
    }
    values.update(overrides)
    return Connector(**values)  # type: ignore[arg-type]


def _executor() -> DevelopmentGrpcExecutor:
    executor = DevelopmentGrpcExecutor()
    executor.register(DEVELOPMENT_CONNECTOR_TYPE, create_development_echo_handler())
    return executor


def _context(principal: Principal) -> ConnectorContext:
    return ConnectorContext(principal=principal, run_id=uuid4(), step_id=uuid4())


def test_grpc_invocation_envelope_does_not_embed_credentials() -> None:
    encoded = encode_grpc_call(
        service=DEVELOPMENT_SERVICE,
        method=DEVELOPMENT_METHOD,
        message={"ping": "pong"},
    )
    assert encoded == {
        "service": DEVELOPMENT_SERVICE,
        "method": DEVELOPMENT_METHOD,
        "message": {"ping": "pong"},
    }
    assert "credential" not in str(encoded).casefold()


@pytest.mark.asyncio
async def test_in_process_grpc_echo_round_trips() -> None:
    principal = _principal()
    result = await _executor().invoke(
        _connector(),
        {
            "service": DEVELOPMENT_SERVICE,
            "method": DEVELOPMENT_METHOD,
            "message": {"ping": "pong"},
        },
        "connector-secret-token",
        _context(principal),
    )
    assert result.data["protocol"] == "grpc"
    assert result.data["adapter"] == "in-process"
    assert result.data["echo"] == {"ping": "pong"}
    assert "connector-secret-token" not in str(result.data)
    assert result.resource.startswith("grpc://")


@pytest.mark.asyncio
async def test_remote_grpc_channel_and_host_fail_closed() -> None:
    principal = _principal()
    executor = _executor()
    context = _context(principal)
    with pytest.raises(ObsionError, match="not implemented") as remote:
        await executor.invoke(
            _connector(endpoint="grpcs://vendor.example:443"),
            {"message": {}},
            None,
            context,
        )
    assert remote.value.code == "capability_transport_unavailable"
    with pytest.raises(ObsionError, match="not implemented"):
        await executor.invoke(
            _connector(configuration={"host": "vendor.example", "port": "443", "tls": True}),
            {"message": {}},
            None,
            context,
        )
    with pytest.raises(ObsionError, match="not implemented"):
        await executor.invoke(
            _connector(allowed_egress=["vendor.example:443"]),
            {"message": {}},
            None,
            context,
        )


@pytest.mark.asyncio
async def test_unknown_grpc_connector_type_and_method_fail_closed() -> None:
    principal = _principal()
    executor = _executor()
    context = _context(principal)
    with pytest.raises(ValidationError, match="No gRPC handler") as missing:
        await executor.invoke(
            _connector(connector_type="grpc-unknown"),
            {"message": {}},
            None,
            context,
        )
    assert missing.value.code == "connector_handler_missing"
    with pytest.raises(ValidationError, match="obsion.development.Echo/Ping") as unknown:
        await executor.invoke(
            _connector(),
            {"service": "vendor.Billing", "method": "Charge", "message": {}},
            None,
            context,
        )
    assert unknown.value.code == "capability_input_invalid"


@pytest.mark.asyncio
async def test_capability_gateway_invokes_grpc_executor() -> None:
    principal = _principal()
    definition = CapabilityDefinition(
        id=uuid4(),
        organization_id=principal.organization_id,
        name="grpc.development.echo",
        display_name="gRPC echo",
        description="test",
        status=RegistryStatus.ACTIVE,
    )
    version = CapabilityVersion(
        id=uuid4(),
        organization_id=principal.organization_id,
        capability_id=definition.id,
        version=1,
        transport=CapabilityTransport.GRPC,
        risk_level=RiskLevel.L1,
        side_effect=SideEffect.NONE,
        permission_action="grpc.invoke",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        evidence_mapping={"type": "TOOL"},
        timeout_seconds=5,
        data_classification=Classification.INTERNAL,
        checksum_sha256="0" * 64,
        created_at=datetime.now(UTC),
    )
    connector = _connector(organization_id=principal.organization_id)
    policy = PolicyEngine()
    policy.evaluate = AsyncMock(  # type: ignore[method-assign]
        return_value=Decision(id=uuid4(), effect=DecisionEffect.ALLOW)
    )
    gateway = CapabilityGateway({CapabilityTransport.GRPC.value: _executor()}, policy=policy)
    gateway._resolve = AsyncMock(return_value=(definition, version, connector))  # noqa: SLF001
    gateway.events = SimpleNamespace(append=AsyncMock())
    gateway._policy_event = AsyncMock()  # noqa: SLF001
    gateway._gateway_event = AsyncMock()  # noqa: SLF001
    gateway._audit = AsyncMock()  # noqa: SLF001
    gateway._evidence = AsyncMock(return_value=SimpleNamespace(id=uuid4()))  # noqa: SLF001

    result = await gateway._invoke(  # noqa: SLF001
        SimpleNamespace(),
        GatewayRequest(
            principal=principal,
            capability_name="grpc.development.echo",
            payload={"message": {"ping": "pong"}},
            resource={},
            environment="development",
            agent_name="external-client",
            run_id=uuid4(),
        ),
    )
    assert result.status == GatewayStatus.COMPLETED
    assert result.output is not None
    assert result.output["adapter"] == "in-process"


def test_grpc_manifests_reject_remote_channel_shape() -> None:
    with pytest.raises(RegistryManifestError, match="process spawn"):
        parse_loaded_document(
            {
                "apiVersion": "obsion.dev/v1",
                "kind": "Connector",
                "metadata": {"name": "remote-grpc"},
                "spec": {
                    "type": "grpc-development",
                    "environment": "development",
                    "transport": "GRPC",
                    "grants": ["grpc.invoke"],
                    "allowedEgress": [],
                    "configuration": {"host": "vendor.example", "port": "443"},
                    "capabilities": ["grpc.development.echo"],
                },
            },
            source="remote-grpc.yaml",
        )


def test_seeded_grpc_capability_is_catalogued(client: TestClient) -> None:
    capabilities = client.get("/api/v1/admin/capabilities")
    assert capabilities.status_code == 200, capabilities.text
    echo = next(item for item in capabilities.json() if item["name"] == "grpc.development.echo")
    assert echo["transport"] == "GRPC"
    connectors = client.get("/api/v1/admin/connectors")
    assert connectors.status_code == 200, connectors.text
    grpc = next(item for item in connectors.json() if item["name"] == "obsion-grpc-development")
    assert grpc["type"] == DEVELOPMENT_CONNECTOR_TYPE
    assert grpc["has_credential"] is False


def test_grpc_is_not_declared_on_shipped_agents() -> None:
    for path in (_REPOSITORY_ROOT / "agents").glob("*.yaml"):
        text = path.read_text(encoding="utf-8")
        assert "grpc.development.echo" not in text
        assert "obsion-grpc-development" not in text


def test_grpc_executor_is_in_process_and_not_a_second_runtime() -> None:
    grpc_source = (_SOURCE_ROOT / "capabilities" / "grpc.py").read_text(encoding="utf-8")
    tree = ast.parse(grpc_source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append(node.module)
    violations = [
        imported
        for imported in imports
        if imported in _FORBIDDEN_GRPC_IMPORTS
        or any(imported.startswith(f"{name}.") for name in _FORBIDDEN_GRPC_IMPORTS)
    ]
    assert violations == []
    assert "gRPC remote channels and process spawn are not implemented" in grpc_source
    main = (_SOURCE_ROOT / "main.py").read_text(encoding="utf-8")
    assert "CapabilityTransport.GRPC.value: grpc_executor" in main
    runtime = (_SOURCE_ROOT / "harness" / "runtime.py").read_text(encoding="utf-8")
    assert "DevelopmentGrpcExecutor" not in runtime
    assert "capabilities.grpc" not in runtime
