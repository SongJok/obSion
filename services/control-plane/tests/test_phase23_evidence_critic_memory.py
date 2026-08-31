from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from obsion.common.time import utc_now
from obsion.config import Environment, Settings
from obsion.db.base import Base
from obsion.db.models import (
    Artifact,
    Event,
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
from obsion.domain.enums import (
    ArtifactKind,
    Classification,
    EvidenceType,
    RunStatus,
    StepKind,
    StepStatus,
)
from obsion.harness.critic import Critic
from obsion.harness.evidence_gaps import select_gap_capabilities
from obsion.harness.runtime import HarnessRuntime
from obsion.persistence.events import EventStore


def _evidence(kind: EvidenceType, content: dict) -> Evidence:
    now = datetime.now(UTC)
    return Evidence(
        id=uuid4(),
        organization_id=uuid4(),
        run_id=uuid4(),
        evidence_type=kind,
        source="test",
        resource="resource://test",
        observed_at=now,
        ingested_at=now,
        content=content,
        content_fingerprint="a" * 64,
        confidence=Decimal("1.0"),
        classification=Classification.INTERNAL,
        permissions=[],
        lineage={},
    )


def test_gap_selector_uses_unused_authorized_capabilities_only() -> None:
    selected = select_gap_capabilities(
        ("LOG", "METRIC", "GIT"),
        available=frozenset({"metric.query", "log.search", "git.diff"}),
        attempted=frozenset({"metric.query"}),
    )
    assert selected == [("LOG", "log.search"), ("GIT", "git.diff")]


def test_gap_selector_does_not_retry_attempted_capabilities() -> None:
    selected = select_gap_capabilities(
        ("CODE",),
        available=frozenset({"code.symbol", "code.search"}),
        attempted=frozenset({"code.symbol", "code.search"}),
    )
    assert selected == []


def test_critic_treats_sql_evidence_as_satisfying_data() -> None:
    item = _evidence(EvidenceType.SQL, {"sql": "SELECT 1", "rows": [{"value": 1}]})
    result = Critic().verify(
        [item],
        required_types=("DATA", "SQL"),
        claims=[{"statement": "Query returned one row", "evidence_ids": [str(item.id)]}],
    )
    assert result.missing_evidence == ()
    assert result.coverage == 1.0


def test_critic_accepts_git_as_independent_cause_artifact() -> None:
    metric = _evidence(
        EvidenceType.METRIC, {"events": [{"service": "payments", "status": "critical"}]}
    )
    git = _evidence(
        EvidenceType.GIT,
        {"items": [{"repository": "acme/payments", "commit_id": "abc123"}], "count": 1},
    )
    result = Critic().verify(
        [metric, git],
        required_types=("METRIC",),
        claims=[
            {
                "statement": "Latency increased because the release changed timeouts",
                "evidence_ids": [str(metric.id), str(git.id)],
            }
        ],
        route="INCIDENT",
        question="Why did latency increase?",
        answer="Latency increased because the release changed timeouts",
    )
    assert result.verified
    assert not any(
        "alternative_explanation_unchecked" in conflict.get("reason_codes", [])
        for conflict in result.conflicts
        if isinstance(conflict, dict)
    )


@pytest.mark.asyncio
async def test_critic_missing_evidence_replan_is_bounded_and_inserts_before_verify(
    tmp_path,
) -> None:
    settings = Settings(
        environment=Environment.TEST,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'critic-replan.db'}",
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
                slug="critic-replan",
                name="Critic Replan",
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
                    "required_evidence": ["METRIC", "LOG"],
                    "available_capabilities": ["metric.query", "log.search"],
                    "steps": [
                        {
                            "ordinal": 1,
                            "name": "Metric",
                            "capability": "metric.query",
                            "payload": {
                                "operation": "metric.query",
                                "query": "Investigate latency",
                                "service": "payments",
                            },
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
                            "payload": {
                                "operation": "metric.query",
                                "query": "Investigate latency",
                                "service": "payments",
                            },
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
                        status=StepStatus.PENDING,
                        depends_on=[4],
                        input_payload={"required_evidence": ["METRIC", "LOG"]},
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
            fingerprint = "b" * 64
            session.add(
                Evidence(
                    organization_id=organization_id,
                    run_id=run.id,
                    evidence_type=EvidenceType.METRIC,
                    source="metrics",
                    resource="payments",
                    observed_at=utc_now(),
                    ingested_at=utc_now(),
                    content={"events": [{"service": "payments", "status": "critical"}]},
                    content_fingerprint=fingerprint,
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
        assert not await runtime._replan_missing_evidence(organization_id, run_id)

        async with database.sessions() as session:
            run = await session.get(Run, run_id)
            assert run is not None
            assert run.status == RunStatus.RUNNING
            assert run.plan["replans"][0]["reason"] == "critic_missing_evidence"
            assert run.plan["replans"][0]["capabilities"] == ["log.search"]
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
            assert steps[1].input_payload["capability"] == "log.search"
            assert steps[1].status == StepStatus.PENDING
            assert steps[2].ordinal == 6
            assert 5 in steps[2].depends_on
            assert steps[3].kind == StepKind.REFLECT
            assert steps[3].ordinal == 7
            assert steps[3].depends_on == [6]
            assert steps[4].ordinal == 8
            assert steps[4].depends_on == [7]
            events = list(
                await session.scalars(
                    select(Event).where(Event.run_id == run_id).order_by(Event.sequence)
                )
            )
            assert [event.name for event in events] == [
                "run.state_changed",
                "plan.updated",
                "run.state_changed",
            ]
    finally:
        await database.dispose()


def test_engineering_route_emits_code_diff_and_report_artifacts() -> None:
    now = utc_now()
    organization_id = uuid4()
    workspace_id = uuid4()
    run = Run(
        id=uuid4(),
        organization_id=organization_id,
        turn_id=uuid4(),
        status=RunStatus.RUNNING,
        plan={"route": "ENGINEERING"},
    )
    turn = Turn(
        id=run.turn_id,
        organization_id=organization_id,
        thread_id=uuid4(),
        ordinal=1,
        created_by=uuid4(),
        input_text="Where is checkout created?",
        sanitized_input="Where is checkout created?",
        context_refs=[],
        attachment_refs=[],
        created_at=now,
    )
    thread = Thread(
        id=turn.thread_id,
        organization_id=organization_id,
        workspace_id=workspace_id,
        title="Code",
        created_by=turn.created_by,
    )
    code = Evidence(
        id=uuid4(),
        organization_id=organization_id,
        run_id=run.id,
        evidence_type=EvidenceType.CODE,
        source="code-graph",
        resource="payments/checkout.py",
        observed_at=now,
        ingested_at=now,
        content={
            "items": [
                {
                    "repository": "acme/payments",
                    "path": "checkout.py",
                    "qualified_name": "create_order",
                    "kind": "FUNCTION",
                    "commit_id": "abc123",
                }
            ],
            "count": 1,
        },
        content_fingerprint="c" * 64,
        confidence=Decimal("1.0"),
        classification=Classification.INTERNAL,
        permissions=["code.read"],
        lineage={},
    )
    git = Evidence(
        id=uuid4(),
        organization_id=organization_id,
        run_id=run.id,
        evidence_type=EvidenceType.GIT,
        source="git",
        resource="acme/payments",
        observed_at=now,
        ingested_at=now,
        content={
            "items": [
                {
                    "repository": "acme/payments",
                    "commit_id": "abc123",
                    "attributes": {"files": ["checkout.py"], "patch": "+ return 201"},
                }
            ],
            "count": 1,
        },
        content_fingerprint="d" * 64,
        confidence=Decimal("1.0"),
        classification=Classification.INTERNAL,
        permissions=["code.read"],
        lineage={},
    )
    runtime = object.__new__(HarnessRuntime)
    artifacts = runtime._engineering_result_artifacts(run, turn, thread, [code, git])
    kinds = {item.kind for item in artifacts}
    assert kinds == {ArtifactKind.CODE, ArtifactKind.DIFF, ArtifactKind.REPORT}
    assert all(isinstance(item, Artifact) for item in artifacts)
