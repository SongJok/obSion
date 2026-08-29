import os
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from obsion.config import get_settings
from obsion.db.models import (
    Organization,
    Run,
    RunConversationSnapshot,
    Thread,
    Turn,
    User,
    Workspace,
)


@pytest.mark.asyncio
async def test_run_conversation_snapshots_are_database_immutable() -> None:
    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL invariant tests are opt-in")

    engine = create_async_engine(get_settings().database_url)
    organization_id = uuid4()
    user_id = uuid4()
    workspace_id = uuid4()
    thread_id = uuid4()
    source_turn_id = uuid4()
    source_run_id = uuid4()
    current_turn_id = uuid4()
    current_run_id = uuid4()
    snapshot_id = uuid4()
    now = datetime.now(UTC)

    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(
                insert(Organization).values(
                    id=organization_id,
                    slug=f"conversation-invariant-{organization_id}",
                    name="Conversation context invariant",
                    active=True,
                    settings={},
                )
            )
            await connection.execute(
                insert(User).values(
                    id=user_id,
                    organization_id=organization_id,
                    external_id="conversation-invariant-owner",
                    email=f"{user_id}@example.invalid",
                    display_name="Conversation invariant owner",
                    active=True,
                    attributes={},
                )
            )
            await connection.execute(
                insert(Workspace).values(
                    id=workspace_id,
                    organization_id=organization_id,
                    name="Conversation invariant workspace",
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
                    title="Conversation invariant thread",
                    status="ACTIVE",
                    created_by=user_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            for ordinal, turn_id, text in (
                (1, source_turn_id, "Original investigation"),
                (2, current_turn_id, "Follow-up question"),
            ):
                await connection.execute(
                    insert(Turn).values(
                        id=turn_id,
                        organization_id=organization_id,
                        thread_id=thread_id,
                        ordinal=ordinal,
                        created_by=user_id,
                        input_text=text,
                        sanitized_input=text,
                        context_refs=[],
                        attachment_refs=[],
                        created_at=now,
                    )
                )
            for run_id, turn_id, status in (
                (source_run_id, source_turn_id, "COMPLETED"),
                (current_run_id, current_turn_id, "PENDING"),
            ):
                await connection.execute(
                    insert(Run).values(
                        id=run_id,
                        organization_id=organization_id,
                        turn_id=turn_id,
                        status=status,
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
                        completed_at=now if status == "COMPLETED" else None,
                        aggregate_version=0,
                        created_at=now,
                        updated_at=now,
                    )
                )
            await connection.execute(
                insert(RunConversationSnapshot).values(
                    id=snapshot_id,
                    organization_id=organization_id,
                    run_id=current_run_id,
                    source_thread_id=thread_id,
                    source_turn_id=source_turn_id,
                    source_run_id=source_run_id,
                    source_principal_id=user_id,
                    ordinal=1,
                    user_content="Original investigation",
                    assistant_content="Original governed answer",
                    content_fingerprint="a" * 64,
                    classification="INTERNAL",
                    captured_at=now,
                )
            )

            for statement in (
                update(RunConversationSnapshot)
                .where(RunConversationSnapshot.id == snapshot_id)
                .values(user_content="Rewritten history"),
                delete(RunConversationSnapshot).where(RunConversationSnapshot.id == snapshot_id),
            ):
                savepoint = await connection.begin_nested()
                with pytest.raises(DBAPIError):
                    await connection.execute(statement)
                await savepoint.rollback()
        finally:
            await transaction.rollback()
            await engine.dispose()
