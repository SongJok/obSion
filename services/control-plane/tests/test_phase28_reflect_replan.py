from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from obsion.common.time import utc_now
from obsion.config import Environment, Settings
from obsion.db.base import Base
from obsion.db.models import (
    Evidence,
    Organization,
    Run,
    RunStep,
    Thread,
    Turn,
    User,
    Workspace,
)
from obsion.db.session import Database
from obsion.domain.enums import Classification, EvidenceType, RunStatus, StepKind, StepStatus
from obsion.harness.critic import Critic, CriticResult
from obsion.harness.runtime import HarnessRuntime
from obsion.persistence.events import EventStore


def test_empty_metric_events_are_not_substantive() -> None:
    now = datetime.now(UTC)
    empty = Evidence(
        id=uuid4(),
        organization_id=uuid4(),
        run_id=uuid4(),
        evidence_type=EvidenceType.METRIC,
        source="metrics",
        resource="payments",
        observed_at=now,
        ingested_at=now,
        content={"events": []},
        content_fingerprint="c" * 64,
        confidence=Decimal("1.0"),
        classification=Classification.INTERNAL,
        permissions=["metric.read"],
        lineage={},
    )
    assert Critic.substantive_records([empty]) == []
    assert Critic.missing_required_types([empty], ("METRIC",)) == ("METRIC",)


def test_reflect_replans_when_required_types_are_missing() -> None:
    critic = CriticResult(
        verified=False,
        confidence=0.1,
        coverage=0.0,
        missing_evidence=("LOG",),
        conflicts=(),
        checks={},
    )
    assert HarnessRuntime._reflect_decision(critic=critic, evidence_free_response=False) == "REPLAN"


@pytest.mark.asyncio
async def test_missing_evidence_replan_ignores_empty_event_lists(tmp_path) -> None:
    settings = Settings(
        environment=Environment.TEST,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'phase28.db'}",
        run_max_critic_replans=1,
    )
    database = Database(settings)
    organization_id = uuid4()
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with database.sessions() as session, session.begin():
            organization = Organization(
                id=organization_id,
                slug="phase28",
                name="Phase 28",
                active=True,
                settings={},
            )
            user = User(
                organization_id=organization_id,
                external_id="operator",
                email="operator@example.test",
                display_name="Operator",
                attributes={},
            )
            session.add_all([organization, user])
            await session.flush()
            workspace = Workspace(
                organization_id=organization_id,
                name="Incident",
                owner_id=user.id,
            )
            session.add(workspace)
            await session.flush()
            thread = Thread(
                organization_id=organization_id,
                workspace_id=workspace.id,
                title="Incident",
                created_by=user.id,
            )
            session.add(thread)
            await session.flush()
            turn = Turn(
                organization_id=organization_id,
                thread_id=thread.id,
                ordinal=1,
                created_by=user.id,
                input_text="Investigate latency",
                sanitized_input="Investigate latency",
                context_refs=[],
                attachment_refs=[],
                created_at=utc_now(),
            )
            session.add(turn)
            await session.flush()
            run = Run(
                organization_id=organization_id,
                turn_id=turn.id,
                status=RunStatus.RUNNING,
                plan={
                    "route": "INCIDENT",
                    "required_evidence": ["METRIC"],
                    "available_capabilities": ["metric.query", "metric.compare"],
                    "steps": [
                        {
                            "ordinal": 1,
                            "name": "Metric",
                            "capability": "metric.query",
                            "payload": {"operation": "metric.query", "query": "latency"},
                            "resource": {"environment": "production", "evidence_type": "METRIC"},
                            "environment": "production",
                            "depends_on": [],
                        }
                    ],
                },
                step_count=4,
            )
            session.add(run)
            await session.flush()
            session.add_all(
                [
                    RunStep(
                        organization_id=organization_id,
                        run_id=run.id,
                        ordinal=4,
                        name="Query metric baseline",
                        kind=StepKind.CAPABILITY,
                        status=StepStatus.COMPLETED,
                        depends_on=[3],
                        input_payload={
                            "capability": "metric.query",
                            "payload": {"operation": "metric.query", "query": "latency"},
                            "resource": {"environment": "production", "evidence_type": "METRIC"},
                            "environment": "production",
                        },
                    ),
                    RunStep(
                        organization_id=organization_id,
                        run_id=run.id,
                        ordinal=5,
                        name="Verify evidence and claims",
                        kind=StepKind.VERIFY,
                        status=StepStatus.COMPLETED,
                        depends_on=[4],
                        input_payload={"required_evidence": ["METRIC"]},
                    ),
                    RunStep(
                        organization_id=organization_id,
                        run_id=run.id,
                        ordinal=6,
                        name="Reflect on verification and publication",
                        kind=StepKind.REFLECT,
                        status=StepStatus.PENDING,
                        depends_on=[5],
                        input_payload={},
                    ),
                    RunStep(
                        organization_id=organization_id,
                        run_id=run.id,
                        ordinal=7,
                        name="Publish governed response",
                        kind=StepKind.RESPOND,
                        status=StepStatus.PENDING,
                        depends_on=[6],
                        input_payload={},
                    ),
                ]
            )
            session.add(
                Evidence(
                    organization_id=organization_id,
                    run_id=run.id,
                    evidence_type=EvidenceType.METRIC,
                    source="metrics",
                    resource="payments",
                    observed_at=utc_now(),
                    ingested_at=utc_now(),
                    content={"events": []},
                    content_fingerprint="d" * 64,
                    confidence=Decimal("1.0"),
                    classification=Classification.INTERNAL,
                    permissions=["metric.read"],
                    lineage={},
                )
            )
            run_id = run.id

        runtime = object.__new__(HarnessRuntime)
        runtime.database = database
        runtime.events = EventStore()
        runtime.settings = settings
        runtime.critic = Critic()
        assert await runtime._replan_missing_evidence(organization_id, run_id)

        async with database.sessions() as session:
            run = await session.get(Run, run_id)
            assert run is not None
            assert run.plan["replans"][0]["reason"] == "critic_missing_evidence"
            assert run.plan["replans"][0]["missing_evidence"] == ["METRIC"]
            assert run.plan["replans"][0]["capabilities"] == ["metric.compare"]
            steps = list(
                await session.scalars(
                    select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.ordinal)
                )
            )
            kinds = [item.kind for item in steps]
            assert kinds == [
                StepKind.CAPABILITY,
                StepKind.CAPABILITY,
                StepKind.VERIFY,
                StepKind.REFLECT,
                StepKind.RESPOND,
            ]
            assert steps[1].input_payload["capability"] == "metric.compare"
            assert steps[1].status == StepStatus.PENDING
    finally:
        await database.dispose()
