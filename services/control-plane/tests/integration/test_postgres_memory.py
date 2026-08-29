import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from obsion.config import get_settings
from obsion.db.models import (
    Memory,
    Organization,
    PolicyDecision,
    Run,
    RunMemorySnapshot,
    Thread,
    Turn,
    User,
    Workspace,
)


@pytest.mark.asyncio
async def test_memory_lineage_and_run_snapshots_are_database_immutable() -> None:
    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL invariant tests are opt-in")

    engine = create_async_engine(get_settings().database_url)
    organization_id = uuid4()
    user_id = uuid4()
    workspace_id = uuid4()
    thread_id = uuid4()
    turn_id = uuid4()
    run_id = uuid4()
    decision_id = uuid4()
    memory_id = uuid4()
    snapshot_id = uuid4()
    now = datetime.now(UTC)
    content = {"preference": "Use UTC"}
    fingerprint = hashlib.sha256(
        json.dumps(content, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(
                insert(Organization).values(
                    id=organization_id,
                    slug=f"memory-invariant-{organization_id}",
                    name="Memory invariant",
                    active=True,
                    settings={},
                )
            )
            await connection.execute(
                insert(User).values(
                    id=user_id,
                    organization_id=organization_id,
                    external_id="memory-invariant-owner",
                    email=f"{user_id}@example.invalid",
                    display_name="Memory invariant owner",
                    active=True,
                    attributes={},
                )
            )
            await connection.execute(
                insert(Workspace).values(
                    id=workspace_id,
                    organization_id=organization_id,
                    name="Memory invariant workspace",
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
                    title="Memory invariant thread",
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
                    input_text="Use governed memory",
                    sanitized_input="Use governed memory",
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
                insert(PolicyDecision).values(
                    id=decision_id,
                    organization_id=organization_id,
                    principal_id=user_id,
                    action="memory.write",
                    resource={"scope": "WORKSPACE"},
                    context={},
                    risk_level="L1",
                    effect="ALLOW",
                    matched_policy_ids=[],
                    obligations=[],
                    reason_codes=["principal_permission"],
                    input_fingerprint="a" * 64,
                    created_at=now,
                )
            )
            await connection.execute(
                insert(Memory).values(
                    id=memory_id,
                    organization_id=organization_id,
                    scope="WORKSPACE",
                    owner_ref=str(workspace_id),
                    content=content,
                    dedupe_key=fingerprint,
                    sensitivity="INTERNAL",
                    status="APPROVED",
                    policy_decision_id=decision_id,
                    expires_at=now + timedelta(days=30),
                    created_at=now,
                    updated_at=now,
                )
            )
            await connection.execute(
                insert(RunMemorySnapshot).values(
                    id=snapshot_id,
                    organization_id=organization_id,
                    run_id=run_id,
                    memory_id=memory_id,
                    principal_id=user_id,
                    ordinal=1,
                    scope="WORKSPACE",
                    owner_ref=str(workspace_id),
                    content=content,
                    content_fingerprint=fingerprint,
                    sensitivity="INTERNAL",
                    policy_decision_id=decision_id,
                    memory_updated_at=now,
                    captured_at=now,
                )
            )

            await connection.execute(
                update(Memory).where(Memory.id == memory_id).values(status="EXPIRED")
            )
            assert (
                await connection.scalar(select(Memory.status).where(Memory.id == memory_id))
                == "EXPIRED"
            )

            for statement in (
                update(Memory).where(Memory.id == memory_id).values(status="CANDIDATE"),
                update(Memory).where(Memory.id == memory_id).values(content={"changed": True}),
                delete(Memory).where(Memory.id == memory_id),
                update(RunMemorySnapshot)
                .where(RunMemorySnapshot.id == snapshot_id)
                .values(content={"changed": True}),
                delete(RunMemorySnapshot).where(RunMemorySnapshot.id == snapshot_id),
            ):
                savepoint = await connection.begin_nested()
                with pytest.raises(DBAPIError):
                    await connection.execute(statement)
                await savepoint.rollback()
        finally:
            await transaction.rollback()
            await engine.dispose()
