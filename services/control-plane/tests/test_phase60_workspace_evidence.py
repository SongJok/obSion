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


def test_conversation_runs_do_not_invent_workspace_evidence(client: TestClient) -> None:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Evidence workspace", "description": "Greeting is not evidence"},
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
    assert client.get(f"/api/v1/runs/{run['id']}/evidence").json() == []
    listed = client.get(f"/api/v1/workspaces/{workspace.json()['id']}/evidence")
    assert listed.status_code == 200, listed.text
    assert listed.json() == []


def test_knowledge_citations_appear_on_the_workspace_evidence_ledger(
    client: TestClient,
) -> None:
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
            "source": "phase60",
            "external_id": "release-policy-evidence",
            "title": "Release policy",
            "classification": "INTERNAL",
            "acl": '{"organization": true}',
        },
    )
    assert document.status_code == 201, document.text
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Knowledge evidence", "description": "Cited documents become evidence"},
    ).json()
    other = client.post(
        "/api/v1/workspaces",
        json={"name": "Empty evidence", "description": "Isolation"},
    ).json()
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace["id"], "title": "Knowledge evidence"},
    ).json()
    created = client.post(
        f"/api/v1/threads/{thread['id']}/turns",
        json={"input": "What does the release policy require?"},
    )
    assert created.status_code == 202, created.text
    run = _wait_terminal(client, created.json()["run"]["id"])
    assert run["status"] == "COMPLETED", run
    run_evidence = client.get(f"/api/v1/runs/{run['id']}/evidence").json()
    assert run_evidence
    assert all(item["evidence_type"] == "DOCUMENT" for item in run_evidence)

    listed = client.get(f"/api/v1/workspaces/{workspace['id']}/evidence")
    assert listed.status_code == 200, listed.text
    assert {item["id"] for item in listed.json()} == {item["id"] for item in run_evidence}
    assert {item["content_fingerprint"] for item in listed.json()} == {
        item["content_fingerprint"] for item in run_evidence
    }
    assert all(item["run_id"] == run["id"] for item in listed.json())
    assert client.get(f"/api/v1/workspaces/{other['id']}/evidence").json() == []


def test_workspace_evidence_is_tenant_scoped(client: TestClient) -> None:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Isolated evidence", "description": "Tenant boundary"},
    ).json()
    other = Principal(
        id=UUID("00000000-0000-7000-8000-000000000099"),
        organization_id=UUID("00000000-0000-7000-8000-000000000099"),
        external_id="cross-tenant-evidence",
        display_name="Cross-tenant Evidence",
        permissions=frozenset({"workspace.read.all"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: other
    try:
        listed = client.get(f"/api/v1/workspaces/{workspace['id']}/evidence")
        assert listed.status_code == 404
    finally:
        client.app.dependency_overrides.pop(get_principal, None)


def test_workspace_evidence_is_not_fabricated() -> None:
    source = (_SOURCE_ROOT / "api" / "run_inspection.py").read_text(encoding="utf-8")
    assert "list_workspace_evidence" in source
    tree = ast.parse(source)
    imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert "obsion.model_gateway.gateway" not in imports
    view = (WEB_ROOT / "src" / "components" / "evidence-view.tsx").read_text(encoding="utf-8")
    assert "工作区证据" in view
    assert "不伪造证据" in view
    sidebar = (WEB_ROOT / "src" / "components" / "sidebar.tsx").read_text(encoding="utf-8")
    assert 'id: "evidence"' in sidebar
