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
from obsion.capabilities.mcp import (
    DEVELOPMENT_CONNECTOR_TYPE,
    DEVELOPMENT_TOOL,
    MCP_PROTOCOL_VERSION,
    DevelopmentMcpExecutor,
    create_development_echo_handler,
    encode_tools_call,
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
_FORBIDDEN_MCP_IMPORTS = (
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
        external_id="phase38-user",
        display_name="Phase 38 User",
        roles=frozenset({"engineer"}),
        permissions=frozenset({"mcp.invoke"}),
    )


def _connector(**overrides: object) -> Connector:
    values: dict[str, object] = {
        "id": uuid4(),
        "organization_id": uuid4(),
        "name": "obsion-mcp-development",
        "connector_type": DEVELOPMENT_CONNECTOR_TYPE,
        "status": ConnectorStatus.ACTIVE,
        "environment": "development",
        "configuration": {"tool": DEVELOPMENT_TOOL},
        "declared_grants": ["mcp.invoke"],
        "allowed_egress": [],
        "last_health": {"status": "ready"},
    }
    values.update(overrides)
    return Connector(**values)  # type: ignore[arg-type]


def _executor() -> DevelopmentMcpExecutor:
    executor = DevelopmentMcpExecutor()
    executor.register(DEVELOPMENT_CONNECTOR_TYPE, create_development_echo_handler())
    return executor


def _context(principal: Principal) -> ConnectorContext:
    return ConnectorContext(principal=principal, run_id=uuid4(), step_id=uuid4())


def test_mcp_jsonrpc_tools_call_does_not_embed_credentials() -> None:
    encoded = encode_tools_call(
        request_id="run-1",
        name=DEVELOPMENT_TOOL,
        arguments={"ping": "pong"},
    )
    assert encoded["jsonrpc"] == "2.0"
    assert encoded["method"] == "tools/call"
    assert encoded["params"]["name"] == DEVELOPMENT_TOOL
    assert encoded["params"]["arguments"] == {"ping": "pong"}
    assert "Authorization" not in str(encoded)
    assert "credential" not in str(encoded).casefold()


@pytest.mark.asyncio
async def test_in_process_mcp_echo_round_trips_jsonrpc() -> None:
    principal = _principal()
    result = await _executor().invoke(
        _connector(),
        {"name": DEVELOPMENT_TOOL, "arguments": {"ping": "pong"}},
        "connector-secret-token",
        _context(principal),
    )
    assert result.data["protocol"] == "mcp"
    assert result.data["adapter"] == "in-process"
    assert result.data["protocol_version"] == MCP_PROTOCOL_VERSION
    assert result.data["echo"] == {"ping": "pong"}
    assert "connector-secret-token" not in str(result.data)
    assert result.resource.startswith("mcp://")


@pytest.mark.asyncio
async def test_remote_mcp_process_and_url_fail_closed() -> None:
    principal = _principal()
    executor = _executor()
    context = _context(principal)
    with pytest.raises(ObsionError, match="not implemented") as remote:
        await executor.invoke(
            _connector(endpoint="https://mcp.example.internal/sse"),
            {"arguments": {}},
            None,
            context,
        )
    assert remote.value.code == "capability_transport_unavailable"
    with pytest.raises(ObsionError, match="not implemented"):
        await executor.invoke(
            _connector(configuration={"command": "npx", "args": ["@modelcontextprotocol/server"]}),
            {"arguments": {}},
            None,
            context,
        )
    with pytest.raises(ObsionError, match="not implemented"):
        await executor.invoke(
            _connector(allowed_egress=["mcp.example.internal:443"]),
            {"arguments": {}},
            None,
            context,
        )


@pytest.mark.asyncio
async def test_unknown_mcp_connector_type_and_tool_fail_closed() -> None:
    principal = _principal()
    executor = _executor()
    context = _context(principal)
    with pytest.raises(ValidationError, match="No MCP handler") as missing:
        await executor.invoke(
            _connector(connector_type="mcp-unknown"),
            {"arguments": {}},
            None,
            context,
        )
    assert missing.value.code == "connector_handler_missing"
    with pytest.raises(ValidationError, match="obsion.echo") as unknown_tool:
        await executor.invoke(
            _connector(),
            {"name": "filesystem.read", "arguments": {}},
            None,
            context,
        )
    assert unknown_tool.value.code == "capability_input_invalid"


@pytest.mark.asyncio
async def test_capability_gateway_invokes_mcp_executor() -> None:
    principal = _principal()
    definition = CapabilityDefinition(
        id=uuid4(),
        organization_id=principal.organization_id,
        name="mcp.development.echo",
        display_name="MCP echo",
        description="test",
        status=RegistryStatus.ACTIVE,
    )
    version = CapabilityVersion(
        id=uuid4(),
        organization_id=principal.organization_id,
        capability_id=definition.id,
        version=1,
        transport=CapabilityTransport.MCP,
        risk_level=RiskLevel.L1,
        side_effect=SideEffect.NONE,
        permission_action="mcp.invoke",
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
    gateway = CapabilityGateway({CapabilityTransport.MCP.value: _executor()}, policy=policy)
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
            capability_name="mcp.development.echo",
            payload={"arguments": {"ping": "pong"}},
            resource={},
            environment="development",
            agent_name="external-client",
            run_id=uuid4(),
        ),
    )
    assert result.status == GatewayStatus.COMPLETED
    assert result.output is not None
    assert result.output["adapter"] == "in-process"


