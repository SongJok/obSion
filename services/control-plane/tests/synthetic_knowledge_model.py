"""Opt-in synthetic author/reviewer for tests whose subject is another subsystem."""

import json
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select

from obsion.db.models import Evidence
from obsion.knowledge.evidence import document_bodies
from obsion.model_gateway.gateway import ModelGateway, ModelResult


def install_grounded_answer(monkeypatch, statement):
    stages = []

    async def complete(self, session, **kwargs):
        items = list(
            await session.scalars(select(Evidence).where(Evidence.run_id == kwargs["run_id"]))
        )
        source = next(
            (e for e in items if any(statement in body.text for body in document_bodies(e))), None
        )
        assert source is not None, (
            "The synthetic statement must exist in authorized document bodies"
        )
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
    return stages
