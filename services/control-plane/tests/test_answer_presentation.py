import json
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_knowledge_grounding import SOURCE, review
from test_phase13_knowledge_agent import _create_thread, _wait_terminal

from obsion.db.models import Evidence
from obsion.domain.enums import EvidenceType
from obsion.harness.investigation import INVESTIGATION_POLICY
from obsion.harness.presentation import contains_transport_references
from obsion.knowledge.publication import KnowledgePublicationGuard
from obsion.model_gateway.gateway import ModelGateway, ModelResult


def test_transport_detection_preserves_literal_technical_document_content():
    identifier = str(uuid4())
    item = SimpleNamespace(
        id=identifier,
        evidence_type=EvidenceType.DOCUMENT,
        content={"text": "接口示例使用 body_index 0，业务编号为 " + identifier},
    )
    assert not contains_transport_references("接口示例使用 body_index 0", [item])
    assert not contains_transport_references("业务编号为 " + identifier, [item])
    item.content = {"text": SOURCE}
    assert contains_transport_references("DOCUMENT " + identifier + "，body_index 0", [item])
    assert contains_transport_references("body_index: 2", [item])
    assert not contains_transport_references("交通报销须先审批后报销。", [item])


@pytest.mark.parametrize(
    "mode", ["repaired", "still_leaks", "unsupported_repair", "access_changed"]
)
def test_one_regeneration_requires_current_access_and_complete_review(client, monkeypatch, mode):
    uploaded = client.post(
        "/api/v1/knowledge/documents",
        files={"file": ("policy.md", SOURCE.encode(), "text/markdown")},
        data={
            "source": "presentation-test",
            "external_id": "policy",
            "title": "交通报销审批",
            "classification": "INTERNAL",
            "acl": '{"organization":true}',
        },
    )
    assert uploaded.status_code == 201
    authors, reviews, proposals = [], [], []
    denied = False
    guard = KnowledgePublicationGuard.check

    async def check(self, *args, **kwargs):
        if denied:
            return False
        return await guard(self, *args, **kwargs)

    async def complete(self, session, **kwargs):
        nonlocal denied
        item = await session.scalar(select(Evidence).where(Evidence.run_id == kwargs["run_id"]))
        assert item is not None
        identifier = str(item.id)
        if kwargs["messages"][0]["content"] == INVESTIGATION_POLICY:
            proposals.append(True)
            payload = {"action": "STOP"}
        elif kwargs["step_id"] is not None:
            reviews.append(True)
            candidate = json.loads(kwargs["messages"][1]["content"])["answer"]
            assert identifier not in candidate and "body_index" not in candidate
            payload = review(
                identifier, "CONTRADICTED" if mode == "unsupported_repair" else "SUPPORTED"
            )
        else:
            authors.append(True)
            sentence = (
                "交通报销不需要审批。"
                if mode == "unsupported_repair" and len(authors) == 2
                else "交通报销须先审批后报销。"
            )
            answer = sentence
            if len(authors) == 1 or mode == "still_leaks":
                answer += f"出处：DOCUMENT {identifier}，body_index 0。"
            if len(authors) == 1 and mode == "access_changed":
                denied = True
            if len(authors) == 2:
                assert "presentation correction" in str(kwargs["messages"])
                assert kwargs["max_input_tokens"] > 0
            payload = {
                "answerable": True,
                "answer": answer,
                "claims": [
                    {"statement": sentence, "evidence_ids": [identifier], "confidence": 0.9}
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
    monkeypatch.setattr(KnowledgePublicationGuard, "check", check)
    thread = _create_thread(client)
    response = client.post(
        f"/api/v1/threads/{thread['id']}/turns", json={"input": "交通报销如何审批？请注明出处。"}
    )
    assert response.status_code == 202
    run = _wait_terminal(client, response.json()["run"]["id"])
    assert run["status"] == "COMPLETED"
    # Restore the read guard after simulating revocation during authoring.
    denied = False
    artifacts = client.get(f"/api/v1/runs/{run['id']}/artifacts").json()
    answer = next(a["inline_content"] for a in artifacts if a["title"] == "Obsion answer")
    assert "body_index" not in answer["markdown"] and "DOCUMENT" not in answer["markdown"]
    assert len(authors) == (1 if mode == "access_changed" else 2)
    assert len(reviews) == (1 if mode in {"repaired", "unsupported_repair"} else 0)
    assert (
        run["input_tokens"]
        == run["output_tokens"]
        == 10 * (len(authors) + len(reviews) + len(proposals))
    )
    assert len(proposals) == (1 if mode == "unsupported_repair" else 0)
    assert answer["verification"]["verified"] is (mode == "repaired")
    if mode == "repaired":
        assert "交通报销须先审批后报销。" in answer["markdown"]
        assert len(answer["citations"]) == 1
        assert run["plan"]["answer_presentation"]["regenerations"] == 1
    else:
        assert answer["markdown"].startswith("不知道：") and not answer["citations"]
        assert "交通报销不需要审批" not in answer["markdown"]
    events = client.get(f"/api/v1/runs/{run['id']}/events").json()
    assert [e["payload"]["delta"] for e in events if e["name"] == "answer.delta"] == [
        answer["markdown"]
    ]
