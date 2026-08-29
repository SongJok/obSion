from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Protocol

import pytest
from fastapi import HTTPException, Query
from fastapi.testclient import TestClient

from obsion.common.errors import ObsionError
from obsion.config import Settings
from obsion.main import create_app


class _Response(Protocol):
    @property
    def status_code(self) -> int: ...

    @property
    def headers(self) -> Any: ...

    @property
    def text(self) -> str: ...

    def json(self) -> Any: ...


_ERROR_BODY_KEYS = {"code", "message", "correlation_id", "details"}
_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})


@pytest.fixture
def error_contract_client(app_settings: Settings) -> Iterator[TestClient]:
    app = create_app(app_settings)

    @app.get("/_contract/query")
    async def query_probe(value: int = Query(ge=1)) -> dict[str, int]:
        return {"value": value}

    @app.post("/_contract/http-exception")
    async def http_exception_probe() -> None:
        raise HTTPException(
            status_code=429,
            detail={"secret": "HTTP-DETAIL-CANARY"},
            headers={
                "Retry-After": "7",
                "X-Request-ID": "spoofed-request-id",
                "X-Untrusted": "must-not-be-forwarded",
            },
        )

    @app.get("/_contract/domain-error")
    async def domain_error_probe() -> None:
        raise ObsionError(
            code="artifact_store_unavailable",
            message="Artifact store is unavailable",
            status_code=503,
            details={"secret": "DOMAIN-SECRET-CANARY", "attempt": 2},
        )

    @app.get("/_contract/internal-error")
    async def internal_error_probe() -> None:
        raise RuntimeError("INTERNAL-ERROR-CANARY")

    with TestClient(
        app,
        raise_server_exceptions=False,
        headers={"Authorization": f"Bearer {app_settings.dev_bearer_token.get_secret_value()}"},
    ) as test_client:
        yield test_client


def _assert_error_body(
    response: _Response,
    *,
    status_code: int,
    code: str,
    message: str,
) -> dict[str, object]:
    assert response.status_code == status_code
    assert response.headers["content-type"].startswith("application/json")
    raw_body = response.json()
    assert isinstance(raw_body, dict)
    body: dict[str, object] = raw_body
    assert set(body) == _ERROR_BODY_KEYS
    assert body["code"] == code
    assert body["message"] == message
    assert body["correlation_id"] == response.headers["X-Request-ID"]
    assert isinstance(body["correlation_id"], str)
    assert body["correlation_id"]
    assert isinstance(body["details"], dict)
    assert "detail" not in body
    return body


def _assert_safe_validation_response(response: _Response) -> None:
    body = _assert_error_body(
        response,
        status_code=422,
        code="request_validation_failed",
        message="Request validation failed",
    )
    assert body["details"] == {}
    assert response.headers["X-Request-ID"] == "validation-request-id"
    assert "CANARY" not in response.text


def test_path_validation_errors_use_safe_error_body(
    error_contract_client: TestClient,
) -> None:
    response = error_contract_client.get(
        "/api/v1/runs/not-a-uuid",
        headers={"X-Request-ID": "validation-request-id"},
    )
    _assert_safe_validation_response(response)


def test_query_validation_errors_use_safe_error_body(
    error_contract_client: TestClient,
) -> None:
    response = error_contract_client.get(
        "/_contract/query",
        params={"value": "QUERY-CANARY"},
        headers={"X-Request-ID": "validation-request-id"},
    )
    _assert_safe_validation_response(response)


def test_body_validation_errors_use_safe_error_body(
    error_contract_client: TestClient,
) -> None:
    response = error_contract_client.post(
        "/api/v1/workspaces",
        json={
            "name": "x" * 241 + "BODY-CANARY",
            "classification": "NOT-A-CLASSIFICATION",
        },
        headers={"X-Request-ID": "validation-request-id"},
    )
    _assert_safe_validation_response(response)


def test_framework_404_uses_error_body_and_does_not_echo_path(
    error_contract_client: TestClient,
) -> None:
    response = error_contract_client.get("/missing/ROUTE-CANARY")

    body = _assert_error_body(
        response,
        status_code=404,
        code="resource_not_found",
        message="The requested resource was not found",
    )
    assert body["details"] == {}
    assert "ROUTE-CANARY" not in response.text


