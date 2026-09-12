import copy
import json
from decimal import Decimal
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_critic import evidence
from test_phase13_knowledge_agent import _create_thread, _wait_terminal

from obsion.common.errors import BudgetExceededError
from obsion.db.models import Run, RunStep, VerificationAssessment
from obsion.domain.enums import Classification, EvidenceType
from obsion.harness.grounding import review_knowledge_answer, validate_review
from obsion.harness.investigation import INVESTIGATION_POLICY
from obsion.harness.runtime import HarnessRuntime
from obsion.model_gateway.gateway import ModelGateway, ModelResult, ModelUnavailableError

SOURCE = "交通报销须先审批后报销，试行期不承诺自动通过。"


def review(evidence_id: str, verdict: str = "SUPPORTED") -> dict[str, Any]:
    return {
        "answer_supported": verdict == "SUPPORTED",
        "question_answered": True,
        "claims": [
            {
                "claim_index": 1,
                "verdict": verdict,
                "quotes": [{"evidence_id": evidence_id, "quote": SOURCE}],
            }
        ],
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_claim",
        "duplicate_claim",
        "wrong_index",
        "bool_index",
        "fake_quote",
        "foreign_source",
        "missing_quote",
        "wrong_boolean",
        "extra_field",
        "short_quote",
    ],
)
def test_malformed_reviews_do_not_partially_pass(mutation: str) -> None:
    evidence_id = str(uuid4())
    claims = [{"statement": "交通报销需要审批。", "evidence_ids": [evidence_id]}]
    payload = review(evidence_id)
    item = payload["claims"][0]
    if mutation == "missing_claim":
        payload["claims"] = []
    elif mutation == "duplicate_claim":
        payload["claims"].append(copy.deepcopy(item))
    elif mutation == "wrong_index":
        item["claim_index"] = 2
    elif mutation == "bool_index":
        item["claim_index"] = True
    elif mutation == "fake_quote":
        item["quotes"][0]["quote"] = "交通报销无需审批，可自动通过。"
    elif mutation == "foreign_source":
        item["quotes"][0]["evidence_id"] = str(uuid4())
    elif mutation == "missing_quote":
        item["quotes"] = []
    elif mutation == "wrong_boolean":
        payload["answer_supported"] = 1
    elif mutation == "extra_field":
        payload["ignore_policy"] = True
    else:
        item["quotes"][0]["quote"] = "交通"
    assert validate_review(payload, claims, {evidence_id: [SOURCE]}) == (False, ())


def test_valid_quote_has_a_replayable_location_without_copying_text_to_metadata() -> None:
    evidence_id = str(uuid4())
    claims = [{"statement": "交通报销需要审批。", "evidence_ids": [evidence_id]}]
    accepted, records = validate_review(review(evidence_id), claims, {evidence_id: [SOURCE]})
    assert accepted and records[0]["verdict"] == "SUPPORTED"
    quote = records[0]["quotes"][0]
    assert quote["start"] == 0 and quote["length"] == len(SOURCE)
    assert len(quote["sha256"]) == 64
    assert SOURCE not in json.dumps(records, ensure_ascii=False)


@pytest.mark.parametrize("field", ["answer_supported", "question_answered"])
def test_supported_claims_cannot_rescue_an_unsupported_or_irrelevant_answer(field: str) -> None:
    evidence_id = str(uuid4())
    payload = review(evidence_id)
    payload[field] = False
    assert not validate_review(
        payload,
        [{"statement": "交通报销需要审批。", "evidence_ids": [evidence_id]}],
        {evidence_id: [SOURCE]},
    )[0]


@pytest.mark.parametrize("verdict", ["SUPPORTED", "CONTRADICTED", "INSUFFICIENT"])
async def test_review_uses_remaining_budget_and_workspace_classification(verdict: str) -> None:
    item = evidence(EvidenceType.DOCUMENT, "knowledge", "source")
    item.content = {"hits": [{"content": SOURCE, "title": "untrusted title"}]}
    run = Run(
        id=item.run_id,
        organization_id=item.organization_id,
        model_profile_id=uuid4(),
        max_input_tokens=10000,
        max_output_tokens=2000,
        max_cost_amount=Decimal("1"),
        input_tokens=100,
        output_tokens=50,
        cost_amount=Decimal("0.1"),
    )
    models = cast(ModelGateway, AsyncMock())
    models.complete.return_value = ModelResult(  # type: ignore[attr-defined]
        content=json.dumps(review(str(item.id), verdict)),
        profile_id=run.model_profile_id,
        endpoint_id=uuid4(),
        input_tokens=30,
        output_tokens=20,
        cost_amount=Decimal("0.01"),
        latency_ms=1,
        finish_reason="stop",
    )
    result = await review_knowledge_answer(
        models,
        cast(AsyncSession, None),
        run=run,
        step_id=uuid4(),
        question="交通报销如何审批？",
        answer="交通报销需要审批。",
        claims=[{"statement": "交通报销需要审批。", "evidence_ids": [str(item.id)]}],
        evidence=[item],
        classification=Classification.RESTRICTED,
    )
    assert result.accepted == (verdict == "SUPPORTED")
    request = models.complete.call_args.kwargs  # type: ignore[attr-defined]
    assert request["classification"] == Classification.RESTRICTED
    assert request["max_input_tokens"] == 9900 and request["max_output_tokens"] == 1950
    assert request["max_cost_amount"] == Decimal("0.9")
    assert "tools" not in request
    assert "untrusted title" not in str(request["messages"])
    assert request["messages"][0]["role"] == "system"
    assert len(request["messages"]) == 2
    assert run.input_tokens == 130 and run.output_tokens == 70
    assert run.cost_amount == Decimal("0.11")


