import time

from fastapi.testclient import TestClient


def _create_catalog(client: TestClient) -> dict[str, str]:
    connector = client.post(
        "/api/v1/admin/connectors",
        json={
            "name": "phase14-semantic-db",
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
            "name": "phase14-readonly",
            "dialect": "postgres",
            "connector_id": connector.json()["id"],
            "environment": "test",
            "classification": "INTERNAL",
            "query_policy": {"max_rows": 500},
        },
    )
    assert source.status_code == 201, source.text
    table = client.post(
        "/api/v1/admin/data/tables",
        json={
            "data_source_id": source.json()["id"],
            "schema_name": "payments",
            "table_name": "transactions",
            "description": "Governed payment facts",
            "owner": "payment-team",
            "classification": "INTERNAL",
            "row_policy": {},
        },
    )
    assert table.status_code == 201, table.text
    for name in ("user_id", "paid_at"):
        column = client.post(
            "/api/v1/admin/data/columns",
            json={
                "table_id": table.json()["id"],
                "name": name,
                "data_type": "uuid" if name == "user_id" else "timestamp",
                "classification": "INTERNAL",
            },
        )
        assert column.status_code == 201, column.text
    metric = client.post(
        "/api/v1/admin/data/metrics",
        json={
            "name": "paid_user_count",
            "display_name": "Paid users",
            "expression": "COUNT(DISTINCT user_id)",
            "filters": {},
            "time_column": "paid_at",
            "source_table_id": table.json()["id"],
            "owner": "payment-team",
            "synonyms": ["付费人数"],
            "validated": True,
        },
    )
    assert metric.status_code == 201, metric.text
    return {
        "source_id": source.json()["id"],
        "table_id": table.json()["id"],
        "metric_id": metric.json()["id"],
    }


def _wait_terminal(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return run
        time.sleep(0.05)
    raise AssertionError(f"Run did not reach a terminal state: {run}")


def test_semantic_catalog_definitions_and_relations_are_tenant_scoped(client: TestClient) -> None:
    catalog = _create_catalog(client)
    entity = client.post(
        "/api/v1/admin/data/entities",
        json={
            "name": "payer",
            "display_name": "Payer",
            "primary_key_expression": "user_id",
            "source_table_id": catalog["table_id"],
            "owner": "payment-team",
        },
    )
    assert entity.status_code == 201, entity.text
    entity_revision = client.post(
        "/api/v1/admin/data/entities",
        json={
            "name": "payer",
            "display_name": "Payer (revised)",
            "primary_key_expression": "user_id",
            "source_table_id": catalog["table_id"],
            "owner": "payment-team",
        },
    )
    assert entity_revision.status_code == 201, entity_revision.text
    assert entity.json()["version"] == 1
    assert entity_revision.json()["version"] == 2

    relation = client.post(
        "/api/v1/admin/data/relations",
        json={
            "source_entity_id": entity.json()["id"],
            "target_entity_id": entity_revision.json()["id"],
            "relation_type": "same_as",
            "join_expression": "payer.user_id = payer.user_id",
            "cardinality": "1:1",
        },
    )
    assert relation.status_code == 201, relation.text
    rule = client.post(
        "/api/v1/admin/data/rules",
        json={
            "name": "successful_payment",
            "expression": {"column": "status", "operator": "=", "value": "SUCCESS"},
            "owner": "payment-team",
        },
    )
    assert rule.status_code == 201, rule.text
    synonym = client.post(
        "/api/v1/admin/data/synonyms",
        json={
            "term": "付费人数",
            "locale": "zh-CN",
            "target_type": "METRIC",
            "target_id": catalog["metric_id"],
        },
    )
    assert synonym.status_code == 201, synonym.text
    time_definition = client.post(
        "/api/v1/admin/data/time-definitions",
        json={
            "name": "business_day",
            "display_name": "Business day",
            "expression": "paid_at",
            "timezone": "Asia/Shanghai",
            "grains": ["day", "week"],
            "owner": "payment-team",
        },
    )
    assert time_definition.status_code == 201, time_definition.text

    summary = client.get("/api/v1/admin/data/catalog")
    assert summary.status_code == 200, summary.text
    assert summary.json()["entities"] == 2
    assert summary.json()["relations"] == 1
    assert summary.json()["rules"] == 1
    assert summary.json()["time_definitions"] == 1
    assert summary.json()["synonyms"] == 1


def test_paid_user_semantic_compile_is_stable_and_unregistered_metrics_fail(
    client: TestClient,
) -> None:
    catalog = _create_catalog(client)
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Phase 14", "description": "Semantic compiler"},
    )
    assert workspace.status_code == 201, workspace.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": "Paid users"},
    )
    assert thread.status_code == 201, thread.text

    first = client.post(
        "/api/v1/data/query",
        json={"thread_id": thread.json()["id"], "question": "付费人数"},
    )
    assert first.status_code == 202, first.text
    first_run = _wait_terminal(client, first.json()["run"]["id"])
    assert first_run["intent"]["metrics"][0]["id"] == catalog["metric_id"]
    first_sql = first_run["plan"]["steps"][0]["payload"]["sql"]
    assert "COUNT(DISTINCT user_id)" in first_sql

    second = client.post(
        "/api/v1/data/query",
        json={"thread_id": thread.json()["id"], "question": "付费人数"},
    )
    assert second.status_code == 202, second.text
    second_run = _wait_terminal(client, second.json()["run"]["id"])
    second_sql = second_run["plan"]["steps"][0]["payload"]["sql"]
    assert second_sql == first_sql

    unresolved = client.post(
        "/api/v1/data/query",
        json={"thread_id": thread.json()["id"], "question": "未注册的业务指标"},
    )
    assert unresolved.status_code == 422, unresolved.text
    assert unresolved.json()["code"] == "metric_not_resolved"
