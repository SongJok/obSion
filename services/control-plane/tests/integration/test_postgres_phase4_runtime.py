from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from obsion.application.workspaces import WorkspaceService
from obsion.common.time import utc_now
from obsion.config import get_settings
from obsion.db.models import (
    AuditRecord,
    Event,
    Organization,
    Run,
    RunStep,
    Thread,
    Turn,
    User,
    Workspace,
)
from obsion.domain.enums import RunStatus, StepKind, StepStatus
from obsion.security.identity import Principal


@pytest.mark.asyncio
async def test_postgres_cancel_atomically_terminates_run_steps_events_and_audit() -> None:
    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL invariant tests are opt-in")

    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    connection = await engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(bind=connection, expire_on_commit=False, autoflush=False)
    organization_id = uuid4()
    user_id = uuid4()
    run_id = uuid4()
    now = utc_now()
    try:
        organization = Organization(
            id=organization_id,
            slug=f"phase4-cancel-{organization_id}",
            name="Phase 4 cancellation",
            active=True,
            settings={},
        )
        user = User(
            id=user_id,
            organization_id=organization_id,
            external_id=f"phase4-{user_id}",
            email=f"{user_id}@example.invalid",
            display_name="Phase 4 owner",
            active=True,
            attributes={},
        )
        session.add_all([organization, user])
        await session.flush()
        workspace = Workspace(
            organization_id=organization_id,
            name="Phase 4",
            owner_id=user_id,
        )
        session.add(workspace)
        await session.flush()
        thread = Thread(
            organization_id=organization_id,
            workspace_id=workspace.id,
            title="Cancellation",
            created_by=user_id,
        )
        session.add(thread)
        await session.flush()
        turn = Turn(
            organization_id=organization_id,
            thread_id=thread.id,
            ordinal=1,
            created_by=user_id,
            input_text="cancel",
            sanitized_input="cancel",
            context_refs=[],
            attachment_refs=[],
            created_at=now,
        )
        session.add(turn)
        await session.flush()
        run = Run(
            id=run_id,
            organization_id=organization_id,
            turn_id=turn.id,
            status=RunStatus.WAITING_APPROVAL,
        )
        session.add(run)
        await session.flush()
        session.add_all(
            [
                RunStep(
                    organization_id=organization_id,
                    run_id=run_id,
                    ordinal=1,
                    name="Waiting approval",
                    kind=StepKind.CAPABILITY,
                    status=StepStatus.WAITING_APPROVAL,
                    depends_on=[],
                    input_payload={},
                ),
                RunStep(
                    organization_id=organization_id,
                    run_id=run_id,
                    ordinal=2,
                    name="Dependent",
                    kind=StepKind.CAPABILITY,
                    status=StepStatus.PENDING,
                    depends_on=[1],
                    input_payload={},
                ),
            ]
        )
        await session.flush()

        principal = Principal(
            id=user_id,
            organization_id=organization_id,
            external_id=user.external_id,
            display_name=user.display_name,
            permissions=frozenset({"*"}),
        )
        cancelled = await WorkspaceService(settings).cancel_run(session, principal, run_id)
        await session.flush()

        steps = list(
            await session.scalars(
                select(RunStep).where(RunStep.run_id == run_id).order_by(RunStep.ordinal)
            )
        )
        events = list(
            await session.scalars(
                select(Event).where(Event.run_id == run_id).order_by(Event.run_sequence)
            )
        )
        audit = await session.scalar(
            select(AuditRecord).where(
                AuditRecord.organization_id == organization_id,
                AuditRecord.action == "run.cancel",
                AuditRecord.resource_id == str(run_id),
            )
        )

        assert cancelled.status == RunStatus.CANCELLED
        assert cancelled.completed_at is not None
        assert cancelled.lease_owner is None
        assert cancelled.lease_expires_at is None
        assert [item.status for item in steps] == [StepStatus.CANCELLED, StepStatus.CANCELLED]
        assert [item.name for item in events] == [
            "run.cancellation_requested",
            "run.cancelled",
        ]
        assert [item.run_sequence for item in events] == [1, 2]
        assert audit is not None
        assert audit.redacted_metadata == {
            "previous_status": "WAITING_APPROVAL",
            "cancelled_steps": 2,
        }
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()
