import pytest
from fastapi.testclient import TestClient

from obsion.common.errors import ValidationError
from obsion.data_intelligence.sql_policy import SqlPolicyValidator


@pytest.fixture
def strict_validator() -> SqlPolicyValidator:
    return SqlPolicyValidator(default_limit=100, max_limit=500)


def test_explicit_limit_mode_rejects_unbounded_queries(
    strict_validator: SqlPolicyValidator,
) -> None:
    with pytest.raises(ValidationError) as caught:
        strict_validator.validate(
            "SELECT account_id FROM analytics.orders",
            allowed_tables={"analytics.orders"},
            allowed_columns={"account_id"},
            require_limit=True,
        )
    assert caught.value.code == "sql_limit_required"


def test_explain_is_read_only_but_analyze_is_not(strict_validator: SqlPolicyValidator) -> None:
    result = strict_validator.validate(
        "EXPLAIN (FORMAT JSON) SELECT account_id FROM analytics.orders LIMIT 5",
        allowed_tables={"analytics.orders"},
        allowed_columns={"account_id"},
        require_limit=True,
    )
    assert result.statement_type == "EXPLAIN"
    assert result.applied_limit == 5
    assert result.normalized_sql.startswith("EXPLAIN (FORMAT JSON)")

    with pytest.raises(ValidationError) as caught:
        strict_validator.validate(
            "EXPLAIN ANALYZE SELECT account_id FROM analytics.orders LIMIT 5",
            allowed_tables={"analytics.orders"},
            allowed_columns={"account_id"},
            require_limit=True,
        )
    assert caught.value.code == "sql_explain_execution_denied"


def test_scan_budget_is_deterministic_and_fail_closed(strict_validator: SqlPolicyValidator) -> None:
    with pytest.raises(ValidationError) as caught:
        strict_validator.validate(
            "SELECT account_id, amount FROM analytics.orders LIMIT 5",
            allowed_tables={"analytics.orders"},
            allowed_columns={"account_id", "amount"},
            require_limit=True,
            scan_budget=1,
        )
    assert caught.value.code == "sql_scan_budget_exceeded"


def test_unregistered_functions_are_not_an_escape_hatch(
    strict_validator: SqlPolicyValidator,
) -> None:
    with pytest.raises(ValidationError) as caught:
        strict_validator.validate(
            "SELECT internal_secret_decoder(account_id) FROM analytics.orders LIMIT 1",
            allowed_tables={"analytics.orders"},
            allowed_columns={"account_id"},
            require_limit=True,
        )
    assert caught.value.code == "sql_function_denied"


def _create_source(client: TestClient) -> str:
    connector = client.post(
        "/api/v1/admin/connectors",
        json={
            "name": "phase15-analytics",
            "connector_type": "postgres",
            "environment": "test",
            "status": "ACTIVE",
            "configuration": {"role": "read_replica", "read_only": True},
            "declared_grants": ["SELECT"],
        },
    )
    assert connector.status_code == 201, connector.text
    source = client.post(
        "/api/v1/admin/data/sources",
        json={
            "name": "phase15-read-replica",
            "dialect": "postgres",
            "connector_id": connector.json()["id"],
            "environment": "test",
            "query_policy": {"scan_budget": 1_000_000},
        },
    )
    assert source.status_code == 201, source.text
    table = client.post(
        "/api/v1/admin/data/tables",
        json={
            "data_source_id": source.json()["id"],
            "schema_name": "analytics",
            "table_name": "orders",
            "owner": "data-platform",
        },
    )
    assert table.status_code == 201, table.text
    for name in ("account_id", "amount"):
        column = client.post(
            "/api/v1/admin/data/columns",
            json={
                "table_id": table.json()["id"],
                "name": name,
                "data_type": "text",
            },
        )
        assert column.status_code == 201, column.text
    return source.json()["id"]


def test_sql_explain_route_returns_auditable_policy_plan(client: TestClient) -> None:
    source_id = _create_source(client)
    response = client.post(
        "/api/v1/data/sql/explain",
        json={
            "data_source_id": source_id,
            "sql": "SELECT account_id FROM analytics.orders LIMIT 5",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["valid"] is True
    assert body["plan"]["policy"]["read_only"] is True
    assert body["audit_id"]
    audit = client.get("/api/v1/admin/audit").json()
    assert any(item["id"] == body["audit_id"] and item["action"] == "sql.explain" for item in audit)

    unbounded = client.post(
        "/api/v1/data/sql/validate",
        json={"data_source_id": source_id, "sql": "SELECT account_id FROM analytics.orders"},
    )
    assert unbounded.status_code == 422, unbounded.text
    assert unbounded.json()["code"] == "sql_limit_required"
