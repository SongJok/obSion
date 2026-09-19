import time
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import select

from obsion.capabilities.connectors import ConnectorResult
from obsion.db.models import (
    CapabilityBinding,
    CapabilityDefinition,
    CapabilityVersion,
    Connector,
)
from obsion.domain.enums import ConnectorStatus


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


def test_time_window_followup_rebuilds_query_contract_without_old_semantics(
    client: TestClient,
    monkeypatch,
) -> None:
    class SyntheticReadOnlyExecutor:
        async def invoke(self, connector, payload, credential, context):
            del payload, credential, context
            return ConnectorResult(
                data={
                    "columns": ["paid_user_count"],
                    "rows": [{"paid_user_count": 17}],
                    "row_count": 1,
                },
                source=connector.name,
                resource="h02-statistics-fixture",
                observed_at=datetime.now(UTC),
            )

    monkeypatch.setitem(
        client.app.state.capability_gateway.executors,
        "SQL_PROXY",
        SyntheticReadOnlyExecutor(),
    )
    catalog = _create_catalog(client)

    async def bind_test_source() -> None:
        async with client.app.state.database.sessions() as session, session.begin():
            connector = await session.scalar(
                select(Connector).where(Connector.name == "phase14-semantic-db")
            )
            version = await session.scalar(
                select(CapabilityVersion)
                .join(
                    CapabilityDefinition,
                    CapabilityDefinition.id == CapabilityVersion.capability_id,
                )
                .where(CapabilityDefinition.name == "data.query")
                .order_by(CapabilityVersion.version.desc())
            )
            assert connector is not None and version is not None
            connector.status = ConnectorStatus.ACTIVE
            connector.declared_grants = [version.permission_action]
            session.add(
                CapabilityBinding(
                    organization_id=connector.organization_id,
                    capability_version_id=version.id,
                    connector_id=connector.id,
                    environment="test",
                    resource_selector={},
                    enabled=True,
                )
            )

    client.portal.call(bind_test_source)
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "H02 semantic amendment", "description": "TaskContract revision"},
    ).json()
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace["id"], "title": "Time-window amendment"},
    ).json()

    first = client.post(
        "/api/v1/data/query",
        json={"thread_id": thread["id"], "question": "查看最近7天的付费人数"},
    )
    assert first.status_code == 202, first.text
    first_run = _wait_terminal(client, first.json()["run"]["id"])
    first_steps = client.get(f"/api/v1/runs/{first_run['id']}/steps").json()
    assert first_run["status"] == "COMPLETED", [
        (step["name"], step["status"], step["error_code"])
        for step in first_steps
        if step["status"] == "FAILED"
    ]

    changed = client.post(
        f"/api/v1/threads/{thread['id']}/turns",
        json={"input": "把时间改成昨天"},
    )
    assert changed.status_code == 202, changed.text
    changed_run = _wait_terminal(client, changed.json()["run"]["id"])
    assert changed_run["status"] == "COMPLETED", changed_run

    first_contract = first_run["intent"]["task_contract"]
    changed_contract = changed_run["intent"]["task_contract"]
    assert changed_run["intent"]["route"] == "DATA"
    assert changed_run["intent"]["metrics"] == [
        {
            "id": catalog["metric_id"],
            "name": "paid_user_count",
            "display_name": "Paid users",
        }
    ]
    assert changed_contract["revision"] == first_contract["revision"] + 1
    assert changed_contract["parent_fingerprint"] == first_contract["fingerprint"]
    assert "TIME_WINDOW" in changed_contract["changed_fields"]
    assert {"DATA_QUERY", "STATISTICS", "CLAIMS"} <= set(changed_contract["invalidated_outputs"])
    first_parameters = first_run["plan"]["steps"][0]["payload"]["parameters"]
    changed_parameters = changed_run["plan"]["steps"][0]["payload"]["parameters"]
    assert changed_parameters != first_parameters
    assert changed_run["plan"]["task_contract"] == changed_contract
    assert changed_run["plan"]["invalidated_history_excluded"] is True
    assert changed_run["plan"]["conversation_source_run_ids"] == []
    artifacts = client.get(f"/api/v1/runs/{changed_run['id']}/artifacts").json()
    assert artifacts
    assert all(
        item["lineage"].get("task_contract_fingerprint") == changed_contract["fingerprint"]
        for item in artifacts
    )