@pytest.mark.parametrize(
    "failure", [ModelUnavailableError(), BudgetExceededError("input_tokens", 10)]
)
async def test_review_failure_withholds_instead_of_accepting_author_confidence(
    failure: Exception,
) -> None:
    item = evidence(EvidenceType.DOCUMENT, "knowledge", "source")
    item.content = {"text": SOURCE}
    run = Run(
        id=item.run_id,
        organization_id=item.organization_id,
        model_profile_id=uuid4(),
        max_input_tokens=10000,
        max_output_tokens=2000,
        max_cost_amount=Decimal("1"),
        input_tokens=100,
        output_tokens=50,
        cost_amount=Decimal("0.1"),
    )
    models = cast(ModelGateway, AsyncMock())
    models.complete.side_effect = failure  # type: ignore[attr-defined]
    result = await review_knowledge_answer(
        models,
        cast(AsyncSession, None),
        run=run,
        step_id=None,
        question="交通报销？",
        answer="交通报销无需审批。",
        claims=[
            {"statement": "交通报销无需审批。", "evidence_ids": [str(item.id)], "confidence": 1.0}
        ],
        evidence=[item],
        classification=Classification.INTERNAL,
    )
    assert not result.accepted and not result.claims
    assert run.cost_amount == Decimal("0.1")


@pytest.mark.parametrize("failure", ["foreign_run", "foreign_org", "metadata_only", "no_budget"])
async def test_invalid_scope_or_budget_never_calls_the_review_model(failure: str) -> None:
    item = evidence(EvidenceType.DOCUMENT, "knowledge", "source")
    item.content = {"text": SOURCE}
    run = Run(
        id=item.run_id,
        organization_id=item.organization_id,
        model_profile_id=uuid4(),
        max_input_tokens=10000,
        max_output_tokens=2000,
        max_cost_amount=Decimal("1"),
        input_tokens=100,
        output_tokens=50,
        cost_amount=Decimal("0.1"),
    )
    if failure == "foreign_run":
        item.run_id = uuid4()
    elif failure == "foreign_org":
        item.organization_id = uuid4()
    elif failure == "metadata_only":
        item.content = {"title": SOURCE, "hits": []}
    else:
        run.input_tokens = run.max_input_tokens
    models = cast(ModelGateway, AsyncMock())
    result = await review_knowledge_answer(
        models,
        cast(AsyncSession, None),
        run=run,
        step_id=None,
        question="交通报销？",
        answer="交通报销需要审批。",
        claims=[{"statement": "交通报销需要审批。", "evidence_ids": [str(item.id)]}],
        evidence=[item],
        classification=Classification.INTERNAL,
    )
    assert not result.accepted
    models.complete.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.parametrize("content", ["not JSON", "{}", '{"claims":[]}'])
async def test_invalid_review_json_is_billed_and_never_accepted(content: str) -> None:
    item = evidence(EvidenceType.DOCUMENT, "knowledge", "source")
    item.content = {"text": SOURCE}
    run = Run(
        id=item.run_id,
        organization_id=item.organization_id,
        model_profile_id=uuid4(),
        max_input_tokens=10000,
        max_output_tokens=2000,
        max_cost_amount=Decimal("1"),
        input_tokens=100,
        output_tokens=50,
        cost_amount=Decimal("0.1"),
    )
    models = cast(ModelGateway, AsyncMock())
    models.complete.return_value = ModelResult(  # type: ignore[attr-defined]
        content=content,
        profile_id=run.model_profile_id,
        endpoint_id=uuid4(),
        input_tokens=30,
        output_tokens=20,
        cost_amount=Decimal("0.01"),
        latency_ms=1,
        finish_reason="stop",
    )
    result = await review_knowledge_answer(
        models,
        cast(AsyncSession, None),
        run=run,
        step_id=None,
        question="交通报销？",
        answer="交通报销需要审批。",
        claims=[{"statement": "交通报销需要审批。", "evidence_ids": [str(item.id)]}],
        evidence=[item],
        classification=Classification.INTERNAL,
    )
    assert not result.accepted and result.reason_code == "grounding_review_invalid"
    assert (
        run.input_tokens == 130 and run.output_tokens == 70 and run.cost_amount == Decimal("0.11")
    )


