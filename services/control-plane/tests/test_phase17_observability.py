from uuid import uuid4

import httpx
import pytest

from obsion.capabilities.connectors import ConnectorContext, HttpJsonExecutor
from obsion.capabilities.observability import (
    ObservabilityResponseError,
    normalize_response,
)
from obsion.config import Environment, Settings
from obsion.db.models import Connector
from obsion.domain.enums import ConnectorStatus
from obsion.security.identity import Principal


def test_prometheus_series_normalize_to_unified_observability_events() -> None:
    normalized = normalize_response(
        {
            "data": {
                "result": [
                    {
                        "metric": {"service": "payments", "instance": "pod-1"},
                        "values": [[1724889600, "0.98"], [1724889660, "0.97"]],
                    }
                ]
            }
        },
        operation="metric.query",
        default_service="*",
        default_environment="production",
    )
    assert normalized["count"] == 2
    event = normalized["events"][0]
    assert set(event) == {
        "timestamp",
        "service",
        "environment",
        "trace_id",
        "request_id",
        "user_id_hash",
        "order_id_hash",
        "deployment_id",
        "commit_id",
        "host",
        "pod",
        "severity",
        "attributes",
    }
    assert event["service"] == "payments"
    assert event["environment"] == "production"
    assert event["attributes"]["value"] == "0.98"
    assert "token" not in event["attributes"]


def _connector() -> Connector:
    return Connector(
        id=uuid4(),
        organization_id=uuid4(),
        name="observability-test",
        connector_type="observability-http",
        status=ConnectorStatus.ACTIVE,
        environment="development",
        endpoint="http://observability.test/query",
        configuration={"protocol": "observability.v1"},
        declared_grants=["metrics.read"],
        allowed_egress=["observability.test:80"],
    )


def _context(connector: Connector) -> ConnectorContext:
    return ConnectorContext(
        principal=Principal(
            id=uuid4(),
            organization_id=connector.organization_id,
            external_id="phase17-user",
            display_name="Phase 17 User",
        ),
        run_id=uuid4(),
        step_id=None,
    )


@pytest.mark.asyncio
async def test_observability_http_executor_posts_bounded_operation_and_normalizes() -> None:
    seen: dict[str, object] = {}

    async def responder(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.read()
        return httpx.Response(
            200,
            json={
                "events": [
                    {
                        "timestamp": "2026-08-29T00:00:00Z",
                        "service": "payments",
                        "environment": "production",
                        "severity": "warning",
                        "message": "timeout rate increased",
                    }
                ]
            },
        )

    connector = _connector()
    executor = HttpJsonExecutor(
        Settings(environment=Environment.TEST), transport=httpx.MockTransport(responder)
    )
    result = await executor.invoke(
        connector,
        {
            "operation": "metric.query",
            "service": "payments",
            "start_time": "2026-08-29T00:00:00Z",
            "end_time": "2026-08-29T01:00:00Z",
        },
        "provider-token",
        _context(connector),
    )
    assert result.data["operation"] == "metric.query"
    assert result.data["count"] == 1
    assert result.data["events"][0]["severity"] == "warning"
    assert b'"operation":"metric.query"' in seen["body"]  # type: ignore[operator]


@pytest.mark.asyncio
async def test_observability_http_executor_rejects_provider_error_payload() -> None:
    async def responder(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(200, json={"error": "provider internal detail"})

    connector = _connector()
    executor = HttpJsonExecutor(
        Settings(environment=Environment.TEST), transport=httpx.MockTransport(responder)
    )
    with pytest.raises(ObservabilityResponseError) as caught:
        await executor.invoke(
            connector,
            {
                "operation": "metric.query",
                "service": "payments",
                "start_time": "2026-08-29T00:00:00Z",
                "end_time": "2026-08-29T01:00:00Z",
            },
            None,
            _context(connector),
        )
    assert caught.value.code == "observability_response_invalid"
