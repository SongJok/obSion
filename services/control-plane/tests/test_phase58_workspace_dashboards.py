from __future__ import annotations

import ast
import json
import time
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from obsion.common.ids import new_id
from obsion.common.time import utc_now
from obsion.db.models import Evidence, Run, Thread, Turn
from obsion.domain.enums import (
    ArtifactKind,
    Classification,
    EvidenceType,
    RunStatus,
    ThreadStatus,
)
from obsion.harness.runtime import HarnessRuntime
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


def _data_artifacts() -> tuple[HarnessRuntime, Run, Turn, Thread, list]:
    organization_id = new_id()
    user_id = new_id()
    thread = Thread(
        id=new_id(),
        organization_id=organization_id,
        workspace_id=new_id(),
        title="Revenue analysis",
        status=ThreadStatus.ACTIVE,
        created_by=user_id,
    )
    turn = Turn(
        id=new_id(),
        organization_id=organization_id,
        thread_id=thread.id,
        ordinal=1,
        created_by=user_id,
        input_text="Revenue by region",
        sanitized_input="Revenue by region",
        context_refs=[],
        attachment_refs=[],
        created_at=utc_now(),
    )
    run = Run(
        id=new_id(),
        organization_id=organization_id,
        turn_id=turn.id,
        status=RunStatus.RUNNING,
        plan={
            "route": "DATA",
            "steps": [
                {
                    "capability": "data.query",
                    "payload": {
                        "sql": "SELECT region, SUM(revenue) AS revenue FROM analytics.sales",
                        "parameters": [],
                        "parameter_types": [],
                    },
                    "resource": {
                        "table": "analytics.sales",
                        "metric": {"display_name": "Revenue"},
                        "validation": {"valid": True},
                    },
                }
            ],
        },
    )
    evidence = Evidence(
        id=new_id(),
        organization_id=organization_id,
        run_id=run.id,
        evidence_type=EvidenceType.DATA,
        source="warehouse",
        resource="analytics.sales",
        observed_at=utc_now(),
        ingested_at=utc_now(),
        content={
            "columns": ["region", "revenue"],
            "rows": [
                {"region": "East", "revenue": "42.5"},
                {"region": "West", "revenue": Decimal("31.25")},
            ],
            "row_count": 2,
        },
        content_fingerprint="a" * 64,
        confidence=Decimal("1"),
        classification=Classification.CONFIDENTIAL,
        permissions=["data.query"],
        lineage={},
    )
    runtime = object.__new__(HarnessRuntime)
    artifacts = runtime._data_result_artifacts(run, turn, thread, [evidence])
    for item in artifacts:
        item.id = new_id()
    return runtime, run, turn, thread, artifacts


def test_data_charts_compose_a_workspace_dashboard_without_invented_series() -> None:
    runtime, run, turn, thread, artifacts = _data_artifacts()
    assert [item.kind for item in artifacts] == [
        ArtifactKind.SQL,
        ArtifactKind.TABLE,
        ArtifactKind.CHART,
    ]
    dashboard = runtime._workspace_dashboard_artifact(run, turn, thread, artifacts)
    assert dashboard is not None
    assert dashboard.kind == ArtifactKind.DASHBOARD
    assert dashboard.title == "Workspace dashboard"
    assert dashboard.media_type == "application/vnd.obsion.dashboard+json"
    content = dashboard.inline_content or {}
    serialized = json.dumps(content)
    assert "encoding" not in serialized
    assert '"values"' not in serialized
    assert content["source"] == "workspace-dashboard"
    chart = next(item for item in artifacts if item.kind == ArtifactKind.CHART)
    table = next(item for item in artifacts if item.kind == ArtifactKind.TABLE)
    sql = next(item for item in artifacts if item.kind == ArtifactKind.SQL)
    assert content["chart_artifact_ids"] == [str(chart.id)]
    assert content["table_artifact_ids"] == [str(table.id)]
    assert content["sql_artifact_ids"] == [str(sql.id)]
    assert [panel["artifact_id"] for panel in content["panels"]] == [
        str(sql.id),
        str(table.id),
        str(chart.id),
    ]
    assert dashboard.lineage["source"] == "workspace-dashboard"
    assert dashboard.lineage["chart_artifact_ids"] == [str(chart.id)]


