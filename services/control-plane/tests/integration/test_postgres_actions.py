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
from obsion.db.models import ActionPlan, ActionRequest, Organization, User, Workspace


@pytest.mark.asyncio
async def test_action_plan_rejects_mutation_and_delete() -> None:
    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL invariant tests are opt-in")

    engine = create_async_engine(get_settings().database_url)
    organization_id = uuid4()
    user_id = uuid4()
    workspace_id = uuid4()
    action_id = uuid4()
    plan_id = uuid4()
    now = datetime.now(UTC)
    spec = {
        "schema_version": 1,
        "action_type": "GENERATE_PR",
        "execute": {"capability_version_id": str(uuid4())},
        "rollback": {"capability_version_id": str(uuid4())},
    }
    checksum = hashlib.sha256(
        json.dumps(spec, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(
                insert(Organization).values(
                    id=organization_id,
                    slug=f"action-invariant-{organization_id}",
                    name="Action invariant",
                    active=True,
                    settings={},
                )
            )
            await connection.execute(
                insert(User).values(
                    id=user_id,
                    organization_id=organization_id,
                    external_id="action-invariant-owner",
                    email=f"{user_id}@example.invalid",
                    display_name="Action invariant owner",
                    active=True,
                    attributes={},
                )
            )
            await connection.execute(
                insert(Workspace).values(
                    id=workspace_id,
                    organization_id=organization_id,
                    name="Action invariant workspace",
                    description="",
                    owner_id=user_id,
                    classification="INTERNAL",
                    visibility="PRIVATE",
                )
            )
            await connection.execute(
                insert(ActionRequest).values(
                    id=action_id,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    action_type="GENERATE_PR",
                    title="Invariant action",
                    description="",
                    environment="development",
                    target={"repository": "obsion/test"},
                    parameters={"title": "test", "head": "test", "base": "main"},
                    rollback_parameters={},
                    status="WAITING_APPROVAL",
                    owner_id=user_id,
                    requested_by=user_id,
                    idempotency_key=f"action-invariant-{action_id}",
                    timeout_seconds=300,
                    plan_checksum_sha256=checksum,
                    preflight={"passed": True},
                    result={},
                )
            )
            await connection.execute(
                insert(ActionPlan).values(
                    id=plan_id,
                    organization_id=organization_id,
                    action_request_id=action_id,
                    spec=spec,
                    checksum_sha256=checksum,
                    created_by=user_id,
                    created_at=now,
                )
            )

            mutation = await connection.begin_nested()
            with pytest.raises(DBAPIError):
                await connection.execute(
                    update(ActionPlan)
                    .where(ActionPlan.id == plan_id)
                    .values(spec={"schema_version": 2})
                )
            await mutation.rollback()

            removal = await connection.begin_nested()
            with pytest.raises(DBAPIError):
                await connection.execute(delete(ActionPlan).where(ActionPlan.id == plan_id))
            await removal.rollback()

            persisted = await connection.scalar(
                select(ActionPlan.spec).where(ActionPlan.id == plan_id)
            )
            assert persisted == spec
        finally:
            await transaction.rollback()
    await engine.dispose()
