"""Phase 96: post-conclusion context actions.

Verified claims in the Runtime inspector can now become workspace tasks or
decision records without leaving the investigation, always carrying the
source Run so provenance survives into the collaboration ledger. Live tests
pin that the payloads the modal sends are accepted with same-workspace
provenance and rejected cross-workspace; static tests pin the Web wiring:
the claim action helpers, the per-claim buttons, the modal, and the
collaboration navigation in the workbench.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from obsion.release.notes import validate_release_notes

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps" / "web"


def _read(relative: str) -> str:
    return (WEB / relative).read_text(encoding="utf-8")


def _workspace(client: TestClient, name: str) -> dict:
    response = client.post(
        "/api/v1/workspaces",
        json={"name": name, "description": "Claim action coverage"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_claim_action_payloads_are_accepted_with_same_workspace_run(client: TestClient) -> None:
    workspace = _workspace(client, "Claim actions")
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace["id"], "title": "支付成功率调查"},
    )
    assert thread.status_code == 201, thread.text
    turn = client.post(
        f"/api/v1/threads/{thread.json()['id']}/turns",
        json={"input": "Investigate the success-rate drop"},
    )
    assert turn.status_code == 202, turn.text
    run_id = turn.json()["run"]["id"]

    task = client.post(
        f"/api/v1/workspaces/{workspace['id']}/tasks",
        json={
            "title": "结论 C1：支付成功率下降主要由渠道 B 的 5xx 激增导致",
            "description": (
                "支付成功率下降主要由渠道 B 的 5xx 激增导致\n\n"
                "来源：Run 123e4567 · 2 项证据支撑 · 验证状态 VERIFIED"
            ),
            "source_run_id": run_id,
        },
    )
    assert task.status_code == 201, task.text
    assert task.json()["source_run_id"] == run_id

    decision = client.post(
        f"/api/v1/workspaces/{workspace['id']}/decisions",
        json={
            "title": "结论 C1：支付成功率下降主要由渠道 B 的 5xx 激增导致",
            "summary": "支付成功率下降主要由渠道 B 的 5xx 激增导致",
            "rationale": (
                "该结论经 Critic 验证（VERIFIED，置信度 HIGH），由 2 项证据支撑：\n"
                "- DATA · payments 数据集 — sql://payments/success_rate"
            ),
            "source_run_id": run_id,
        },
    )
    assert decision.status_code == 201, decision.text
    assert decision.json()["source_run_id"] == run_id

    other = _workspace(client, "Unrelated workspace")
    mismatched = client.post(
        f"/api/v1/workspaces/{other['id']}/tasks",
        json={"title": "跨空间结论", "source_run_id": run_id},
    )
    assert mismatched.status_code == 422
    assert mismatched.json()["code"] == "workspace_source_run_mismatch"


def test_claim_action_helpers_exist() -> None:
    helpers = _read("src/lib/claim-actions.ts")
    for marker in (
        "CLAIM_TITLE_MAX",
        "CLAIM_EVIDENCE_LINES_MAX",
        "truncateClaim",
        "claimActionTitle",
        "claimEvidenceLines",
        "claimTaskPayload",
        "claimDecisionPayload",
        "source_run_id: runId",
    ):
        assert marker in helpers


def test_inspector_wires_claim_actions() -> None:
    inspector = _read("src/components/runtime-inspector.tsx")
    for marker in (
        "ClaimActionModal",
        "claimTaskPayload(claim, run.id, index)",
        "claimDecisionPayload(claim, evidence, run.id, index)",
        "api.collaboration.createTask",
        "api.collaboration.createDecision",
        'run.status === "COMPLETED"',
        "run.workspace_context?.workspace_id",
        "workspace_source_run_mismatch",
        "转为任务",
        "记录决策",
        "在协作中查看",
        "claim-actions",
    ):
        assert marker in inspector
    styles = _read("src/app/globals.css")
    assert ".claim-actions" in styles


def test_workbench_opens_collaboration_from_claim_actions() -> None:
    workbench = _read("src/components/workbench.tsx")
    assert "onOpenCollaboration={() => {" in workbench
    assert 'setView("collaboration"); }}' in workbench


def test_web_behaviour_suite_covers_claim_actions() -> None:
    suite = _read("tests/claim-actions.test.ts")
    for marker in (
        "claimTaskPayload",
        "claimDecisionPayload",
        "truncateClaim",
        "另有 2 项证据见运行详情",
        "Run 123e4567",
    ):
        assert marker in suite


def test_release_notes_and_project_status_track_phase96() -> None:
    result = validate_release_notes(ROOT / "docs" / "release" / "0.96.0-dev.yaml", ROOT)
    assert result["version"] == "0.96.0-dev"
    status = yaml.safe_load((ROOT / "docs" / "project-status.yaml").read_text(encoding="utf-8"))
    assert status["version"] == "0.97.0-dev"
    assert status["current_phase"] == "phase-97"
    assert "phase-95" in status["completed_phases"]
