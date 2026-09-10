from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from obsion.capabilities.circuit_breaker import ConnectorCircuitBreaker
from obsion.capabilities.connectors import ConnectorContext, ConnectorResult, HttpJsonExecutor
from obsion.capabilities.gateway import CapabilityGateway, GatewayRequest, GatewayStatus
from obsion.capabilities.yunxiao import (
    YUNXIAO_PROTOCOL,
    YunxiaoClient,
    YunxiaoResponseError,
    assert_yunxiao_egress,
    is_yunxiao_connector,
    normalize_repository_response,
    resolve_yunxiao_credentials,
)
from obsion.common.errors import ValidationError
from obsion.config import Environment, Settings
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
from obsion.registry.builtins import _CAPABILITIES
from obsion.security.identity import Principal
from obsion.security.policy import Decision, PolicyEngine

_TOKEN = "yunxiao-pat-sentinel"


def _connector(
    *, endpoint: str = "https://openapi-rdc.aliyuncs.com", **configuration: object
) -> Connector:
    organization_id = uuid4()
    return Connector(
        id=uuid4(),
        organization_id=organization_id,
        name="yunxiao-read-only",
        connector_type="yunxiao",
        status=ConnectorStatus.ACTIVE,
        environment="production",
        endpoint=endpoint,
        configuration={
            "protocol": YUNXIAO_PROTOCOL,
            "rate_limit_per_minute": 60,
            "timeout_seconds": 15,
            **configuration,
        },
        credential_ref="secret://yunxiao-read-only",
        declared_grants=["code.read"],
        allowed_egress=[endpoint],
    )


def _context(connector: Connector) -> ConnectorContext:
    return ConnectorContext(
        principal=Principal(
            id=uuid4(),
            organization_id=connector.organization_id,
            external_id="phase98-user",
            display_name="Phase 98 User",
        ),
        run_id=uuid4(),
        step_id=None,
    )


@pytest.mark.asyncio
async def test_yunxiao_central_lists_every_pat_visible_organization_without_bearer() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["x-yunxiao-token"] == _TOKEN
        assert "authorization" not in request.headers
        assert request.content == b""
        if request.url.path == "/oapi/v1/platform/organizations":
            return httpx.Response(200, json={"data": [{"id": "org-a"}, {"id": "org-b"}]})
        if request.url.path == "/oapi/v1/codeup/organizations/org-a/repositories":
            return httpx.Response(
                200,
                json={
                    "total": 1,
                    "data": [
                        {
                            "id": 11,
                            "name": "alpha",
                            "description": "token=not-returned",
                            "webUrl": "https://codeup.example/alpha?token=not-returned#section",
                            "avatarUrl": "https://codeup.example/avatar.png",
                        }
                    ],
                },
            )
        if request.url.path == "/oapi/v1/codeup/organizations/org-b/repositories":
            return httpx.Response(200, json={"total": 1, "data": [{"id": 12, "name": "beta"}]})
        raise AssertionError(f"Unexpected Yunxiao route {request.url.path}")

    connector = _connector()
    client = YunxiaoClient(
        connector,
        _TOKEN,
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )
    result = await client.list_repositories(page=1, per_page=2)

    assert [request.url.path for request in requests] == [
        "/oapi/v1/platform/organizations",
        "/oapi/v1/codeup/organizations/org-a/repositories",
        "/oapi/v1/codeup/organizations/org-b/repositories",
    ]
    assert result["total"] == 2
    assert result["count"] == 2
    assert result["items"][0]["organization_id"] == "org-a"
    assert result["items"][0]["description"] == "token=[REDACTED]"
    assert result["items"][0]["web_url"] == "https://codeup.example/alpha"
    assert "avatar_url" not in result["items"][0]
    assert _TOKEN not in repr(result)


@pytest.mark.asyncio
async def test_yunxiao_central_empty_organization_list_is_a_read_only_empty_result() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oapi/v1/platform/organizations"
        return httpx.Response(200, json={"data": []})

    client = YunxiaoClient(
        _connector(),
        _TOKEN,
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )

    result = await client.list_repositories()

    assert result == {
        "operation": "yunxiao.repositories.list",
        "items": [],
        "count": 0,
        "total": 0,
        "page": 1,
        "per_page": 50,
        "next_page": None,
    }


