import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from obsion.config import get_settings
from obsion.db.models import Organization, Run, RunFeedback, Thread, Turn, User, Workspace


@pytest.mark.asyncio
async def test_run_feedback_enforces_database_version_and_identity_invariants() -> None:
    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL invariant tests are opt-in")

    engine = create_async_engine(get_settings().database_url)
    organization_id = uuid4()
    user_id = uuid4()
    workspace_id = uuid4()
    thread_id = uuid4()
    turn_id = uuid4()
    run_id = uuid4()
    feedback_id = uuid4()
    now = datetime.now(UTC)

    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(
                insert(Organization).values(
                    id=organization_id,
                    slug=f"feedback-invariant-{organization_id}",
                    name="Feedback invariant",
                    active=True,
                    settings={},
                    created_at=now,
                    updated_at=now,
                )
            )
            await connection.execute(
                insert(User).values(
                    id=user_id,
                    organization_id=organization_id,
                    external_id="feedback-invariant-owner",
                    email=f"{user_id}@example.invalid",
                    display_name="Feedback invariant owner",
                    active=True,
                    attributes={},
                    created_at=now,
                    updated_at=now,
                )
            )
            await connection.execute(
                insert(Workspace).values(
                    id=workspace_id,
                    organization_id=organization_id,
                    name="Feedback invariant workspace",
                    description="",
                    owner_id=user_id,
                    classification="INTERNAL",
                    visibility="PRIVATE",
                    created_at=now,
                    updated_at=now,
                )
            )
            await connection.execute(
                insert(Thread).values(
                    id=thread_id,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    title="Feedback invariant thread",
                    status="ACTIVE",
                    created_by=user_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            await connection.execute(
                insert(Turn).values(
                    id=turn_id,
                    organization_id=organization_id,
                    thread_id=thread_id,
                    ordinal=1,
                    created_by=user_id,
                    input_text="Collect feedback",
                    sanitized_input="Collect feedback",
                    context_refs=[],
                    attachment_refs=[],
                    created_at=now,
                )
            )
            await connection.execute(
                insert(Run).values(
                    id=run_id,
                    organization_id=organization_id,
                    turn_id=turn_id,
                    status="COMPLETED",
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
                    started_at=now,
                    completed_at=now,
                    aggregate_version=0,
                    created_at=now,
                    updated_at=now,
                )
            )
            await connection.execute(
                insert(RunFeedback).values(
                    id=feedback_id,
                    organization_id=organization_id,
                    run_id=run_id,
                    user_id=user_id,
                    rating="HELPFUL",
                    reason="",
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )

            await connection.execute(
                update(RunFeedback)
                .where(RunFeedback.id == feedback_id)
                .values(rating="NEEDS_IMPROVEMENT", reason="Missing evidence", version=2)
            )
            assert (
                await connection.scalar(
                    select(RunFeedback.version).where(RunFeedback.id == feedback_id)
                )
                == 2
            )

            for statement in (
                update(RunFeedback)
                .where(RunFeedback.id == feedback_id)
                .values(reason="Bypass version"),
                update(RunFeedback)
                .where(RunFeedback.id == feedback_id)
                .values(rating="HELPFUL", version=4),
                update(RunFeedback)
                .where(RunFeedback.id == feedback_id)
                .values(run_id=uuid4(), version=3),
                update(RunFeedback)
                .where(RunFeedback.id == feedback_id)
                .values(user_id=uuid4(), version=3),
                delete(RunFeedback).where(RunFeedback.id == feedback_id),
            ):
                savepoint = await connection.begin_nested()
                with pytest.raises(DBAPIError):
                    await connection.execute(statement)
                await savepoint.rollback()
        finally:
            await transaction.rollback()
            await engine.dispose()
