import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.db.models import (
    AgentDefinition,
    AgentVersion,
    Evidence,
    Run,
    RunConversationSnapshot,
    Turn,
)
from obsion.domain.enums import Classification, EvidenceType, RunStatus
from obsion.harness.runtime import HarnessRuntime
from obsion.model_gateway.gateway import ModelResult


class _RecordingModelGateway:
    def __init__(self, evidence_id: str) -> None:
        self.evidence_id = evidence_id
        self.messages: list[dict[str, str]] = []
        self.classification: Classification | None = None

    async def complete(self, _session: AsyncSession, **kwargs: Any) -> ModelResult:
        self.messages = kwargs["messages"]
        self.classification = kwargs["classification"]
        return ModelResult(
            content=json.dumps(
                {
                    "answer": "Governed answer",
                    "claims": [
                        {
                            "statement": "Supported claim",
                            "evidence_ids": [self.evidence_id],
                            "confidence": 0.9,
                        }
                    ],
                }
            ),
            profile_id=uuid4(),
            endpoint_id=uuid4(),
            input_tokens=100,
            output_tokens=20,
            latency_ms=10,
            cost_amount=Decimal("0.001"),
            finish_reason="stop",
        )


@pytest.mark.asyncio
async def test_harness_supplies_frozen_history_without_treating_it_as_evidence() -> None:
    organization_id = uuid4()
    current_user_id = uuid4()
    other_user_id = uuid4()
    run_id = uuid4()
    turn_id = uuid4()
    profile_id = uuid4()
    evidence_id = uuid4()
    now = datetime.now(UTC)
    run = Run(
        id=run_id,
        organization_id=organization_id,
        turn_id=turn_id,
        status=RunStatus.RUNNING,
        agent_version_id=uuid4(),
        model_profile_id=profile_id,
        intent={},
        plan={},
        max_steps=30,
        timeout_seconds=300,
        max_input_tokens=120_000,
        max_output_tokens=16_000,
        max_cost_amount=Decimal("10"),
        step_count=0,
        input_tokens=0,
        output_tokens=0,
        cost_amount=Decimal("0"),
        aggregate_version=0,
    )
    turn = Turn(
        id=turn_id,
        organization_id=organization_id,
        thread_id=uuid4(),
        ordinal=3,
        created_by=current_user_id,
        input_text="current question",
        sanitized_input="current question",
        context_refs=[],
        attachment_refs=[],
        created_at=now,
    )
    definition = AgentDefinition(
        id=uuid4(),
        organization_id=organization_id,
        name="general-agent",
        display_name="General Agent",
        description="",
    )
    version = AgentVersion(
        id=run.agent_version_id,
        organization_id=organization_id,
        agent_id=definition.id,
        version=1,
        spec={"purpose": "governed investigation"},
        checksum_sha256="a" * 64,
    )
    evidence = Evidence(
        id=evidence_id,
        organization_id=organization_id,
        run_id=run_id,
        evidence_type=EvidenceType.DOCUMENT,
        source="knowledge",
        resource="document:policy",
        observed_at=now,
        ingested_at=now,
        content={"text": "current governed evidence"},
        content_fingerprint="b" * 64,
        confidence=Decimal("1"),
        classification=Classification.INTERNAL,
        permissions=["knowledge.read"],
        lineage={},
    )
    snapshots = [
        RunConversationSnapshot(
            id=uuid4(),
            organization_id=organization_id,
            run_id=run_id,
            source_thread_id=turn.thread_id,
            source_turn_id=uuid4(),
            source_run_id=uuid4(),
            source_principal_id=current_user_id,
            ordinal=1,
            user_content="previous question",
            assistant_content="previous answer",
            content_fingerprint="c" * 64,
            classification=Classification.INTERNAL,
            captured_at=now,
        ),
        RunConversationSnapshot(
            id=uuid4(),
            organization_id=organization_id,
            run_id=run_id,
            source_thread_id=turn.thread_id,
            source_turn_id=uuid4(),
            source_principal_id=other_user_id,
            ordinal=2,
            user_content="other member instruction",
            assistant_content=None,
            content_fingerprint="d" * 64,
            classification=Classification.CONFIDENTIAL,
            captured_at=now,
        ),
    ]
    gateway = _RecordingModelGateway(str(evidence_id))
    runtime = HarnessRuntime.__new__(HarnessRuntime)
    runtime.models = cast(Any, gateway)

    answer, claims = await runtime._synthesize(
        cast(AsyncSession, None),
        run,
        turn,
        version,
        definition,
        [evidence],
        [],
        snapshots,
    )

    assert answer == "Governed answer"
    assert claims[0]["evidence_ids"] == [str(evidence_id)]
    assert gateway.classification == Classification.CONFIDENTIAL
    contents = [item["content"] for item in gateway.messages]
    assert contents.index("previous question") < contents.index("previous answer")
    assert contents.index("previous answer") < contents.index("current question")
    other_member = next(
        item for item in gateway.messages if "other member instruction" in item["content"]
    )
    assert other_member["role"] == "user"
    assert "untrusted-data" in other_member["content"]
    assert "current governed evidence" in next(
        item["content"] for item in gateway.messages if "evidence-bus" in item["content"]
    )
    assert run.conversation_compact["method"] == "extractive"
    assert run.conversation_compact["summarized_turns"] == 0
    assert run.conversation_compact["kept_turns"] == 2
