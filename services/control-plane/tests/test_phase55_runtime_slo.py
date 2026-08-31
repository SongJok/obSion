from __future__ import annotations

import ast
import json
import time
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from obsion.application.slo import _rate
from obsion.security.auth import get_principal
from obsion.security.identity import Principal

_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "obsion"
WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web"


def _completed_run(client: TestClient) -> dict:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "SLO workspace", "description": "Runtime metric projection"},
    )
    assert workspace.status_code == 201, workspace.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": "SLO lifecycle"},
    )
    assert thread.status_code == 201, thread.text
    created = client.post(
        f"/api/v1/threads/{thread.json()['id']}/turns",
        json={"input": "Summarize this request with verifiable evidence."},
    )
    assert created.status_code == 202, created.text
    run = created.json()["run"]
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run['id']}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            break
        time.sleep(0.05)
    assert run["status"] == "COMPLETED", run
    return run


def test_rate_is_null_when_the_denominator_is_empty() -> None:
    assert _rate(1, 0) is None
    assert _rate(2, 4) == 0.5


def test_runtime_slo_projects_core_metrics_from_postgresql(client: TestClient) -> None:
    empty = client.get("/api/v1/admin/slo")
    assert empty.status_code == 200, empty.text
    baseline = empty.json()
    assert baseline["source"] == "postgresql"
    assert baseline["runs"] == {
        "terminal": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "success_rate": None,
    }
    assert baseline["latency"]["ttft"] == {
        "available": False,
        "metric": "obsion.run.ttft",
        "reason": "histogram-only",
    }
    assert baseline["latency"]["tool"]["source"] == "capability-steps"
    assert "p95" not in json.dumps(baseline)

    run = _completed_run(client)
    recorded = client.put(
        f"/api/v1/runs/{run['id']}/feedback",
        json={"rating": "HELPFUL"},
    )
    assert recorded.status_code == 200, recorded.text

    projected = client.get("/api/v1/admin/slo").json()
    assert projected["source"] == "postgresql"
    assert projected["runs"]["completed"] == 1
    assert projected["runs"]["failed"] == 0
    assert projected["runs"]["success_rate"] == 1.0
    assert projected["satisfaction"] == {
        "total": 1,
        "helpful": 1,
        "needs_improvement": 0,
        "helpful_rate": 1.0,
    }
    assert projected["latency"]["count"] >= 1
    assert projected["latency"]["average_ms"] is not None
    assert projected["steps"]["count"] == 1
    assert projected["tokens"]["input"] >= 0
    assert projected["replans"]["events"] >= 0
    assert projected["approvals"]["approval_rate"] is None
    if projected["evidence_coverage"]["count"]:
        assert projected["evidence_coverage"]["average"] is not None


def test_runtime_slo_is_tenant_scoped_and_requires_audit_read(client: TestClient) -> None:
    _completed_run(client)
    other_tenant = Principal(
        id=UUID("00000000-0000-7000-8000-000000000099"),
        organization_id=UUID("00000000-0000-7000-8000-000000000099"),
        external_id="cross-tenant-slo",
        display_name="Cross-tenant SLO",
        permissions=frozenset({"workspace.read.all", "audit.read"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: other_tenant
    try:
        isolated = client.get("/api/v1/admin/slo")
        assert isolated.status_code == 200, isolated.text
        assert isolated.json()["runs"]["terminal"] == 0
        assert isolated.json()["satisfaction"]["total"] == 0
    finally:
        client.app.dependency_overrides.pop(get_principal, None)

    denied = Principal(
        id=UUID("00000000-0000-7000-8000-000000000088"),
        organization_id=UUID("00000000-0000-7000-8000-000000000001"),
        external_id="no-audit-slo",
        display_name="No Audit",
        permissions=frozenset({"workspace.read.all"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: denied
    try:
        response = client.get("/api/v1/admin/slo")
        assert response.status_code == 403
        assert response.json()["code"] == "admin_access_denied"
    finally:
        client.app.dependency_overrides.pop(get_principal, None)


def test_runtime_slo_does_not_invent_p95_or_a_second_truth_store() -> None:
    source = (_SOURCE_ROOT / "application" / "slo.py").read_text(encoding="utf-8")
    lowered = source.lower()
    assert "p95" not in lowered
    assert "percentile" not in lowered
    assert "quantile" not in lowered
    assert "kafka" not in lowered
    assert "clickhouse" not in lowered
    tree = ast.parse(source)
    imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert "obsion.telemetry" not in imports
    telemetry = (_SOURCE_ROOT / "telemetry.py").read_text(encoding="utf-8")
    assert 'meter.create_counter("obsion.run.satisfaction"' in telemetry
    feedback = (_SOURCE_ROOT / "feedback" / "service.py").read_text(encoding="utf-8")
    assert "run_satisfaction.add" in feedback
    inspector = (WEB_ROOT / "src" / "components" / "admin-view.tsx").read_text(encoding="utf-8")
    assert "运行 SLO 投影" in inspector
    assert "不是 OTel histogram 的 p95" in inspector
    assert "api.admin.runtimeSlo" in inspector
