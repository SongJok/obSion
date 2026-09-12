import json
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_critic import evidence
from test_knowledge_grounding import SOURCE, review

from obsion.db.models import Run
from obsion.domain.enums import Classification, EvidenceType
from obsion.harness.grounding import review_knowledge_answer
from obsion.model_gateway.gateway import ModelGateway, ModelResult


@pytest.mark.parametrize(
    "answer_supported,question_answered",
    [(True, True), (False, True), (True, False), (False, False)],
)
@pytest.mark.parametrize("invalid", [None, "schema", "truncated"])
async def test_global_review_decisions_are_retained_only_after_full_local_validation(
    answer_supported: bool, question_answered: bool, invalid: str | None
) -> None:
    item = evidence(EvidenceType.DOCUMENT, "knowledge", "source")
    item.content = {"hits": [{"content": SOURCE, "title": "untrusted citation label"}]}
    run = Run(
        id=item.run_id,
        organization_id=item.organization_id,
        model_profile_id=uuid4(),
        max_input_tokens=10000,
        max_output_tokens=2000,
        max_cost_amount=Decimal("1"),
        input_tokens=0,
        output_tokens=0,
        cost_amount=Decimal("0"),
    )
    payload = review(str(item.id))
    payload.update(answer_supported=answer_supported, question_answered=question_answered)
    if invalid == "schema":
        payload["claims"][0]["quotes"][0]["quote"] = "fabricated quotation"
    models = cast(ModelGateway, AsyncMock())
    models.complete.return_value = ModelResult(  # type: ignore[attr-defined]
        content=json.dumps(payload),
        profile_id=run.model_profile_id,
        endpoint_id=uuid4(),
        input_tokens=10,
        output_tokens=10,
        cost_amount=Decimal("0.01"),
        latency_ms=1,
        finish_reason="length" if invalid == "truncated" else "stop",
    )
    assessment = await review_knowledge_answer(
        models,
        cast(AsyncSession, None),
        run=run,
        step_id=None,
        question="交通报销如何审批？请注明出处。",
        answer="交通报销须先审批后报销。",
        claims=[{"statement": "交通报销须先审批后报销。", "evidence_ids": [str(item.id)]}],
        evidence=[item],
        classification=Classification.RESTRICTED,
    )
    summary = assessment.summary()
    assert assessment.accepted is (answer_supported and question_answered and invalid is None)
    assert summary["answer_supported"] is (answer_supported if invalid is None else None)
    assert summary["question_answered"] is (question_answered if invalid is None else None)
    if invalid is None:
        assert summary["claims"][0]["verdict"] == "SUPPORTED"
    else:
        assert not summary["claims"]
    assert SOURCE not in json.dumps(summary, ensure_ascii=False)
    messages = models.complete.call_args.kwargs["messages"]  # type: ignore[attr-defined]
    assert "untrusted citation label" not in str(messages)
    assert run.input_tokens == run.output_tokens == 10