def test_framework_405_preserves_allow_header(
    error_contract_client: TestClient,
) -> None:
    response = error_contract_client.post(
        "/health/live",
        headers={"X-Request-ID": "method-request-id"},
    )

    body = _assert_error_body(
        response,
        status_code=405,
        code="method_not_allowed",
        message="The requested method is not allowed",
    )
    assert body["details"] == {}
    assert "GET" in response.headers["Allow"]
    assert response.headers["X-Request-ID"] == "method-request-id"


def test_unmodeled_http_exception_fails_closed_and_preserves_protocol_headers(
    error_contract_client: TestClient,
) -> None:
    response = error_contract_client.post(
        "/_contract/http-exception",
        headers={"X-Request-ID": "canonical-request-id"},
    )

    body = _assert_error_body(
        response,
        status_code=500,
        code="internal_error",
        message="The request could not be completed",
    )
    assert body["details"] == {}
    assert response.headers["Retry-After"] == "7"
    assert response.headers["X-Request-ID"] == "canonical-request-id"
    assert "X-Untrusted" not in response.headers
    assert "HTTP-DETAIL-CANARY" not in response.text


def test_obsion_error_preserves_semantics_and_redacts_details(
    error_contract_client: TestClient,
) -> None:
    response = error_contract_client.get(
        "/_contract/domain-error",
        headers={"X-Request-ID": "domain-request-id"},
    )

    body = _assert_error_body(
        response,
        status_code=503,
        code="artifact_store_unavailable",
        message="Artifact store is unavailable",
    )
    assert body["details"] == {"secret": "[REDACTED]", "attempt": 2}
    assert "DOMAIN-SECRET-CANARY" not in response.text


def test_unhandled_exception_uses_safe_error_body(
    error_contract_client: TestClient,
) -> None:
    response = error_contract_client.get(
        "/_contract/internal-error",
        headers={"X-Request-ID": "internal-request-id"},
    )

    body = _assert_error_body(
        response,
        status_code=500,
        code="internal_error",
        message="An internal error occurred",
    )
    assert body["details"] == {}
    assert response.headers["X-Request-ID"] == "internal-request-id"
    assert "INTERNAL-ERROR-CANARY" not in response.text


def test_unhandled_exception_preserves_cors_visibility(
    error_contract_client: TestClient,
) -> None:
    response = error_contract_client.get(
        "/_contract/internal-error",
        headers={
            "Origin": "http://testserver",
            "X-Request-ID": "cors-request-id",
        },
    )

    _assert_error_body(
        response,
        status_code=500,
        code="internal_error",
        message="An internal error occurred",
    )
    assert response.headers["Access-Control-Allow-Origin"] == "http://testserver"
    assert "X-Request-ID" in {
        item.strip() for item in response.headers["Access-Control-Expose-Headers"].split(",")
    }


@pytest.mark.parametrize(
    "request_id",
    [
        "",
        "x" * 129,
        "contains space",
    ],
)
def test_unsafe_request_ids_are_not_reflected(
    error_contract_client: TestClient,
    request_id: str,
) -> None:
    response = error_contract_client.get(
        "/missing",
        headers={"X-Request-ID": request_id},
    )

    body = _assert_error_body(
        response,
        status_code=404,
        code="resource_not_found",
        message="The requested resource was not found",
    )
    assert body["correlation_id"] != request_id


def test_openapi_uses_error_body_for_framework_failures() -> None:
    document = create_app().openapi()

    assert "ErrorBody" in document["components"]["schemas"]
    assert "HTTPValidationError" not in document["components"]["schemas"]
    for path_item in document["paths"].values():
        for method, operation in path_item.items():
            if method not in _HTTP_METHODS:
                continue
            for status in ("404", "405", "422", "500"):
                response = operation["responses"][status]
                assert response["content"]["application/json"]["schema"] == {
                    "$ref": "#/components/schemas/ErrorBody"
                }
    assert "HTTPValidationError" not in str(document)
