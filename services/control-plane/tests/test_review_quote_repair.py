import json
from decimal import Decimal
from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from test_critic import evidence
from test_knowledge_grounding import SOURCE, review

from obsion.common.time import utc_now
from obsion.db.models import Run
from obsion.domain.enums import Classification, EvidenceType
from obsion.harness.grounding import QUOTE_REPAIR_POLICY, review_knowledge_answer
from obsion.model_gateway.gateway import ModelGateway, ModelResult


@pytest.mark.parametrize(
    "mode",
    [
        "repaired",
        "still_bad",
        "decline",
        "input",
        "output",
        "cost",
        "cancel",
        "deadline",
        "negative",
        "late_cancel",
        "late_deadline",
    ],
)
async def test_quote_repair_is_bounded_budgeted_and_cannot_reroll_negative_review(mode):
    item = evidence(EvidenceType.DOCUMENT, "knowledge", "source")
    item.content = {"text": SOURCE}
    run = Run(
        id=item.run_id,
        organization_id=item.organization_id,
        model_profile_id=uuid4(),
        max_input_tokens=10 if mode == "input" else 10000,
        max_output_tokens=10 if mode == "output" else 2000,
        max_cost_amount=Decimal("0.1") if mode == "cost" else Decimal("1"),
        input_tokens=0,
        output_tokens=0,
        cost_amount=Decimal("0"),
    )
    claims = [{"statement": "交通报销需要审批。", "evidence_ids": [str(item.id)]}]
    first = review(str(item.id))
    first["claims"][0]["quotes"][0]["quote"] = "NOT_EXACT_QUOTE_FOR_TEST"
    if mode == "negative":
        claims.append({"statement": "其他要求。", "evidence_ids": [str(item.id)]})
        first["claims"].append({"claim_index": 2, "verdict": "INSUFFICIENT", "quotes": []})
    requests = []

    async def complete(*args, **kwargs):
        requests.append(kwargs)
        if len(requests) == 1:
            if mode == "cancel":
                run.cancellation_requested_at = utc_now()
            if mode == "deadline":
                run.deadline_at = utc_now()
        if len(requests) == 2:
            if mode == "late_cancel":
                run.cancellation_requested_at = utc_now()
            if mode == "late_deadline":
                run.deadline_at = utc_now()
        payload = first
        if len(requests) == 2 and mode != "still_bad":
            payload = review(str(item.id), "INSUFFICIENT" if mode == "decline" else "SUPPORTED")
        return ModelResult(
            content=json.dumps(payload),
            profile_id=run.model_profile_id,
            endpoint_id=uuid4(),
            input_tokens=10,
            output_tokens=10,
            cost_amount=Decimal("0.1"),
            latency_ms=1,
            finish_reason="stop",
        )

    models = cast(ModelGateway, AsyncMock())
    models.complete.side_effect = complete  # type: ignore[attr-defined]
    result = await review_knowledge_answer(
        models,
        cast(AsyncSession, None),
        run=run,
        step_id=uuid4(),
        question="交通报销如何审批？",
        answer="交通报销需要审批。",
        claims=claims,
        evidence=[item],
        classification=Classification.RESTRICTED,
    )
    count = 2 if mode in {"repaired", "still_bad", "decline", "late_cancel", "late_deadline"} else 1
    assert len(requests) == len(result.attempts) == count
    assert result.accepted is (mode == "repaired")
    assert run.input_tokens == run.output_tokens == 10 * count
    assert run.cost_amount == Decimal("0.1") * count
    assert result.attempts[0]["diagnostic"] == "quote_not_exact"
    assert "NOT_EXACT_QUOTE_FOR_TEST" not in json.dumps(result.summary())
    if count == 2:
        original = json.loads(requests[0]["messages"][1]["content"])
        correction = json.loads(requests[1]["messages"][1]["content"])
        assert {k: v for k, v in original.items() if k != "sources"} == {
            k: v for k, v in correction.items() if k != "sources"
        }
        assert "".join(p["text"] for p in correction["sources"][str(item.id)]) == SOURCE
        assert requests[1]["messages"][0]["content"].endswith(QUOTE_REPAIR_POLICY)
        assert "NOT_EXACT_QUOTE_FOR_TEST" not in json.dumps(requests[1]["messages"])
        assert requests[1]["classification"] == Classification.RESTRICTED
        assert requests[1]["max_input_tokens"] == 9990
        assert requests[1]["max_output_tokens"] == 1990
        assert requests[1]["max_cost_amount"] == Decimal("0.9")
        assert result.attempts[0]["input_fingerprint"] != result.attempts[1]["input_fingerprint"]
    if mode == "decline":
        assert result.reason_code == "grounding_not_supported"
    if mode in {"input", "output", "cost"}:
        assert result.reason_code == "grounding_budget_unavailable"
    if mode in {"late_cancel", "late_deadline"}:
        assert result.reason_code == "grounding_repair_stopped"
