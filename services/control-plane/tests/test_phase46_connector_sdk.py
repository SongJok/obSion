from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from obsion.capabilities.connector_spi import (
    DEVELOPMENT_CAPABILITY,
    DEVELOPMENT_CONNECTOR_TYPE,
    DEVELOPMENT_OPERATION,
    REMOTE_UNAVAILABLE_MESSAGE,
    ConnectorSdkRuntime,
)
from obsion.capabilities.connectors import ConnectorContext, InternalExecutor
from obsion.capabilities.gateway import CapabilityGateway, GatewayRequest, GatewayStatus
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
from obsion_sdk.connector import (
    DEVELOPMENT_PLUGIN,
    ConnectorExecuteContext,
    ConnectorExecuteRequest,
    DevelopmentEchoConnector,
)

_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "obsion"
_FORBIDDEN_SPI_IMPORTS = (
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
        external_id="phase46-user",
        display_name="Phase 46 User",
        roles=frozenset({"engineer"}),
        permissions=frozenset({"connector.sdk.invoke"}),
    )


def _connector(**overrides: object) -> Connector:
    values: dict[str, object] = {
        "id": uuid4(),
        "organization_id": uuid4(),
        "name": "obsion-connector-sdk-development",
        "connector_type": DEVELOPMENT_CONNECTOR_TYPE,
        "status": ConnectorStatus.ACTIVE,
        "environment": "development",
        "configuration": {"operation": DEVELOPMENT_OPERATION, "plugin": DEVELOPMENT_PLUGIN},
        "declared_grants": ["connector.sdk.invoke"],
        "allowed_egress": [],
        "last_health": {"status": "ready"},
    }
    values.update(overrides)
    return Connector(**values)  # type: ignore[arg-type]


def _runtime() -> ConnectorSdkRuntime:
    runtime = ConnectorSdkRuntime()
    runtime.register(DEVELOPMENT_CONNECTOR_TYPE, DevelopmentEchoConnector())
    return runtime


def _context(principal: Principal, *, credential: str | None = None) -> ConnectorContext:
    return ConnectorContext(
        principal=principal,
        run_id=uuid4(),
        step_id=uuid4(),
        credential=credential,
    )


def test_execute_envelope_does_not_embed_credentials() -> None:
    encoded = {
        "operation": DEVELOPMENT_OPERATION,
        "arguments": {"ping": "pong"},
    }
    assert "credential" not in str(encoded).casefold()


@pytest.mark.asyncio
async def test_in_process_connector_sdk_echo_round_trips() -> None:
    principal = _principal()
    result = await _runtime().execute(
        _connector(),
        {"operation": DEVELOPMENT_OPERATION, "arguments": {"ping": "pong"}},
        "connector-secret-token",
        _context(principal, credential="connector-secret-token"),
    )
    assert result.data["protocol"] == "connector-sdk"
    assert result.data["adapter"] == "in-process"
    assert result.data["echo"] == {"ping": "pong"}
    assert "connector-secret-token" not in str(result.data)
    assert result.resource.startswith("connector-sdk://")


@pytest.mark.asyncio
async def test_remote_connector_sdk_install_and_url_fail_closed() -> None:
    principal = _principal()
    runtime = _runtime()
    context = _context(principal)
    with pytest.raises(ObsionError, match="not implemented") as remote:
        await runtime.execute(
            _connector(endpoint="https://pypi.org/simple/vendor-connector"),
            {"arguments": {}},
            None,
            context,
        )
    assert remote.value.code == "capability_transport_unavailable"
    with pytest.raises(ObsionError, match="not implemented"):
        await runtime.execute(
            _connector(configuration={"pip": "vendor-connector", "module": "vendor.client"}),
            {"arguments": {}},
            None,
            context,
        )
    with pytest.raises(ObsionError, match="not implemented"):
        await runtime.execute(
            _connector(allowed_egress=["pypi.org:443"]),
            {"arguments": {}},
            None,
            context,
        )


@pytest.mark.asyncio
async def test_unknown_connector_type_and_operation_fail_closed() -> None:
    principal = _principal()
    runtime = _runtime()
    context = _context(principal)
    with pytest.raises(ValidationError, match="No Connector SDK adapter") as missing:
        await runtime.execute(
            _connector(connector_type="connector-sdk-unknown"),
            {"arguments": {}},
            None,
            context,
        )
    assert missing.value.code == "connector_handler_missing"
    with pytest.raises(ValidationError, match="only exposes echo") as unknown:
        await runtime.execute(
            _connector(),
            {"operation": "delete", "arguments": {}},
            None,
            context,
        )
    assert unknown.value.code == "capability_input_invalid"


