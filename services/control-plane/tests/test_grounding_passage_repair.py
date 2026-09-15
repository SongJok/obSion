import copy
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
from obsion.harness.grounding import (
    InvalidReview,
    ReviewDiagnostic,
    _resolve_passage_quotes,
    review_knowledge_answer,
    validate_review,
)
from obsion.knowledge.passages import PASSAGE_CHARACTERS, source_passages
from obsion.model_gateway.gateway import ModelGateway, ModelResult


def test_passages_partition_exact_unicode_bodies_and_bind_each_source():
    body = ("第一项：需要审批。\n" * 300) + "结束。"
    sources = {"a": [body, SOURCE], "b": [body]}
    displayed, lookup = source_passages(sources)
    first = [p for p in displayed["a"] if p["text"] != SOURCE]
    assert "".join(p["text"] for p in first) == body
    assert all(0 < len(p["text"]) <= PASSAGE_CHARACTERS for p in first)
    assert displayed == source_passages(sources)[0]
    assert len(lookup) == sum(map(len, displayed.values()))
    assert {p["passage_id"] for p in displayed["a"]}.isdisjoint(
        p["passage_id"] for p in displayed["b"]
    )
    assert source_passages({"a": ["changed " + body]})[0]["a"][0] != first[0]


def test_redacted_bodies_are_omitted_before_partitioning_secret_blocks():
    secret = "-----BEGIN PRIVATE KEY-----\n" + "x" * 4000 + "\n-----END PRIVATE KEY-----"
    displayed, lookup = source_passages({"a": [secret, SOURCE], "b": ["password=synthetic"]})
    assert displayed["a"] == [{"passage_id": next(iter(lookup))[1], "text": SOURCE}]
    assert displayed["b"] == []
    assert secret not in json.dumps(displayed)
    assert "x" * 100 not in json.dumps(displayed)


@pytest.mark.parametrize(
    "mode", ["valid", "unknown", "other_source", "extra_text", "type", "short"]
)
def test_references_resolve_only_to_current_exact_substantive_source(mode):
    source = "短句" if mode == "short" else SOURCE
    # A short passage cannot establish support when it is only part of a body.
    sources = {"a": [source], "b": [source]}
    displayed, lookup = source_passages(sources)
    payload = review("a")
    passage_id = displayed["a"][0]["passage_id"]
    quote = {"evidence_id": "a", "passage_id": passage_id}
    if mode == "unknown":
        quote["passage_id"] = "p-unknown"
    if mode == "other_source":
        quote["passage_id"] = displayed["b"][0]["passage_id"]
    if mode == "extra_text":
        quote["quote"] = SOURCE
    if mode == "type":
        quote["passage_id"] = []
    payload["claims"][0]["quotes"] = [quote]
    original = copy.deepcopy(payload)
    claims = [{"statement": "需要审批。", "evidence_ids": ["a"]}]
    if mode in {"unknown", "other_source", "type"}:
        with pytest.raises(InvalidReview) as error:
            _resolve_passage_quotes(payload, lookup)
        assert error.value.diagnostic == ReviewDiagnostic.QUOTE_REFERENCE_INVALID
    else:
        resolved = _resolve_passage_quotes(payload, lookup)
        if mode == "short":
            sources["a"] = [source + "，还需要其他审批。"]
        accepted, checked = validate_review(resolved, claims, sources)
        assert accepted is (mode == "valid")
        if accepted:
            q = checked[0]["quotes"][0]
            assert sources["a"][q["body_index"]][q["start"] : q["start"] + q["length"]] == SOURCE
    assert payload == original


@pytest.mark.parametrize("mode", ["supported", "denied", "unknown", "all_redacted"])
async def test_real_repair_protocol_retains_semantic_review_and_two_call_budget(mode):
    item = evidence(EvidenceType.DOCUMENT, "knowledge", "source")
    item.content = {"text": "password=synthetic" if mode == "all_redacted" else SOURCE}
    run = Run(
        id=item.run_id,
        organization_id=item.organization_id,
        model_profile_id=uuid4(),
        max_input_tokens=10000,
        max_output_tokens=4000,
        max_cost_amount=Decimal("1"),
        input_tokens=0,
        output_tokens=0,
        cost_amount=Decimal("0"),
    )
    requests = []

    async def complete(*args, **kwargs):
        requests.append(kwargs)
        payload = review(str(item.id))
        payload["claims"][0]["quotes"][0]["quote"] = "invalid copied quotation"
        if len(requests) == 2:
            sent = json.loads(kwargs["messages"][1]["content"])
            passage = sent["sources"][str(item.id)][0]
            payload["claims"][0]["quotes"] = [
                {
                    "evidence_id": str(item.id),
                    "passage_id": "unknown" if mode == "unknown" else passage["passage_id"],
                }
            ]
            if mode == "denied":
                payload["answer_supported"] = False
                payload["claims"][0]["verdict"] = "INSUFFICIENT"
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
    models.complete.side_effect = complete
    result = await review_knowledge_answer(
        models,
        cast(AsyncSession, None),
        run=run,
        step_id=uuid4(),
        question="如何审批？",
        answer="需要审批。",
        claims=[{"statement": "需要审批。", "evidence_ids": [str(item.id)]}],
        evidence=[item],
        classification=Classification.INTERNAL,
    )
    count = 1 if mode == "all_redacted" else 2
    assert len(requests) == len(result.attempts) == count
    assert run.input_tokens == run.output_tokens == 10 * count
    assert run.cost_amount == Decimal("0.1") * count
    assert result.accepted is (mode == "supported")
    assert SOURCE not in json.dumps(result.summary())
    if mode == "unknown":
        assert result.diagnostic == ReviewDiagnostic.QUOTE_REFERENCE_INVALID
    if mode == "denied":
        assert result.reason_code == "grounding_not_supported"
    if mode == "all_redacted":
        assert result.reason_code == "grounding_sources_incomplete"