@pytest.mark.asyncio
async def test_yunxiao_paginates_across_organizations_without_duplicate_items() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oapi/v1/platform/organizations":
            return httpx.Response(200, json={"data": [{"id": "org-a"}, {"id": "org-b"}]})
        if request.url.path.endswith("/org-a/repositories"):
            page = request.url.params["page"]
            items = [
                {"id": 1, "name": "alpha-1"},
                {"id": 2, "name": "alpha-2"},
                {"id": 3, "name": "alpha-3"},
            ]
            start = (int(page) - 1) * 2
            return httpx.Response(200, json={"total": 3, "data": items[start : start + 2]})
        if request.url.path.endswith("/org-b/repositories"):
            return httpx.Response(
                200,
                json={
                    "total": 2,
                    "data": [{"id": 4, "name": "beta-1"}, {"id": 5, "name": "beta-2"}],
                },
            )
        raise AssertionError(f"Unexpected Yunxiao route {request.url.path}")

    client = YunxiaoClient(
        _connector(),
        _TOKEN,
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )

    page = await client.list_repositories(page=2, per_page=2)

    assert [item["id"] for item in page["items"]] == ["3", "4"]
    assert page["total"] == 5
    assert page["next_page"] == 3

    page_three = await client.list_repositories(page=3, per_page=2)
    assert [item["id"] for item in page_three["items"]] == ["5"]


@pytest.mark.asyncio
async def test_yunxiao_malformed_vendor_response_counts_as_circuit_failure() -> None:
    connector = _connector()
    circuit = ConnectorCircuitBreaker(failure_threshold=1)
    executor = HttpJsonExecutor(
        Settings(environment=Environment.TEST),
        circuit=circuit,
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"data": "invalid"})
        ),
    )

    with pytest.raises(YunxiaoResponseError):
        await executor.invoke(
            connector,
            {"operation": "yunxiao.repositories.list"},
            _TOKEN,
            _context(connector),
        )

    with pytest.raises(Exception) as blocked:
        await executor.invoke(
            connector,
            {"operation": "yunxiao.repositories.list"},
            _TOKEN,
            _context(connector),
        )
    assert blocked.value.__class__.__name__ == "ConnectorCircuitOpenError"


@pytest.mark.asyncio
async def test_yunxiao_regional_connector_requires_explicit_visible_organizations() -> None:
    connector = _connector(endpoint="https://codeup.example.invalid")
    client = YunxiaoClient(connector, _TOKEN, timeout_seconds=1)

    with pytest.raises(ValidationError) as caught:
        await client.list_repositories()

    assert caught.value.code == "capability_input_invalid"


@pytest.mark.asyncio
async def test_yunxiao_regional_connector_sends_each_configured_organization_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/oapi/v1/codeup/repositories"
        assert request.url.params["organizationId"] == "org-regional"
        return httpx.Response(200, json={"total": 1, "data": [{"id": 7, "name": "regional"}]})

    connector = _connector(
        endpoint="https://codeup.example.invalid",
        organization_ids=["org-regional"],
    )
    client = YunxiaoClient(
        connector,
        _TOKEN,
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )

    result = await client.list_repositories()

    assert result["items"][0]["organization_id"] == "org-regional"