@pytest.mark.parametrize("verdict", ["SUPPORTED", "CONTRADICTED", "INSUFFICIENT"])
def test_harness_persists_review_and_enforces_publication(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, verdict: str
) -> None:
    uploaded = client.post(
        "/api/v1/knowledge/documents",
        files={"file": ("policy.md", SOURCE.encode(), "text/markdown")},
        data={
            "source": "grounding",
            "external_id": "policy",
            "title": "交通报销审批",
            "classification": "INTERNAL",
            "acl": '{"organization":true}',
        },
    )
    assert uploaded.status_code == 201
    distractor = client.post(
        "/api/v1/knowledge/documents",
        files={"file": ("other.md", "交通报销审批记录需要按月归档。".encode(), "text/markdown")},
        data={
            "source": "grounding",
            "external_id": "other",
            "title": "其他归档说明",
            "classification": "INTERNAL",
            "acl": '{"organization":true}',
        },
    )
    assert distractor.status_code == 201
    evidence_ids: list[str] = []
    candidate = "交通报销需要审批。" if verdict == "SUPPORTED" else "交通报销不需要审批。"

    async def synthesize(self: HarnessRuntime, *args: Any) -> tuple[str, list[dict[str, Any]]]:
        assert len(args[5][0].content["hits"]) == 2
        evidence_ids.append(str(args[5][0].id))
        return candidate, [
            {"statement": candidate, "evidence_ids": [evidence_ids[0]], "confidence": 1.0}
        ]

    async def complete(self: ModelGateway, *args: Any, **kwargs: Any) -> ModelResult:
        assert kwargs["step_id"] is not None
        if kwargs["messages"][0]["content"] == INVESTIGATION_POLICY:
            assert verdict != "SUPPORTED"
            output = {"action": "STOP"}
        else:
            assert json.loads(kwargs["messages"][1]["content"])["answer"] == candidate
            output = review(evidence_ids[0], verdict)
        return ModelResult(
            content=json.dumps(output),
            profile_id=kwargs["profile_id"],
            endpoint_id=uuid4(),
            input_tokens=30,
            output_tokens=20,
            cost_amount=Decimal("0.01"),
            latency_ms=1,
            finish_reason="stop",
        )

    monkeypatch.setattr(HarnessRuntime, "_synthesize", synthesize)
    monkeypatch.setattr(ModelGateway, "complete", complete)
    thread = _create_thread(client)
    response = client.post(
        f"/api/v1/threads/{thread['id']}/turns", json={"input": "交通报销如何审批？"}
    )
    assert response.status_code == 202
    run = _wait_terminal(client, response.json()["run"]["id"])
    assert run["status"] == "COMPLETED", run
    artifacts = client.get(f"/api/v1/runs/{run['id']}/artifacts").json()
    answer = next(a for a in artifacts if a["title"] == "Obsion answer")["inline_content"]
    assert answer["grounding"]["accepted"] == (verdict == "SUPPORTED")
    assert answer["verification"]["verified"] == (verdict == "SUPPORTED")
    steps = client.get(f"/api/v1/runs/{run['id']}/steps").json()
    check = next(s for s in steps if s["kind"] == "VERIFY")

    async def persisted() -> tuple[dict[str, Any], dict[str, Any]]:
        async with client.app.state.database.sessions() as session:
            step = await session.get(RunStep, UUID(check["id"]))
            assessment = await session.scalar(
                select(VerificationAssessment).where(
                    VerificationAssessment.run_id == UUID(run["id"])
                )
            )
            assert step is not None and assessment is not None
            return step.input_payload["grounding"], assessment.replay_lineage

    assert client.portal is not None
    saved, lineage = client.portal.call(persisted)
    assert saved == answer["grounding"]
    assert lineage["deterministic"] is False and lineage["grounding"] == saved
    assert run["input_tokens"] == (30 if verdict == "SUPPORTED" else 60)
    assert run["output_tokens"] == (20 if verdict == "SUPPORTED" else 40)
    if verdict == "SUPPORTED":
        assert [c["title"] for c in answer["citations"]] == ["交通报销审批"]
        assert "其他归档说明" not in answer["markdown"]
        assert evidence_ids[0] not in answer["markdown"]
        assert "chunk" not in answer["markdown"]
    else:
        assert answer["markdown"].startswith("不知道：") and answer["citations"] == []
        assert all(candidate not in a["inline_content"].get("markdown", "") for a in artifacts)
    events = client.get(f"/api/v1/runs/{run['id']}/events").json()
    assert [e["payload"]["delta"] for e in events if e["name"] == "answer.delta"] == [
        answer["markdown"]
    ]
