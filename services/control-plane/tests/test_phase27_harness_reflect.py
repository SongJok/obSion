import time

from fastapi.testclient import TestClient

from obsion.harness.critic import CriticResult
from obsion.harness.runtime import HarnessRuntime


def _create_thread(client: TestClient, title: str) -> dict:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": title, "description": "Phase 27 harness reflect"},
    )
    assert workspace.status_code == 201, workspace.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": title},
    )
    assert thread.status_code == 201, thread.text
    return thread.json()


def _create_turn(client: TestClient, thread_id: str, prompt: str) -> str:
    response = client.post(f"/api/v1/threads/{thread_id}/turns", json={"input": prompt})
    assert response.status_code == 202, response.text
    return str(response.json()["run"]["id"])


def _wait_terminal(client: TestClient, run_id: str) -> dict:
    run = {}
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return run
        time.sleep(0.05)
    raise AssertionError(f"Run did not reach a terminal state: {run}")


def test_reflect_decision_publishes_only_when_verified_or_evidence_free() -> None:
    missing = CriticResult(
        verified=False,
        confidence=0.1,
        coverage=0.0,
        missing_evidence=("DATA",),
        conflicts=(),
        checks={},
    )
    verified = CriticResult(
        verified=True,
        confidence=1.0,
        coverage=1.0,
        missing_evidence=(),
        conflicts=(),
        checks={"question_coverage": True},
    )
    assert (
        HarnessRuntime._reflect_decision(critic=missing, evidence_free_response=False) == "REPLAN"
    )
    assert (
        HarnessRuntime._reflect_decision(critic=missing, evidence_free_response=True) == "RESPOND"
    )
    assert (
        HarnessRuntime._reflect_decision(critic=verified, evidence_free_response=False) == "RESPOND"
    )
    withheld = CriticResult(
        verified=False,
        confidence=0.2,
        coverage=1.0,
        missing_evidence=(),
        conflicts=({"reason_codes": ["question_not_covered"]},),
        checks={},
    )
    assert (
        HarnessRuntime._reflect_decision(critic=withheld, evidence_free_response=False)
        == "WITHHOLD"
    )


def test_greeting_persists_reflect_between_verify_and_respond(client: TestClient) -> None:
    thread = _create_thread(client, "Phase 27 reflect")
    run_id = _create_turn(client, thread["id"], "你好")
    run = _wait_terminal(client, run_id)

    assert run["status"] == "COMPLETED"
    steps = client.get(f"/api/v1/runs/{run_id}/steps").json()
    kinds = [step["kind"] for step in steps]
    assert kinds == ["OBSERVE", "UNDERSTAND", "PLAN", "VERIFY", "REFLECT", "RESPOND"]
    reflect = steps[4]
    assert reflect["status"] == "COMPLETED"
    assert reflect["depends_on"] == [4]
    assert reflect["output_ref"] == "reflect.respond"
    assert steps[5]["depends_on"] == [5]
