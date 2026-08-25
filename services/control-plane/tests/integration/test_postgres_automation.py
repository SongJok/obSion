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
    WorkflowDefinition,
    WorkflowVersion,
    Workspace,
)


@pytest.mark.asyncio
async def test_workflow_version_allows_publish_but_rejects_mutation_and_delete() -> None:
    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL invariant tests are opt-in")

    engine = create_async_engine(get_settings().database_url)
    organization_id = uuid4()
    user_id = uuid4()
    workspace_id = uuid4()
    workflow_id = uuid4()
    version_id = uuid4()
    now = datetime.now(UTC)
    spec = {"steps": [{"id": "notify", "type": "NOTIFICATION"}]}
    checksum = hashlib.sha256(
        json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(
                insert(Organization).values(
                    id=organization_id,
                    slug=f"automation-invariant-{organization_id}",
                    name="Automation invariant",
                    active=True,
                    settings={},
                )
            )
            await connection.execute(
                insert(User).values(
                    id=user_id,
                    organization_id=organization_id,
                    external_id="invariant-owner",
                    email=f"{user_id}@example.invalid",
                    display_name="Invariant owner",
                    active=True,
                    attributes={},
                )
            )
            await connection.execute(
                insert(Workspace).values(
                    id=workspace_id,
                    organization_id=organization_id,
                    name="Invariant workspace",
                    description="",
                    owner_id=user_id,
                    classification="INTERNAL",
                    visibility="PRIVATE",
                )
            )
            await connection.execute(
                insert(WorkflowDefinition).values(
                    id=workflow_id,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    name="invariant-workflow",
                    display_name="Invariant workflow",
                    description="",
                    status="DRAFT",
                    owner_id=user_id,
                    concurrency_policy="FORBID",
                    max_concurrency=1,
                    timeout_seconds=300,
                    notify_on_success=False,
                    notify_on_failure=True,
                    classification="INTERNAL",
                )
            )
            await connection.execute(
                insert(WorkflowVersion).values(
                    id=version_id,
                    organization_id=organization_id,
                    workflow_id=workflow_id,
                    version=1,
                    spec=spec,
                    checksum_sha256=checksum,
                    created_by=user_id,
                    created_at=now,
                )
            )

            await connection.execute(
                update(WorkflowVersion)
                .where(WorkflowVersion.id == version_id)
                .values(published_at=now)
            )
            published = await connection.scalar(
                select(WorkflowVersion.published_at).where(WorkflowVersion.id == version_id)
            )
            assert published is not None

            mutation = await connection.begin_nested()
            with pytest.raises(DBAPIError):
                await connection.execute(
                    update(WorkflowVersion)
                    .where(WorkflowVersion.id == version_id)
                    .values(spec={"steps": []})
                )
            await mutation.rollback()

            removal = await connection.begin_nested()
            with pytest.raises(DBAPIError):
                await connection.execute(
                    delete(WorkflowVersion).where(WorkflowVersion.id == version_id)
                )
            await removal.rollback()

            persisted = await connection.scalar(
                select(WorkflowVersion.spec).where(WorkflowVersion.id == version_id)
            )
            assert persisted == spec
        finally:
            await transaction.rollback()
    await engine.dispose()
