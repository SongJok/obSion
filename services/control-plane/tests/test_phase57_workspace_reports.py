from __future__ import annotations

import ast
import time
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

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


def test_conversation_runs_do_not_invent_workspace_reports(client: TestClient) -> None:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Report workspace", "description": "Greeting is not a report"},
    )
    assert workspace.status_code == 201, workspace.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": "Greeting"},
    )
    assert thread.status_code == 201, thread.text
    created = client.post(
        f"/api/v1/threads/{thread.json()['id']}/turns",
        json={"input": "你好"},
    )
    assert created.status_code == 202, created.text
    run = _wait_terminal(client, created.json()["run"]["id"])
    assert run["status"] == "COMPLETED", run
    artifacts = client.get(f"/api/v1/runs/{run['id']}/artifacts").json()
    assert [item["kind"] for item in artifacts] == ["TEXT"]
    reports = client.get(f"/api/v1/workspaces/{workspace.json()['id']}/reports")
    assert reports.status_code == 200, reports.text
    assert reports.json() == []


def test_knowledge_citations_publish_a_workspace_report(client: TestClient) -> None:
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
            "source": "phase57",
            "external_id": "release-policy-report",
            "title": "Release policy",
            "classification": "INTERNAL",
            "acl": '{"organization": true}',
        },
    )
    assert document.status_code == 201, document.text
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Knowledge reports", "description": "Cited answers become reports"},
    ).json()
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace["id"], "title": "Knowledge report"},
    ).json()
    created = client.post(
        f"/api/v1/threads/{thread['id']}/turns",
        json={"input": "What does the release policy require?"},
    )
    assert created.status_code == 202, created.text
    run = _wait_terminal(client, created.json()["run"]["id"])
    assert run["status"] == "COMPLETED", run
    artifacts = client.get(f"/api/v1/runs/{run['id']}/artifacts").json()
    kinds = [item["kind"] for item in artifacts]
    assert kinds[0] == "TEXT"
    assert kinds.count("REPORT") == 1
    answer = next(item for item in artifacts if item["kind"] == "TEXT")
    report = next(item for item in artifacts if item["kind"] == "REPORT")
    assert report["title"] == "Workspace report"
    assert report["lineage"]["answer_artifact_id"] == answer["id"]
    assert report["lineage"]["source"] == "workspace-report"
    assert answer["inline_content"]["citations"]
    assert report["inline_content"]["citations"]
    assert "rollback" in report["inline_content"]["markdown"]

    listed = client.get(f"/api/v1/workspaces/{workspace['id']}/reports").json()
    assert [item["id"] for item in listed] == [report["id"]]


def test_workspace_reports_are_tenant_scoped(client: TestClient) -> None:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Isolated reports", "description": "Tenant boundary"},
    ).json()
    other = Principal(
        id=UUID("00000000-0000-7000-8000-000000000099"),
        organization_id=UUID("00000000-0000-7000-8000-000000000099"),
        external_id="cross-tenant-reports",
        display_name="Cross-tenant Reports",
        permissions=frozenset({"workspace.read.all"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: other
    try:
        listed = client.get(f"/api/v1/workspaces/{workspace['id']}/reports")
        assert listed.status_code == 404
    finally:
        client.app.dependency_overrides.pop(get_principal, None)


def test_workspace_reports_are_not_a_system_or_dashboard_fabric() -> None:
    source = (_SOURCE_ROOT / "harness" / "runtime.py").read_text(encoding="utf-8")
    assert "_workspace_report_artifact" in source
    tree = ast.parse((_SOURCE_ROOT / "artifacts" / "service.py").read_text(encoding="utf-8"))
    imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert "obsion.model_gateway.gateway" not in imports
    reports_view = (WEB_ROOT / "src" / "components" / "reports-view.tsx").read_text(
        encoding="utf-8"
    )
    assert "工作区报告" in reports_view
    assert "不伪造仪表盘" in reports_view
    sidebar = (WEB_ROOT / "src" / "components" / "sidebar.tsx").read_text(encoding="utf-8")
    assert 'id: "reports"' in sidebar
