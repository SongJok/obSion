from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from obsion.domain.enums import SystemRole
from obsion.security.auth import get_principal
from obsion.security.identity import Principal
from obsion.security.roles import SYSTEM_ROLE_DEFINITIONS

WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web"
EVAL_SERVICE = Path(__file__).resolve().parents[1] / "src" / "obsion" / "application" / "eval.py"


def _promoted_pins(client: TestClient) -> tuple[str, str]:
    catalog = client.get("/api/v1/eval/catalog")
    assert catalog.status_code == 200, catalog.text
    body = catalog.json()
    agent = next(item for item in body["agents"] if item["name"] == "general-agent")
    profile = next(item for item in body["model_profiles"] if item["name"] == "reasoning-high")
    return agent["version_id"], profile["id"]


def test_eval_console_is_not_a_second_harness_or_agent_picker() -> None:
    service = EVAL_SERVICE.read_text(encoding="utf-8")
    assert "obsion.harness" not in service
    assert "CapabilityGateway" not in service
    assert "ModelGateway" not in service
    composer = (WEB_ROOT / "src" / "components" / "composer.tsx").read_text(encoding="utf-8")
    workbench = (WEB_ROOT / "src" / "components" / "workbench.tsx").read_text(encoding="utf-8")
    sidebar = (WEB_ROOT / "src" / "components" / "sidebar.tsx").read_text(encoding="utf-8")
    eval_view = (WEB_ROOT / "src" / "components" / "eval-view.tsx").read_text(encoding="utf-8")
    types = (WEB_ROOT / "src" / "lib" / "types.ts").read_text(encoding="utf-8")
    assert "selectedAgent" not in composer
    assert "agent-picker" not in composer.casefold()
    assert "Agent picker" not in workbench
    assert 'id: "eval"' in sidebar
    assert "EvalView" in workbench
    assert '| "eval"' in types
    assert "fixtures.actual" in eval_view
    engineer = next(item for item in SYSTEM_ROLE_DEFINITIONS if item.name == SystemRole.ENGINEER)
    assert "evaluations.read" in engineer.permissions
    assert "evaluations.write" in engineer.permissions
    analyst = next(item for item in SYSTEM_ROLE_DEFINITIONS if item.name == SystemRole.ANALYST)
    assert "evaluations.read" in analyst.permissions
    assert "evaluations.write" not in analyst.permissions


def test_eval_rejects_fixtures_actual_and_compares_completed_runs(client: TestClient) -> None:
    dataset = client.post(
        "/api/v1/eval/datasets",
        json={
            "name": "Eval console routing",
            "description": "Experience Eval probe",
            "domain": "foundation",
        },
    )
    assert dataset.status_code == 201, dataset.text
    dataset_id = dataset.json()["id"]

    leaked = client.post(
        f"/api/v1/eval/datasets/{dataset_id}/cases",
        json={
            "external_id": "leaky-actual",
            "evaluator": "ROUTING",
            "input_payload": {"question": "Summarize the employee handbook"},
            "expected": {"route": "KNOWLEDGE"},
            "fixtures": {"actual": "fabricated"},
        },
    )
    assert leaked.status_code == 422, leaked.text
    assert leaked.json()["code"] == "evaluation_expectation_unsupported"

    created_case = client.post(
        f"/api/v1/eval/datasets/{dataset_id}/cases",
        json={
            "external_id": "route-knowledge-eval",
            "evaluator": "ROUTING",
            "input_payload": {"question": "Summarize the employee handbook"},
            "expected": {"route": "KNOWLEDGE"},
            "fixtures": {},
        },
    )
    assert created_case.status_code == 201, created_case.text

    agent_version_id, model_profile_id = _promoted_pins(client)
    first = client.post(
        f"/api/v1/eval/datasets/{dataset_id}/runs",
        json={
            "agent_version_id": agent_version_id,
            "model_profile_id": model_profile_id,
            "application_revision": "eval-console-1",
        },
    )
    assert first.status_code == 201, first.text
    assert first.json()["gate_passed"] is True
    assert first.json()["metrics"]["passed"] == 1

    second = client.post(
        f"/api/v1/eval/datasets/{dataset_id}/runs",
        json={
            "agent_version_id": agent_version_id,
            "model_profile_id": model_profile_id,
            "application_revision": "eval-console-2",
            "baseline_run_id": first.json()["id"],
        },
    )
    assert second.status_code == 201, second.text
    compared = client.post(
        "/api/v1/eval/compare",
        json={
            "baseline_run_id": first.json()["id"],
            "candidate_run_id": second.json()["id"],
        },
    )
    assert compared.status_code == 200, compared.text
    body = compared.json()
    assert body["gate_passed"] is True
    assert body["agent_changed"] is False
    assert body["prompt_changed"] is False
    assert body["metrics"]["baseline"]["regressions"] == []
    assert body["baseline"]["id"] == first.json()["id"]
    assert body["candidate"]["id"] == second.json()["id"]

    catalog = client.get("/api/v1/eval/catalog")
    assert catalog.status_code == 200, catalog.text
    names = {item["name"] for item in catalog.json()["datasets"]}
    assert "Eval console routing" in names
    cases = client.get(f"/api/v1/eval/datasets/{dataset_id}/cases")
    assert cases.status_code == 200, cases.text
    assert cases.json()[0]["external_id"] == "route-knowledge-eval"


def test_eval_denies_principals_without_evaluation_permissions(client: TestClient) -> None:
    viewer = Principal(
        id=UUID("00000000-0000-7000-8000-000000000002"),
        organization_id=UUID("00000000-0000-7000-8000-000000000001"),
        external_id="eval-viewer",
        display_name="Eval Viewer",
        permissions=frozenset(),
    )
    client.app.dependency_overrides[get_principal] = lambda: viewer
    try:
        denied = client.get("/api/v1/eval/catalog")
        assert denied.status_code == 403, denied.text
        assert denied.json()["code"] == "evaluation_read_denied"
        write_denied = client.post(
            "/api/v1/eval/datasets",
            json={"name": "denied", "description": "", "domain": "foundation"},
        )
        assert write_denied.status_code == 403, write_denied.text
        assert write_denied.json()["code"] == "evaluation_write_denied"
    finally:
        client.app.dependency_overrides.pop(get_principal, None)