def test_workspace_dashboard_requires_a_real_chart() -> None:
    runtime, run, turn, thread, artifacts = _data_artifacts()
    without_chart = [item for item in artifacts if item.kind != ArtifactKind.CHART]
    assert runtime._workspace_dashboard_artifact(run, turn, thread, without_chart) is None
    already = runtime._workspace_dashboard_artifact(run, turn, thread, artifacts)
    assert already is not None
    assert runtime._workspace_dashboard_artifact(run, turn, thread, [*artifacts, already]) is None


def test_conversation_runs_do_not_invent_workspace_dashboards(client: TestClient) -> None:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Dashboard workspace", "description": "Greeting is not a dashboard"},
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
    dashboards = client.get(f"/api/v1/workspaces/{workspace.json()['id']}/dashboards")
    assert dashboards.status_code == 200, dashboards.text
    assert dashboards.json() == []


def test_knowledge_reports_do_not_invent_workspace_dashboards(client: TestClient) -> None:
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
            "source": "phase58",
            "external_id": "release-policy-dashboard",
            "title": "Release policy",
            "classification": "INTERNAL",
            "acl": '{"organization": true}',
        },
    )
    assert document.status_code == 201, document.text
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Knowledge dashboards", "description": "Reports are not dashboards"},
    ).json()
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace["id"], "title": "Knowledge dashboard"},
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
    assert "DASHBOARD" not in kinds
    assert kinds.count("REPORT") == 1
    listed = client.get(f"/api/v1/workspaces/{workspace['id']}/dashboards")
    assert listed.status_code == 200, listed.text
    assert listed.json() == []


def test_workspace_dashboards_are_tenant_scoped(client: TestClient) -> None:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Isolated dashboards", "description": "Tenant boundary"},
    ).json()
    other = Principal(
        id=UUID("00000000-0000-7000-8000-000000000099"),
        organization_id=UUID("00000000-0000-7000-8000-000000000099"),
        external_id="cross-tenant-dashboards",
        display_name="Cross-tenant Dashboards",
        permissions=frozenset({"workspace.read.all"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: other
    try:
        listed = client.get(f"/api/v1/workspaces/{workspace['id']}/dashboards")
        assert listed.status_code == 404
    finally:
        client.app.dependency_overrides.pop(get_principal, None)


def test_workspace_dashboards_are_not_a_fabricated_series() -> None:
    source = (_SOURCE_ROOT / "harness" / "runtime.py").read_text(encoding="utf-8")
    assert "_workspace_dashboard_artifact" in source
    assert "self._workspace_dashboard_artifact(run, turn, thread, result_artifacts)" in source
    tree = ast.parse(source)
    runtime_class = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "HarnessRuntime"
    )
    helper = next(
        item
        for item in runtime_class.body
        if isinstance(item, ast.FunctionDef) and item.name == "_workspace_dashboard_artifact"
    )
    helper_source = ast.get_source_segment(source, helper) or ""
    assert "values" not in helper_source
    assert "encoding" not in helper_source
    assert "vega" not in helper_source.casefold()
    service_tree = ast.parse(
        (_SOURCE_ROOT / "artifacts" / "service.py").read_text(encoding="utf-8")
    )
    imports = [
        node.module
        for node in ast.walk(service_tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert "obsion.model_gateway.gateway" not in imports
    dashboards_view = (WEB_ROOT / "src" / "components" / "dashboards-view.tsx").read_text(
        encoding="utf-8"
    )
    assert "工作区仪表盘" in dashboards_view
    assert "不伪造数据系列" in dashboards_view
    sidebar = (WEB_ROOT / "src" / "components" / "sidebar.tsx").read_text(encoding="utf-8")
    assert 'id: "dashboards"' in sidebar
