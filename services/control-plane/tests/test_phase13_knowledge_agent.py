import json
import time
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from obsion.db.models import Evidence
from obsion.model_gateway.gateway import ModelGateway, ModelResult


def _wait_terminal(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return run
        time.sleep(0.05)
    raise AssertionError(f"Run did not reach a terminal state: {run}")


def _create_thread(client: TestClient) -> dict:
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Knowledge QA", "description": "Phase 13 KnowledgeAgent"},
    )
    assert workspace.status_code == 201, workspace.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": "Knowledge QA"},
    )
    assert thread.status_code == 201, thread.text
    return thread.json()


def test_knowledge_route_pins_knowledge_agent_skill_and_citations(
    client: TestClient, monkeypatch
) -> None:
    # Explicit synthetic author/reviewer; retrieving a document without an
    # eligible model must no longer masquerade as a verified answer.
    statement = "Every production release requires an owner and rollback plan."
    stages = []

    async def complete(self, session, **kwargs):
        source = await session.scalar(select(Evidence).where(Evidence.run_id == kwargs["run_id"]))
        assert source and any(statement in hit["content"] for hit in source.content["hits"])
        if kwargs["step_id"] is None:
            stages.append("author")
            payload = {
                "answerable": True,
                "answer": statement,
                "claims": [{"statement": statement, "evidence_ids": [str(source.id)]}],
            }
        else:
            stages.append("review")
            payload = {
                "answer_supported": True,
                "question_answered": True,
                "claims": [
                    {
                        "claim_index": 1,
                        "verdict": "SUPPORTED",
                        "quotes": [{"evidence_id": str(source.id), "quote": statement}],
                    }
                ],
            }
        return ModelResult(
            content=json.dumps(payload),
            profile_id=kwargs["profile_id"],
            endpoint_id=uuid4(),
            input_tokens=10,
            output_tokens=10,
            latency_ms=1,
            cost_amount=Decimal("0"),
            finish_reason="stop",
        )

    monkeypatch.setattr(ModelGateway, "complete", complete)
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
            "source": "phase13",
            "external_id": "release-policy",
            "title": "Release policy",
            "classification": "INTERNAL",
            "acl": '{"organization": true}',
        },
    )
    assert document.status_code == 201, document.text
    thread = _create_thread(client)
    created = client.post(
        f"/api/v1/threads/{thread['id']}/turns",
        json={"input": "What does the release policy require?"},
    )
    assert created.status_code == 202, created.text
    run = _wait_terminal(client, created.json()["run"]["id"])

    assert run["status"] == "COMPLETED", f"{run.get('error_code')}: {run.get('error_message')}"
    assert run["intent"]["route"] == "KNOWLEDGE"
    assert run["intent"]["agent"] == "knowledge-agent"
    assert run["intent"]["skill"] == "knowledge-qa"
    assert run["plan"]["agent"] == "knowledge-agent"
    assert run["plan"]["skill"]["name"] == "knowledge-qa"
    assert run["plan"]["skill"]["required_evidence"] == ["DOCUMENT"]
    steps = client.get(f"/api/v1/runs/{run['id']}/steps").json()
    capability_steps = [item for item in steps if item["kind"] == "CAPABILITY"]
    assert len(capability_steps) == 1
    assert capability_steps[0]["name"] == "Search authorized enterprise knowledge"

    artifacts = client.get(f"/api/v1/runs/{run['id']}/artifacts").json()
    answer = artifacts[0]["inline_content"]
    assert answer["citations"]
    assert "### 引用" in answer["markdown"]
    assert "[1]" in answer["markdown"]
    claims = client.get(f"/api/v1/runs/{run['id']}/claims").json()
    assert claims and claims[0]["evidence_ids"]
    assert stages == ["author", "review"]
    assert answer["verification"]["verified"] is True


def test_knowledge_agent_says_unknown_without_authorized_evidence(client: TestClient) -> None:
    thread = _create_thread(client)
    created = client.post(
        f"/api/v1/threads/{thread['id']}/turns",
        json={"input": "What is the unrecorded retention exception?"},
    )
    assert created.status_code == 202, created.text
    run = _wait_terminal(client, created.json()["run"]["id"])

    assert run["status"] == "COMPLETED", f"{run.get('error_code')}: {run.get('error_message')}"
    artifacts = client.get(f"/api/v1/runs/{run['id']}/artifacts").json()
    answer = artifacts[0]["inline_content"]
    assert answer["markdown"].startswith("不知道：")
    assert answer["citations"] == []
    assert answer["verification"]["verified"] is False
    assert client.get(f"/api/v1/runs/{run['id']}/claims").json() == []