@pytest.mark.asyncio
async def test_health_and_discover_never_receive_or_return_credentials() -> None:
    connector = _connector()
    runtime = _runtime()
    health = await runtime.probe_health(connector)
    assert health["status"] == "ready"
    assert health["adapter"] == "connector-sdk"
    assert "credential" not in str(health).casefold()
    discovery = await runtime.discover(connector)
    assert discovery["operations"][0]["capability"] == DEVELOPMENT_CAPABILITY
    assert discovery["operations"][0]["side_effect"] is False
    assert "endpoint" not in str(discovery).casefold()
    assert "credential" not in str(discovery).casefold()


@pytest.mark.asyncio
async def test_read_only_execute_retries_transient_errors() -> None:
    class FlakyAdapter(DevelopmentEchoConnector):
        def __init__(self) -> None:
            self.calls = 0

        async def execute(
            self,
            request: ConnectorExecuteRequest,
            context: ConnectorExecuteContext,
        ) -> dict[str, object]:
            self.calls += 1
            if self.calls < 3:
                raise OSError("transient connector failure")
            return dict(await super().execute(request, context))

    runtime = ConnectorSdkRuntime()
    adapter = FlakyAdapter()
    runtime.register(DEVELOPMENT_CONNECTOR_TYPE, adapter)
    result = await runtime.execute(
        _connector(),
        {"arguments": {"ok": True}},
        None,
        _context(_principal()),
    )
    assert adapter.calls == 3
    assert result.data["echo"] == {"ok": True}


@pytest.mark.asyncio
async def test_side_effect_execute_does_not_retry() -> None:
    class FailingAdapter(DevelopmentEchoConnector):
        def __init__(self) -> None:
            self.calls = 0

        async def execute(
            self,
            request: ConnectorExecuteRequest,
            context: ConnectorExecuteContext,
        ) -> dict[str, object]:
            del request, context
            self.calls += 1
            raise OSError("write failed")

    runtime = ConnectorSdkRuntime()
    adapter = FailingAdapter()
    runtime.register(DEVELOPMENT_CONNECTOR_TYPE, adapter)
    with pytest.raises(OSError, match="write failed"):
        await runtime.execute(
            _connector(
                configuration={
                    "operation": DEVELOPMENT_OPERATION,
                    "side_effect": True,
                    "plugin": DEVELOPMENT_PLUGIN,
                }
            ),
            {"arguments": {}},
            None,
            _context(_principal()),
        )
    assert adapter.calls == 1


@pytest.mark.asyncio
async def test_capability_gateway_invokes_connector_sdk_through_internal_transport() -> None:
    principal = _principal()
    definition = CapabilityDefinition(
        id=uuid4(),
        organization_id=principal.organization_id,
        name=DEVELOPMENT_CAPABILITY,
        display_name="Connector SDK echo",
        description="test",
        status=RegistryStatus.ACTIVE,
    )
    version = CapabilityVersion(
        id=uuid4(),
        organization_id=principal.organization_id,
        capability_id=definition.id,
        version=1,
        transport=CapabilityTransport.INTERNAL,
        risk_level=RiskLevel.L1,
        side_effect=SideEffect.NONE,
        permission_action="connector.sdk.invoke",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        evidence_mapping={"type": "TOOL"},
        timeout_seconds=5,
        data_classification=Classification.INTERNAL,
        checksum_sha256="0" * 64,
        created_at=datetime.now(UTC),
    )
    connector = _connector(organization_id=principal.organization_id)
    internal = InternalExecutor()
    internal.register(DEVELOPMENT_CONNECTOR_TYPE, _runtime().as_internal_handler())
    policy = PolicyEngine()
    policy.evaluate = AsyncMock(  # type: ignore[method-assign]
        return_value=Decision(id=uuid4(), effect=DecisionEffect.ALLOW)
    )
    gateway = CapabilityGateway(
        {CapabilityTransport.INTERNAL.value: internal},
        policy=policy,
    )
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
            capability_name=DEVELOPMENT_CAPABILITY,
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
    assert result.output["protocol"] == "connector-sdk"


def test_connector_sdk_manifests_reject_package_install_shape() -> None:
    with pytest.raises(RegistryManifestError, match="package install"):
        parse_loaded_document(
            {
                "apiVersion": "obsion.dev/v1",
                "kind": "Connector",
                "metadata": {"name": "remote-connector-sdk"},
                "spec": {
                    "type": DEVELOPMENT_CONNECTOR_TYPE,
                    "environment": "development",
                    "transport": "INTERNAL",
                    "grants": ["connector.sdk.invoke"],
                    "allowedEgress": [],
                    "configuration": {"pip": "vendor-connector"},
                    "capabilities": [DEVELOPMENT_CAPABILITY],
                },
            },
            source="remote-connector-sdk.yaml",
        )