def test_mcp_manifests_reject_remote_process_shape() -> None:
    with pytest.raises(RegistryManifestError, match="process spawn"):
        parse_loaded_document(
            {
                "apiVersion": "obsion.dev/v1",
                "kind": "Connector",
                "metadata": {"name": "remote-mcp"},
                "spec": {
                    "type": "mcp-development",
                    "environment": "development",
                    "transport": "MCP",
                    "grants": ["mcp.invoke"],
                    "allowedEgress": [],
                    "configuration": {"command": "npx"},
                    "capabilities": ["mcp.development.echo"],
                },
            },
            source="remote-mcp.yaml",
        )


def test_seeded_mcp_capability_is_catalogued(client: TestClient) -> None:
    capabilities = client.get("/api/v1/admin/capabilities")
    assert capabilities.status_code == 200, capabilities.text
    echo = next(item for item in capabilities.json() if item["name"] == "mcp.development.echo")
    assert echo["transport"] == "MCP"
    connectors = client.get("/api/v1/admin/connectors")
    assert connectors.status_code == 200, connectors.text
    names = {item["name"] for item in connectors.json()}
    assert "obsion-mcp-development" in names
    mcp = next(item for item in connectors.json() if item["name"] == "obsion-mcp-development")
    assert mcp["type"] == DEVELOPMENT_CONNECTOR_TYPE
    assert mcp["has_credential"] is False


def test_mcp_executor_is_in_process_and_not_a_second_runtime() -> None:
    mcp_source = (_SOURCE_ROOT / "capabilities" / "mcp.py").read_text(encoding="utf-8")
    tree = ast.parse(mcp_source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append(node.module)
    violations = [
        imported
        for imported in imports
        if imported in _FORBIDDEN_MCP_IMPORTS
        or any(imported.startswith(f"{name}.") for name in _FORBIDDEN_MCP_IMPORTS)
    ]
    assert violations == []
    assert "REMOTE_UNAVAILABLE_MESSAGE" in mcp_source
    assert "MCP process spawn and remote endpoints are not implemented" in mcp_source
    assert "jsonrpc" in mcp_source
    main = (_SOURCE_ROOT / "main.py").read_text(encoding="utf-8")
    assert "CapabilityTransport.MCP.value: mcp_executor" in main
    runtime = (_SOURCE_ROOT / "harness" / "runtime.py").read_text(encoding="utf-8")
    assert "DevelopmentMcpExecutor" not in runtime
    assert "capabilities.mcp" not in runtime
