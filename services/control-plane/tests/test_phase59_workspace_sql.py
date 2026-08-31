from __future__ import annotations

import ast
import time
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from test_phase58_workspace_dashboards import _data_artifacts

from obsion.domain.enums import ArtifactKind
from obsion.security.auth import get_principal
from obsion.security.identity import Principal

_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "obsion"
WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web"


def _wait_terminal(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return run
        time.sleep(0.05)
    raise AssertionError(f"Run did not reach a terminal state: {run_id}")


def test_data_result_sql_is_validated_text_not_invented() -> None:
    _runtime, _run, _turn, _thread, artifacts = _data_artifacts()
    sql = next(item for item in artifacts if item.kind == ArtifactKind.SQL)
    assert sql.inline_content["sql"].startswith("SELECT region")
    assert sql.inline_content["validation"]["valid"] is True
    assert "FROM analytics.sales" in sql.inline_content["sql"]


def test_workspace_sql_lists_published_sql_only(client: TestClient) -> None:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "SQL workspace", "description": "Validated SQL ledger"},
    )
    assert workspace.status_code == 201, workspace.text
    workspace_id = workspace.json()["id"]
    empty = client.get(f"/api/v1/workspaces/{workspace_id}/sql")
    assert empty.status_code == 200, empty.text
    assert empty.json() == []

    uploaded = client.post(
        f"/api/v1/workspaces/{workspace_id}/artifacts",
        files={
            "file": (
                "paid_users.sql",
                b"SELECT COUNT(DISTINCT user_id) FROM payments.transactions",
                "text/sql",
            )
        },
        data={"title": "Paid users SQL", "kind": "SQL"},
    )
    assert uploaded.status_code == 201, uploaded.text
    assert uploaded.json()["kind"] == "SQL"

    report = client.post(
        f"/api/v1/workspaces/{workspace_id}/artifacts",
        files={"file": ("notes.md", b"# not sql", "text/markdown")},
        data={"title": "notes", "kind": "REPORT"},
    )
    assert report.status_code == 201, report.text

    listed = client.get(f"/api/v1/workspaces/{workspace_id}/sql")
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()] == [uploaded.json()["id"]]
    assert listed.json()[0]["kind"] == "SQL"


def test_conversation_and_knowledge_do_not_invent_workspace_sql(client: TestClient) -> None:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "No invented SQL", "description": "Greeting and knowledge stay text"},
    ).json()
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace["id"], "title": "Greeting"},
    ).json()
    created = client.post(
        f"/api/v1/threads/{thread['id']}/turns",
        json={"input": "你好"},
    )
    assert created.status_code == 202, created.text
    run = _wait_terminal(client, created.json()["run"]["id"])
    assert run["status"] == "COMPLETED", run
    assert client.get(f"/api/v1/workspaces/{workspace['id']}/sql").json() == []

    document = client.post(
        "/api/v1/knowledge/documents",
        files={
            "file": (
                "release.md",
                b"# Release policy\nEvery production release requires an owner and rollback plan.",
                "text/markdown",
            )
        },
        data={
            "source": "phase59",
            "external_id": "release-policy-sql",
            "title": "Release policy",
            "classification": "INTERNAL",
            "acl": '{"organization": true}',
        },
    )
    assert document.status_code == 201, document.text
    knowledge = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace["id"], "title": "Knowledge"},
    ).json()
    asked = client.post(
        f"/api/v1/threads/{knowledge['id']}/turns",
        json={"input": "What does the release policy require?"},
    )
    assert asked.status_code == 202, asked.text
    finished = _wait_terminal(client, asked.json()["run"]["id"])
    assert finished["status"] == "COMPLETED", finished
    kinds = [item["kind"] for item in client.get(f"/api/v1/runs/{finished['id']}/artifacts").json()]
    assert kinds[0] == "TEXT"
    assert "SQL" not in kinds
    assert client.get(f"/api/v1/workspaces/{workspace['id']}/sql").json() == []


def test_workspace_sql_is_tenant_scoped(client: TestClient) -> None:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Isolated SQL", "description": "Tenant boundary"},
    ).json()
    other = Principal(
        id=UUID("00000000-0000-7000-8000-000000000099"),
        organization_id=UUID("00000000-0000-7000-8000-000000000099"),
        external_id="cross-tenant-sql",
        display_name="Cross-tenant SQL",
        permissions=frozenset({"workspace.read.all"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: other
    try:
        listed = client.get(f"/api/v1/workspaces/{workspace['id']}/sql")
        assert listed.status_code == 404
    finally:
        client.app.dependency_overrides.pop(get_principal, None)


def test_workspace_sql_is_not_a_warehouse_fabric() -> None:
    source = (_SOURCE_ROOT / "artifacts" / "service.py").read_text(encoding="utf-8")
    assert "async def list_sql" in source
    tree = ast.parse(source)
    imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert "obsion.model_gateway.gateway" not in imports
    assert "asyncpg" not in source
    sql_view = (WEB_ROOT / "src" / "components" / "sql-view.tsx").read_text(encoding="utf-8")
    assert "工作区 SQL" in sql_view
    assert "不伪造仓库行" in sql_view
    sidebar = (WEB_ROOT / "src" / "components" / "sidebar.tsx").read_text(encoding="utf-8")
    assert 'id: "sql"' in sidebar
