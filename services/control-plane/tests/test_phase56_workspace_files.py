from __future__ import annotations

import ast
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from obsion.artifacts.paths import normalize_workspace_path
from obsion.common.errors import ValidationError
from obsion.security.auth import get_principal
from obsion.security.identity import Principal

_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "obsion"
WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web"


def test_workspace_path_rejects_relative_and_unsafe_segments() -> None:
    assert normalize_workspace_path(None) is None
    assert normalize_workspace_path("  ") is None
    assert normalize_workspace_path("/reports/incident.md") == "/reports/incident.md"
    with pytest.raises(ValidationError) as captured:
        normalize_workspace_path("../secret")
    assert captured.value.code == "artifact_path_invalid"
    with pytest.raises(ValidationError):
        normalize_workspace_path("/reports/../passwd")
    with pytest.raises(ValidationError):
        normalize_workspace_path("/reports/")
    with pytest.raises(ValidationError):
        normalize_workspace_path("/reports/has space.md")


def test_workspace_files_are_versioned_and_do_not_enter_system(
    client: TestClient,
) -> None:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Files workspace", "description": "Path ledger"},
    )
    assert workspace.status_code == 201, workspace.text
    workspace_id = workspace.json()["id"]
    empty = client.get(f"/api/v1/workspaces/{workspace_id}/files")
    assert empty.status_code == 200, empty.text
    assert empty.json() == []

    first = client.post(
        f"/api/v1/workspaces/{workspace_id}/artifacts",
        files={"file": ("notes.txt", b"first draft", "text/plain")},
        data={"title": "notes", "kind": "FILE", "path": "/notes/runbook.txt"},
    )
    assert first.status_code == 201, first.text
    assert first.json()["path"] == "/notes/runbook.txt"
    assert first.json()["file_version"] == 1
    assert first.json()["superseded_at"] is None

    invalid = client.post(
        f"/api/v1/workspaces/{workspace_id}/artifacts",
        files={"file": ("bad.txt", b"nope", "text/plain")},
        data={"title": "bad", "kind": "FILE", "path": "/notes/../secret"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["code"] == "artifact_path_invalid"

    second = client.post(
        f"/api/v1/workspaces/{workspace_id}/artifacts",
        files={"file": ("notes.txt", b"second draft", "text/plain")},
        data={"title": "notes", "kind": "FILE", "path": "/notes/runbook.txt"},
    )
    assert second.status_code == 201, second.text
    assert second.json()["file_version"] == 2
    assert second.json()["superseded_at"] is None

    current = client.get(f"/api/v1/workspaces/{workspace_id}/files").json()
    assert [item["id"] for item in current] == [second.json()["id"]]
    assert current[0]["file_version"] == 2

    history = client.get(
        f"/api/v1/workspaces/{workspace_id}/files",
        params={"include_superseded": True},
    ).json()
    assert [item["file_version"] for item in history] == [2, 1]
    assert history[1]["superseded_at"] is not None

    untitled = client.post(
        f"/api/v1/workspaces/{workspace_id}/artifacts",
        files={"file": ("loose.txt", b"no path", "text/plain")},
        data={"title": "loose", "kind": "FILE"},
    )
    assert untitled.status_code == 201, untitled.text
    assert untitled.json()["path"] is None
    still_current = client.get(f"/api/v1/workspaces/{workspace_id}/files").json()
    assert [item["id"] for item in still_current] == [second.json()["id"]]


def test_workspace_files_are_tenant_scoped(client: TestClient) -> None:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Tenant files", "description": "Isolation"},
    ).json()
    created = client.post(
        f"/api/v1/workspaces/{workspace['id']}/artifacts",
        files={"file": ("a.txt", b"secret", "text/plain")},
        data={"title": "a", "kind": "FILE", "path": "/private/a.txt"},
    )
    assert created.status_code == 201, created.text
    other = Principal(
        id=UUID("00000000-0000-7000-8000-000000000099"),
        organization_id=UUID("00000000-0000-7000-8000-000000000099"),
        external_id="cross-tenant-files",
        display_name="Cross-tenant Files",
        permissions=frozenset({"workspace.read.all", "artifact.write"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: other
    try:
        listed = client.get(f"/api/v1/workspaces/{workspace['id']}/files")
        assert listed.status_code == 404
    finally:
        client.app.dependency_overrides.pop(get_principal, None)


def test_workspace_files_are_not_a_system_context_channel() -> None:
    source = (_SOURCE_ROOT / "artifacts" / "paths.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert "obsion.model_gateway.gateway" not in imports
    harness = (_SOURCE_ROOT / "harness" / "runtime.py").read_text(encoding="utf-8")
    assert "normalize_workspace_path" not in harness
    files_view = (WEB_ROOT / "src" / "components" / "files-view.tsx").read_text(encoding="utf-8")
    assert "工作区文件" in files_view
    assert "不会自动进入 SYSTEM" in files_view
    sidebar = (WEB_ROOT / "src" / "components" / "sidebar.tsx").read_text(encoding="utf-8")
    assert 'id: "files"' in sidebar
