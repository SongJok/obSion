from uuid import UUID

from fastapi.testclient import TestClient

from obsion.security.auth import get_principal
from obsion.security.identity import Principal


def _create_validated_metric(client: TestClient) -> dict[str, str]:
    connector = client.post(
        "/api/v1/admin/connectors",
        json={
            "name": "analytics-catalog",
            "connector_type": "postgres",
            "environment": "test",
            "configuration": {},
            "declared_grants": ["SELECT"],
            "allowed_egress": [],
        },
    )
    assert connector.status_code == 201, connector.text

    source = client.post(
        "/api/v1/admin/data/sources",
        json={
            "name": "analytics-readonly",
            "dialect": "postgres",
            "connector_id": connector.json()["id"],
            "environment": "test",
            "classification": "INTERNAL",
            "query_policy": {"max_rows": 1000},
        },
    )
    assert source.status_code == 201, source.text
    assert source.json()["read_only"] is True

    table = client.post(
        "/api/v1/admin/data/tables",
        json={
            "data_source_id": source.json()["id"],
            "schema_name": "analytics",
            "table_name": "daily_revenue",
            "description": "Governed daily revenue facts",
            "owner": "data-platform",
            "classification": "INTERNAL",
            "row_policy": {"regions": ["CN"]},
        },
    )
    assert table.status_code == 201, table.text

    metric = client.post(
        "/api/v1/admin/data/metrics",
        json={
            "name": "net_revenue",
            "display_name": "Net revenue",
            "expression": "sum(net_revenue_cents) / 100.0",
            "filters": {"status": "settled"},
            "time_column": "business_date",
            "source_table_id": table.json()["id"],
            "owner": "finance-analytics",
            "synonyms": ["revenue", "sales"],
            "validated": True,
        },
    )
    assert metric.status_code == 201, metric.text
    return {
        "metric_id": metric.json()["id"],
        "table_id": table.json()["id"],
        "source_id": source.json()["id"],
    }


def test_metric_catalog_exposes_governed_definition_and_lineage(client: TestClient) -> None:
    created = _create_validated_metric(client)

    catalog = client.get("/api/v1/data/metrics")
    assert catalog.status_code == 200, catalog.text
    assert catalog.json() == [
        {
            "id": created["metric_id"],
            "name": "net_revenue",
            "display_name": "Net revenue",
            "version": 1,
            "expression": "sum(net_revenue_cents) / 100.0",
            "filters": {"status": "settled"},
            "time_column": "business_date",
            "source_table_id": created["table_id"],
            "owner": "finance-analytics",
            "synonyms": ["revenue", "sales"],
            "validated": True,
            "created_at": catalog.json()[0]["created_at"],
            "updated_at": catalog.json()[0]["updated_at"],
        }
    ]

    lineage = client.get(f"/api/v1/data/lineage/{created['metric_id']}")
    assert lineage.status_code == 200, lineage.text
    assert lineage.json() == {
        "metric": {"id": created["metric_id"], "name": "net_revenue", "version": 1},
        "table": {
            "id": created["table_id"],
            "name": "analytics.daily_revenue",
            "owner": "data-platform",
        },
        "data_source": {
            "id": created["source_id"],
            "name": "analytics-readonly",
            "environment": "test",
            "read_only": True,
        },
    }


def test_metric_catalog_preserves_tenant_boundary(client: TestClient) -> None:
    created = _create_validated_metric(client)
    other_tenant = Principal(
        id=UUID("00000000-0000-7000-8000-000000000099"),
        organization_id=UUID("00000000-0000-7000-8000-000000000099"),
        external_id="cross-tenant-data-reader",
        display_name="Cross-tenant Data Reader",
        permissions=frozenset({"data.read"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: other_tenant
    try:
        assert client.get("/api/v1/data/metrics").json() == []
        assert client.get(f"/api/v1/data/lineage/{created['metric_id']}").status_code == 404
    finally:
        client.app.dependency_overrides.pop(get_principal, None)