@pytest.mark.asyncio
async def test_yunxiao_executor_rejects_invalid_operation_and_egress_before_network() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        del request
        calls += 1
        return httpx.Response(200, json=[])

    connector = _connector()
    executor = HttpJsonExecutor(
        Settings(environment=Environment.TEST), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(ValidationError) as operation:
        await executor.invoke(
            connector,
            {"operation": "yunxiao.repositories.create"},
            _TOKEN,
            _context(connector),
        )
    assert operation.value.code == "capability_input_invalid"

    connector.allowed_egress = ["https://elsewhere.invalid"]
    with pytest.raises(ValidationError) as egress:
        await executor.invoke(
            connector,
            {"operation": "yunxiao.repositories.list"},
            _TOKEN,
            _context(connector),
        )
    assert egress.value.code == "connector_egress_denied"
    assert calls == 0


@pytest.mark.parametrize(
    ("endpoint", "configuration"),
    [
        ("http://openapi-rdc.aliyuncs.com", {}),
        ("https://access:token@openapi-rdc.aliyuncs.com", {}),
        ("https://openapi-rdc.aliyuncs.com?token=bad", {}),
        ("https://openapi-rdc.aliyuncs.com:8443", {}),
        ("https://openapi-rdc.aliyuncs.com", {"baseUrl": "https://elsewhere.invalid"}),
    ],
)
def test_yunxiao_egress_is_exact_and_fail_closed(
    endpoint: str, configuration: dict[str, object]
) -> None:
    connector = _connector(endpoint=endpoint, **configuration)
    with pytest.raises(ValidationError):
        assert_yunxiao_egress(connector)


def test_yunxiao_credentials_are_opaque_and_never_read_from_connector_configuration() -> None:
    connector = _connector()
    assert resolve_yunxiao_credentials(connector, _TOKEN) == _TOKEN

    connector.configuration["tokenization"] = "standard"
    assert resolve_yunxiao_credentials(connector, _TOKEN) == _TOKEN

    for credential in (None, "", '{"access_token":"wrong-contract"}'):
        with pytest.raises(ValidationError) as caught:
            resolve_yunxiao_credentials(connector, credential)
        assert caught.value.code == "credential_unavailable"
        assert _TOKEN not in str(caught.value)

    connector.configuration["access_token"] = _TOKEN
    with pytest.raises(ValidationError) as inline:
        resolve_yunxiao_credentials(connector, _TOKEN)
    assert inline.value.code == "credential_unavailable"
    assert _TOKEN not in str(inline.value)


def test_yunxiao_normalization_rejects_malformed_rows_and_schema_is_read_only() -> None:
    with pytest.raises(YunxiaoResponseError):
        normalize_repository_response([{"id": "repository-only"}], page=1, per_page=10)

    seed = next(item for item in _CAPABILITIES if item.name == "yunxiao.repositories.list")
    assert seed.permission == "code.read"
    assert seed.evidence_type == "CODE"
    assert seed.risk == RiskLevel.L1
    assert seed.transport == CapabilityTransport.HTTP
    assert seed.side_effect == SideEffect.NONE
    assert seed.input_schema is not None
    assert seed.input_schema["additionalProperties"] is False
    assert "organization_id" not in seed.input_schema["properties"]


@pytest.mark.asyncio
async def test_yunxiao_policy_denial_precedes_credential_resolution_and_execution() -> None:
    connector = _connector()
    principal = _context(connector).principal
    definition = CapabilityDefinition(
        id=uuid4(),
        organization_id=connector.organization_id,
        name="yunxiao.repositories.list",
        display_name="Yunxiao repositories",
        description="test",
        status=RegistryStatus.ACTIVE,
    )
    version = CapabilityVersion(
        id=uuid4(),
        organization_id=connector.organization_id,
        capability_id=definition.id,
        version=1,
        transport=CapabilityTransport.HTTP,
        risk_level=RiskLevel.L1,
        side_effect=SideEffect.NONE,
        permission_action="code.read",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        evidence_mapping={"type": "CODE"},
        timeout_seconds=5,
        data_classification=Classification.INTERNAL,
        checksum_sha256="0" * 64,
        created_at=datetime.now(UTC),
    )
    executor = AsyncMock()
    policy = PolicyEngine()
    policy.evaluate = AsyncMock(  # type: ignore[method-assign]
        return_value=Decision(id=uuid4(), effect=DecisionEffect.DENY)
    )
    gateway = CapabilityGateway({CapabilityTransport.HTTP.value: executor}, policy=policy)
    gateway._resolve = AsyncMock(  # type: ignore[method-assign]  # noqa: SLF001
        return_value=(definition, version, connector)
    )
    gateway.events = SimpleNamespace(append=AsyncMock())  # type: ignore[assignment]
    gateway._policy_event = AsyncMock()  # type: ignore[method-assign]  # noqa: SLF001
    gateway._audit = AsyncMock()  # type: ignore[method-assign]  # noqa: SLF001
    gateway.credentials.resolve = AsyncMock()  # type: ignore[method-assign]

    result = await gateway.invoke(
        AsyncMock(),
        GatewayRequest(
            principal=principal,
            capability_name=definition.name,
            payload={"operation": definition.name},
            resource={"connector": connector.name},
            environment="production",
            agent_name="engineering-agent",
            run_id=uuid4(),
        ),
    )

    assert result.status == GatewayStatus.DENIED
    assert gateway.credentials.resolve.await_count == 0
    assert executor.invoke.await_count == 0
    assert is_yunxiao_connector(connector)
    assert ConnectorResult is not None
