from uuid import uuid4

import httpx
import pytest

from obsion.capabilities.connectors import ConnectorContext, HttpJsonExecutor
from obsion.capabilities.engineering import normalize_response as normalize_engineering
from obsion.capabilities.observability import normalize_response as normalize_observability
from obsion.common.errors import ValidationError
from obsion.config import Environment, Settings
from obsion.db.models import Connector
from obsion.domain.enums import ConnectorStatus
from obsion.harness.planner import Planner
from obsion.security.identity import Principal


def test_trace_spans_normalize_to_unified_observability_events() -> None:
    normalized = normalize_observability(
        {
            "spans": [
                {
                    "timestamp": "2026-08-29T08:01:00Z",
                    "service": "payments",
                    "traceId": "trace-abc",
                    "span_id": "span-1",
                    "parent_span_id": None,
                    "span_name": "POST /order/create",
                    "duration_ms": 620,
                    "status_code": "ERROR",
                }
            ]
        },
        operation="trace.search",
        default_service="*",
        default_environment="production",
    )
    event = normalized["events"][0]
    assert normalized["operation"] == "trace.search"
    assert event["trace_id"] == "trace-abc"
    assert event["service"] == "payments"
    assert event["attributes"]["span_id"] == "span-1"
    assert event["attributes"]["span_name"] == "POST /order/create"
    assert "token" not in event["attributes"]


def test_config_diff_and_k8s_status_normalize_to_read_only_change_events() -> None:
    config = normalize_engineering(
        {
            "configs": [
                {
                    "observed_at": "2026-08-29T08:00:00Z",
                    "cluster": "prod-cluster",
                    "service": "payments",
                    "key": "timeout_ms",
                    "previous": "password=super-secret",
                    "current": "180",
                }
            ]
        },
        operation="config.diff",
        default_repository="*",
        default_environment="production",
    )
    item = config["items"][0]
    assert item["repository"] == "prod-cluster"
    assert item["service"] == "payments"
    assert item["attributes"]["key"] == "timeout_ms"
    assert "super-secret" not in str(item["attributes"]["previous"])

    status = normalize_engineering(
        {
            "workloads": [
                {
                    "observed_at": "2026-08-29T08:00:00Z",
                    "cluster": "prod-cluster",
                    "service": "payments",
                    "namespace": "payments",
                    "workload": "payment-api",
                    "replicas": 3,
                    "ready": 2,
                    "status": "Degraded",
                }
            ]
        },
        operation="k8s.status",
        default_repository="*",
        default_environment="production",
    )
    workload = status["items"][0]
    assert workload["status"] == "Degraded"
    assert workload["attributes"]["namespace"] == "payments"
    assert workload["attributes"]["ready"] == 2


def test_incident_plan_includes_trace_config_and_k8s_when_registered() -> None:
    plan = Planner().create(
        {
            "route": "INCIDENT",
            "question": "生产环境 p99 latency 异常的根因是什么？",
            "time_range": {"start": "2026-08-29T08:00:00Z", "end": "2026-08-29T09:00:00Z"},
            "service": "payments",
        },
        available_capabilities=frozenset(
            {"metric.query", "trace.search", "config.diff", "k8s.status"}
        ),
    )
    assert [step.capability for step in plan.steps] == [
        "metric.query",
        "trace.search",
        "config.diff",
        "k8s.status",
    ]
    assert all(step.payload.get("service") == "payments" for step in plan.steps)


def _observability_connector() -> Connector:
    return Connector(
        id=uuid4(),
        organization_id=uuid4(),
        name="observability-phase22",
        connector_type="observability-http",
        status=ConnectorStatus.ACTIVE,
        environment="development",
        endpoint="http://observability.test/query",
        configuration={"protocol": "observability.v1"},
        declared_grants=["traces.read"],
        allowed_egress=["observability.test:80"],
    )


def _engineering_connector() -> Connector:
    return Connector(
        id=uuid4(),
        organization_id=uuid4(),
        name="engineering-phase22",
        connector_type="engineering-http",
        status=ConnectorStatus.ACTIVE,
        environment="development",
        endpoint="http://engineering.test/query",
        configuration={"protocol": "engineering.v1"},
        declared_grants=["config.read", "runtime.read"],
        allowed_egress=["engineering.test:80"],
    )


def _context(connector: Connector) -> ConnectorContext:
    return ConnectorContext(
        principal=Principal(
            id=uuid4(),
            organization_id=connector.organization_id,
            external_id="phase22-user",
            display_name="Phase 22 User",
        ),
        run_id=uuid4(),
        step_id=None,
    )


@pytest.mark.asyncio
async def test_trace_search_http_executor_is_read_only_and_omits_secrets() -> None:
    seen: dict[str, object] = {}

    async def responder(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization")
        seen["body"] = request.read()
        return httpx.Response(
            200,
            json={
                "traces": [
                    {
                        "timestamp": "2026-08-29T08:01:00Z",
                        "service": "payments",
                        "trace_id": "trace-abc",
                        "token": "should-not-leak",
                    }
                ]
            },
        )

    connector = _observability_connector()
    executor = HttpJsonExecutor(
        Settings(environment=Environment.TEST), transport=httpx.MockTransport(responder)
    )
    result = await executor.invoke(
        connector,
        {
            "operation": "trace.search",
            "service": "payments",
            "start_time": "2026-08-29T08:00:00Z",
            "end_time": "2026-08-29T09:00:00Z",
        },
        "provider-token",
        _context(connector),
    )
    assert seen["authorization"] == "Bearer provider-token"
    assert result.data["operation"] == "trace.search"
    assert result.data["events"][0]["trace_id"] == "trace-abc"
    assert "token" not in result.data["events"][0]["attributes"]
    assert b"should-not-leak" not in seen["body"]  # type: ignore[operator]


@pytest.mark.asyncio
async def test_k8s_status_http_executor_rejects_write_shaped_operations() -> None:
    connector = _engineering_connector()
    executor = HttpJsonExecutor(Settings(environment=Environment.TEST))
    with pytest.raises(ValidationError) as caught:
        await executor.invoke(
            connector,
            {"operation": "k8s.restart", "service": "payments"},
            None,
            _context(connector),
        )
    assert caught.value.code == "engineering_operation_invalid"
