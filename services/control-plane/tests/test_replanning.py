from uuid import uuid4

import pytest
from sqlalchemy import select

from obsion.common.time import utc_now
from obsion.config import Environment, Settings
from obsion.db.base import Base
from obsion.db.models import Event, Organization, Run, RunStep, Thread, Turn, User, Workspace
from obsion.db.session import Database
from obsion.domain.enums import RunStatus, StepKind, StepStatus
from obsion.harness.runtime import HarnessRuntime
from obsion.persistence.events import EventStore


@pytest.mark.asyncio
async def test_transient_replan_is_bounded_persistent_and_restores_dependents(tmp_path) -> None:
    settings = Settings(
        environment=Environment.TEST,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'replanning.db'}",
    )
    database = Database(settings)
    organization_id = uuid4()
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with database.sessions() as session, session.begin():
            organization = Organization(
                id=organization_id,
                slug="replanning",
                name="Replanning",
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
                input_text="Investigate",
                sanitized_input="Investigate",
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
                plan={"route": "INCIDENT", "steps": []},
                step_count=4,
            )
            session.add(run)
            await session.flush()
            session.add_all(
                [
                    RunStep(
                        organization_id=organization_id,
                        run_id=run.id,
                        ordinal=3,
                        name="Metric",
                        kind=StepKind.CAPABILITY,
                        status=StepStatus.FAILED,
                        depends_on=[],
                        input_payload={},
                        error_code="capability_timeout",
                        retry_count=0,
                        max_retries=1,
                    ),
                    RunStep(
                        organization_id=organization_id,
                        run_id=run.id,
                        ordinal=4,
                        name="Logs",
                        kind=StepKind.CAPABILITY,
                        status=StepStatus.SKIPPED,
                        depends_on=[3],
                        input_payload={},
                        error_code="dependency_failed",
                        retry_count=0,
                        max_retries=1,
                    ),
                ]
            )
            run_id = run.id

        runtime = object.__new__(HarnessRuntime)
        runtime.database = database
        runtime.events = EventStore()
        assert await runtime._replan_transient_failures(organization_id, run_id)

        async with database.sessions() as session:
            run = await session.get(Run, run_id)
            assert run is not None
            assert run.status == RunStatus.RUNNING
            assert run.plan["replans"][0]["step_ordinals"] == [3, 4]
            steps = list(
                await session.scalars(
                    select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.ordinal)
                )
            )
            assert [item.status for item in steps] == [StepStatus.PENDING, StepStatus.PENDING]
            assert steps[0].retry_count == 1
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
