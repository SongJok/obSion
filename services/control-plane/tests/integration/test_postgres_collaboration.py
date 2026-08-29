import hashlib
import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from obsion.config import get_settings
from obsion.db.models import (
    Organization,
    User,
    Workspace,
    WorkspaceDecision,
    WorkspaceDecisionVersion,
    WorkspaceTask,
)


@pytest.mark.asyncio
async def test_workspace_tasks_and_decisions_enforce_database_governance() -> None:
    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL invariant tests are opt-in")

    engine = create_async_engine(get_settings().database_url)
    organization_id = uuid4()
    user_id = uuid4()
    workspace_id = uuid4()
    task_id = uuid4()
    decision_id = uuid4()
    decision_version_id = uuid4()
    now = datetime.now(UTC)
    content = {
        "title": "Use immutable evidence",
        "summary": "Preserve governed history.",
        "rationale": "Replay requires stable inputs.",
        "alternatives": ["Mutable evidence"],
    }
    checksum = hashlib.sha256(
        json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(
                insert(Organization).values(
                    id=organization_id,
                    slug=f"collaboration-invariant-{organization_id}",
                    name="Collaboration invariant",
                    active=True,
                    settings={},
                )
            )
            await connection.execute(
                insert(User).values(
                    id=user_id,
                    organization_id=organization_id,
                    external_id="collaboration-invariant-owner",
                    email=f"{user_id}@example.invalid",
                    display_name="Collaboration invariant owner",
                    active=True,
                    attributes={},
                )
            )
            await connection.execute(
                insert(Workspace).values(
                    id=workspace_id,
                    organization_id=organization_id,
                    name="Collaboration invariant workspace",
                    description="",
                    owner_id=user_id,
                    classification="INTERNAL",
                    visibility="PRIVATE",
                    created_at=now,
                    updated_at=now,
                )
            )
            await connection.execute(
                insert(WorkspaceTask).values(
                    id=task_id,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    title="Verify impact",
                    description="",
                    status="OPEN",
                    priority="HIGH",
                    created_by=user_id,
                    version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            await connection.execute(
                update(WorkspaceTask)
                .where(WorkspaceTask.id == task_id)
                .values(status="IN_PROGRESS", version=2)
            )
            assert (
                await connection.scalar(
                    select(WorkspaceTask.version).where(WorkspaceTask.id == task_id)
                )
                == 2
            )

            for statement in (
                update(WorkspaceTask)
                .where(WorkspaceTask.id == task_id)
                .values(title="Bypass version"),
                update(WorkspaceTask)
                .where(WorkspaceTask.id == task_id)
                .values(status="COMPLETED", completed_at=now, version=4),
                update(WorkspaceTask)
                .where(WorkspaceTask.id == task_id)
                .values(workspace_id=uuid4(), version=3),
                delete(WorkspaceTask).where(WorkspaceTask.id == task_id),
            ):
                savepoint = await connection.begin_nested()
                with pytest.raises(DBAPIError):
                    await connection.execute(statement)
                await savepoint.rollback()

            await connection.execute(
                insert(WorkspaceDecision).values(
                    id=decision_id,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    status="PROPOSED",
                    current_version=1,
                    created_by=user_id,
                    created_at=now,
                    updated_at=now,
                )
            )
            await connection.execute(
                insert(WorkspaceDecisionVersion).values(
                    id=decision_version_id,
                    organization_id=organization_id,
                    decision_id=decision_id,
                    version=1,
                    title=content["title"],
                    summary=content["summary"],
                    rationale=content["rationale"],
                    alternatives=content["alternatives"],
                    created_by=user_id,
                    checksum_sha256=checksum,
                    created_at=now,
                )
            )
            await connection.execute(
                update(WorkspaceDecision)
                .where(WorkspaceDecision.id == decision_id)
                .values(status="ACCEPTED", decided_by=user_id, decided_at=now)
            )
            assert (
                await connection.scalar(
                    select(WorkspaceDecision.status).where(WorkspaceDecision.id == decision_id)
                )
                == "ACCEPTED"
            )

            for statement in (
                update(WorkspaceDecision)
                .where(WorkspaceDecision.id == decision_id)
                .values(current_version=2),
                update(WorkspaceDecision)
                .where(WorkspaceDecision.id == decision_id)
                .values(status="REJECTED"),
                delete(WorkspaceDecision).where(WorkspaceDecision.id == decision_id),
                update(WorkspaceDecisionVersion)
                .where(WorkspaceDecisionVersion.id == decision_version_id)
                .values(summary="Rewritten history"),
                delete(WorkspaceDecisionVersion).where(
                    WorkspaceDecisionVersion.id == decision_version_id
                ),
            ):
                savepoint = await connection.begin_nested()
                with pytest.raises(DBAPIError):
                    await connection.execute(statement)
                await savepoint.rollback()
        finally:
            await transaction.rollback()
            await engine.dispose()