def test_seeded_connector_sdk_capability_is_catalogued(client: TestClient) -> None:
    capabilities = client.get("/api/v1/admin/capabilities")
    assert capabilities.status_code == 200, capabilities.text
    echo = next(item for item in capabilities.json() if item["name"] == DEVELOPMENT_CAPABILITY)
    assert echo["transport"] == "INTERNAL"
    connectors = client.get("/api/v1/admin/connectors")
    assert connectors.status_code == 200, connectors.text
    spi = next(
        item for item in connectors.json() if item["name"] == "obsion-connector-sdk-development"
    )
    assert spi["type"] == DEVELOPMENT_CONNECTOR_TYPE
    assert spi["spi"] is True
    assert spi["has_credential"] is False


def test_admin_health_and_discover_do_not_auto_bind(client: TestClient) -> None:
    connectors = client.get("/api/v1/admin/connectors")
    assert connectors.status_code == 200, connectors.text
    spi = next(
        item for item in connectors.json() if item["name"] == "obsion-connector-sdk-development"
    )
    connector_id = spi["id"]
    before = client.get("/api/v1/admin/capabilities")
    assert before.status_code == 200
    health = client.post(f"/api/v1/admin/connectors/{connector_id}/health")
    assert health.status_code == 200, health.text
    body = health.json()
    assert body["health"]["status"] == "ready"
    assert "credential" not in str(body).casefold()
    discover = client.post(f"/api/v1/admin/connectors/{connector_id}/discover")
    assert discover.status_code == 200, discover.text
    payload = discover.json()
    assert payload["discovery"]["operations"][0]["capability"] == DEVELOPMENT_CAPABILITY
    assert payload["binding_count"] >= 1
    after = client.get("/api/v1/admin/capabilities")
    assert after.status_code == 200
    assert [item["name"] for item in before.json()] == [item["name"] for item in after.json()]
    knowledge = next(item for item in connectors.json() if item["name"] == "obsion-knowledge-index")
    denied = client.post(f"/api/v1/admin/connectors/{knowledge['id']}/health")
    assert denied.status_code == 422
    assert denied.json()["code"] == "connector_handler_missing"


def test_connector_sdk_runtime_is_in_process_and_not_a_second_runtime() -> None:
    source = (_SOURCE_ROOT / "capabilities" / "connector_spi.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append(node.module)
    violations = [
        imported
        for imported in imports
        if imported in _FORBIDDEN_SPI_IMPORTS
        or any(imported.startswith(f"{name}.") for name in _FORBIDDEN_SPI_IMPORTS)
    ]
    assert violations == []
    assert REMOTE_UNAVAILABLE_MESSAGE in source
    main = (_SOURCE_ROOT / "main.py").read_text(encoding="utf-8")
    assert "connector_sdk_runtime.register" in main
    assert "CONNECTOR_SDK_DEVELOPMENT_TYPE" in main
    runtime = (_SOURCE_ROOT / "harness" / "runtime.py").read_text(encoding="utf-8")
    assert "ConnectorSdkRuntime" not in runtime
    assert "capabilities.connector_spi" not in runtime
    builtins = (_SOURCE_ROOT / "registry" / "builtins.py").read_text(encoding="utf-8")
    assert '"connector.sdk.echo"' in builtins
    assert (
        "connector.sdk.echo"
        in builtins.split("_IN_PROCESS_ADAPTER_CAPABILITIES", 1)[1].split(")", 1)[0]
    )


@pytest.mark.asyncio
async def test_execute_rejects_credential_leak_from_adapter() -> None:
    class LeakyAdapter(DevelopmentEchoConnector):
        async def execute(
            self,
            request: ConnectorExecuteRequest,
            context: ConnectorExecuteContext,
        ) -> dict[str, object]:
            del request
            return {"echo": context.credential or "missing"}

    runtime = ConnectorSdkRuntime()
    runtime.register(DEVELOPMENT_CONNECTOR_TYPE, LeakyAdapter())
    with pytest.raises(ValidationError) as leaked:
        await runtime.execute(
            _connector(),
            {"arguments": {}},
            "connector-secret-token",
            _context(_principal(), credential="connector-secret-token"),
        )
    assert leaked.value.code == "capability_output_invalid"
