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
from obsion.capabilities.sdk import (
    DEVELOPMENT_CONNECTOR_TYPE,
    DEVELOPMENT_METHOD,
    DEVELOPMENT_SDK,
    DevelopmentSdkExecutor,
    create_development_echo_handler,
    encode_sdk_call,
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
_FORBIDDEN_SDK_IMPORTS = (
    "subprocess",
    "multiprocessing",
    "httpx",
    "requests",
    "aiohttp",
    "socket",
    "http.client",
    "urllib",
    "importlib",
    "pkgutil",
)


def _principal() -> Principal:
    return Principal(
        id=uuid4(),
        organization_id=uuid4(),
        external_id="phase39-user",
        display_name="Phase 39 User",
        roles=frozenset({"engineer"}),
        permissions=frozenset({"sdk.invoke"}),
    )


def _connector(**overrides: object) -> Connector:
    values: dict[str, object] = {
        "id": uuid4(),
        "organization_id": uuid4(),
        "name": "obsion-sdk-development",
        "connector_type": DEVELOPMENT_CONNECTOR_TYPE,
        "status": ConnectorStatus.ACTIVE,
        "environment": "development",
        "configuration": {"sdk": DEVELOPMENT_SDK, "method": DEVELOPMENT_METHOD},
        "declared_grants": ["sdk.invoke"],
        "allowed_egress": [],
        "last_health": {"status": "ready"},
    }
    values.update(overrides)
    return Connector(**values)  # type: ignore[arg-type]


def _executor() -> DevelopmentSdkExecutor:
    executor = DevelopmentSdkExecutor()
    executor.register(DEVELOPMENT_CONNECTOR_TYPE, create_development_echo_handler())
    return executor


def _context(principal: Principal) -> ConnectorContext:
    return ConnectorContext(principal=principal, run_id=uuid4(), step_id=uuid4())


def test_sdk_invocation_envelope_does_not_embed_credentials() -> None:
    encoded = encode_sdk_call(
        sdk=DEVELOPMENT_SDK,
        method=DEVELOPMENT_METHOD,
        arguments={"ping": "pong"},
    )
    assert encoded == {
        "sdk": DEVELOPMENT_SDK,
        "method": DEVELOPMENT_METHOD,
        "arguments": {"ping": "pong"},
    }
    assert "credential" not in str(encoded).casefold()


@pytest.mark.asyncio
async def test_in_process_sdk_echo_round_trips() -> None:
    principal = _principal()
    result = await _executor().invoke(
        _connector(),
        {"sdk": DEVELOPMENT_SDK, "method": DEVELOPMENT_METHOD, "arguments": {"ping": "pong"}},
        "connector-secret-token",
        _context(principal),
    )
    assert result.data["protocol"] == "sdk"
    assert result.data["adapter"] == "in-process"
    assert result.data["echo"] == {"ping": "pong"}
    assert "connector-secret-token" not in str(result.data)
    assert result.resource.startswith("sdk://")


@pytest.mark.asyncio
async def test_remote_sdk_install_and_url_fail_closed() -> None:
    principal = _principal()
    executor = _executor()
    context = _context(principal)
    with pytest.raises(ObsionError, match="not implemented") as remote:
        await executor.invoke(
            _connector(endpoint="https://pypi.org/simple/vendor-sdk"),
            {"arguments": {}},
            None,
            context,
        )
    assert remote.value.code == "capability_transport_unavailable"
    with pytest.raises(ObsionError, match="not implemented"):
        await executor.invoke(
            _connector(configuration={"pip": "vendor-sdk", "module": "vendor.client"}),
            {"arguments": {}},
            None,
            context,
        )
    with pytest.raises(ObsionError, match="not implemented"):
        await executor.invoke(
            _connector(allowed_egress=["pypi.org:443"]),
            {"arguments": {}},
            None,
            context,
        )


@pytest.mark.asyncio
async def test_unknown_sdk_connector_type_and_method_fail_closed() -> None:
    principal = _principal()
    executor = _executor()
    context = _context(principal)
    with pytest.raises(ValidationError, match="No SDK handler") as missing:
        await executor.invoke(
            _connector(connector_type="sdk-unknown"),
            {"arguments": {}},
            None,
            context,
        )
    assert missing.value.code == "connector_handler_missing"
    with pytest.raises(ValidationError, match="obsion.development.echo") as unknown:
        await executor.invoke(
            _connector(),
            {"sdk": "vendor.sdk", "method": "delete", "arguments": {}},
            None,
            context,
        )
    assert unknown.value.code == "capability_input_invalid"


@pytest.mark.asyncio
async def test_capability_gateway_invokes_sdk_executor() -> None:
    principal = _principal()
    definition = CapabilityDefinition(
        id=uuid4(),
        organization_id=principal.organization_id,
        name="sdk.development.echo",
        display_name="SDK echo",
        description="test",
        status=RegistryStatus.ACTIVE,
    )
    version = CapabilityVersion(
        id=uuid4(),
        organization_id=principal.organization_id,
        capability_id=definition.id,
        version=1,
        transport=CapabilityTransport.SDK,
        risk_level=RiskLevel.L1,
        side_effect=SideEffect.NONE,
        permission_action="sdk.invoke",
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
    gateway = CapabilityGateway({CapabilityTransport.SDK.value: _executor()}, policy=policy)
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
            capability_name="sdk.development.echo",
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


def test_sdk_manifests_reject_package_install_shape() -> None:
    with pytest.raises(RegistryManifestError, match="package install"):
        parse_loaded_document(
            {
                "apiVersion": "obsion.dev/v1",
                "kind": "Connector",
                "metadata": {"name": "remote-sdk"},
                "spec": {
                    "type": "sdk-development",
                    "environment": "development",
                    "transport": "SDK",
                    "grants": ["sdk.invoke"],
                    "allowedEgress": [],
                    "configuration": {"pip": "vendor-sdk"},
                    "capabilities": ["sdk.development.echo"],
                },
            },
            source="remote-sdk.yaml",
        )


def test_seeded_sdk_capability_is_catalogued(client: TestClient) -> None:
    capabilities = client.get("/api/v1/admin/capabilities")
    assert capabilities.status_code == 200, capabilities.text
    echo = next(item for item in capabilities.json() if item["name"] == "sdk.development.echo")
    assert echo["transport"] == "SDK"
    connectors = client.get("/api/v1/admin/connectors")
    assert connectors.status_code == 200, connectors.text
    sdk = next(item for item in connectors.json() if item["name"] == "obsion-sdk-development")
    assert sdk["type"] == DEVELOPMENT_CONNECTOR_TYPE
    assert sdk["has_credential"] is False


def test_sdk_executor_is_in_process_and_not_a_second_runtime() -> None:
    sdk_source = (_SOURCE_ROOT / "capabilities" / "sdk.py").read_text(encoding="utf-8")
    tree = ast.parse(sdk_source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append(node.module)
    violations = [
        imported
        for imported in imports
        if imported in _FORBIDDEN_SDK_IMPORTS
        or any(imported.startswith(f"{name}.") for name in _FORBIDDEN_SDK_IMPORTS)
    ]
    assert violations == []
    assert "SDK package install and remote endpoints are not implemented" in sdk_source
    main = (_SOURCE_ROOT / "main.py").read_text(encoding="utf-8")
    assert "CapabilityTransport.SDK.value: sdk_executor" in main
    runtime = (_SOURCE_ROOT / "harness" / "runtime.py").read_text(encoding="utf-8")
    assert "DevelopmentSdkExecutor" not in runtime
    assert "capabilities.sdk" not in runtime
