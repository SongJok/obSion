"""Explicit offline model fixture for backup row coverage, never real model evidence."""

import json
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.config import Environment, Settings
from obsion.db.models import Evidence, Run, Turn
from obsion.knowledge.evidence import document_bodies
from obsion.model_gateway.gateway import ModelGateway, ModelResult, ModelUnavailableError

DRILL_QUESTION = "What must every restore preserve?"
DRILL_STATEMENT = "Every restore must preserve audit history and evidence."


class OfflineDrillModels(ModelGateway):
    """Generate only the known synthetic drill statement through normal verification."""

    def __init__(self, settings: Settings) -> None:
        if settings.environment != Environment.TEST:
            raise ValueError("Offline drill models are restricted to the isolated test runtime")
        super().__init__(settings)

    async def complete(self, session: AsyncSession, **kwargs: Any) -> ModelResult:
        run = await session.get(Run, kwargs["run_id"])
        if run is None or run.organization_id != kwargs["organization_id"]:
            raise ModelUnavailableError("No isolated drill run")
        turn = await session.get(Turn, run.turn_id)
        if turn is None or turn.sanitized_input != DRILL_QUESTION:
            raise ModelUnavailableError("Only the fixed backup drill question is supported")
        items = await session.scalars(
            select(Evidence).where(
                Evidence.run_id == run.id, Evidence.organization_id == run.organization_id
            )
        )
        source = next(
            (
                item
                for item in items
                if any(
                    body.metadata.get("source") == "dr-drill" and DRILL_STATEMENT in body.text
                    for body in document_bodies(item)
                )
            ),
            None,
        )
        if source is None:
            raise ModelUnavailableError("The controlled drill document is required")
        run.plan = {
            **run.plan,
            "model_fixture": {"kind": "offline_restore_dataset", "real_model_calls": False},
        }
        if kwargs.get("step_id") is None:
            payload = {
                "answerable": True,
                "answer": DRILL_STATEMENT,
                "claims": [{"statement": DRILL_STATEMENT, "evidence_ids": [str(source.id)]}],
            }
        else:
            payload = {
                "answer_supported": True,
                "question_answered": True,
                "claims": [
                    {
                        "claim_index": 1,
                        "verdict": "SUPPORTED",
                        "quotes": [{"evidence_id": str(source.id), "quote": DRILL_STATEMENT}],
                    }
                ],
            }
        # No endpoint is invoked and no ModelCall is fabricated. The sentinel is
        # local result metadata; the durable Run explicitly labels this fixture.
        return ModelResult(
            content=json.dumps(payload),
            profile_id=kwargs["profile_id"],
            endpoint_id=UUID("00000000-0000-7000-8000-000000000000"),
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            cost_amount=Decimal("0"),
            finish_reason="stop",
        )
