"""Opt-in destructive round trip; CI provisions an isolated database."""

import asyncio
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from obsion.config import get_settings


async def _legacy_binding(binding_id: UUID, *, create: bool, scoped: bool = False) -> tuple:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.begin() as connection:
            if create:
                await connection.execute(
                    text(
                        "INSERT INTO organizations (id, slug, name, active, settings) "
                        "VALUES (:id, :slug, 'Migration fixture', true, '{}')"
                    ),
                    {"id": binding_id, "slug": str(binding_id)},
                )
                await connection.execute(
                    text(
                        "INSERT INTO users (id, organization_id, external_id, email, display_name, "
                        "active, attributes) VALUES (:id, :id, :external, :email, "
                        "'Migration fixture', "
                        "true, '{}')"
                    ),
                    {
                        "id": binding_id,
                        "external": str(binding_id),
                        "email": f"{binding_id}@example.invalid",
                    },
                )
                await connection.execute(
                    text(
                        "INSERT INTO im_principal_bindings "
                        "(id, organization_id, channel, sender_id, user_id, created_by, active) "
                        "VALUES (:id, :id, 'dingtalk', 'legacy-sender', :id, :id, true)"
                    ),
                    {"id": binding_id},
                )
            query = (
                "SELECT id, user_id, active, installation_id "
                "FROM im_principal_bindings WHERE id = :id"
                if scoped
                else "SELECT id, user_id, active FROM im_principal_bindings WHERE id = :id"
            )
            result = await connection.execute(
                text(query),
                {"id": binding_id},
            )
            return tuple(result.one())
    finally:
        await engine.dispose()


def test_im_inbox_migration_round_trip() -> None:
    if os.getenv("OBSION_RUN_IM_INBOX_MIGRATION_TEST") != "1":
        pytest.skip("destructive PostgreSQL IM Inbox migration test is opt-in")
    root = Path(__file__).parents[4]
    config = Config(str(root / "services/control-plane/alembic.ini"))
    command.upgrade(config, "a88f69d7c8a0")
    binding_id = uuid4()
    original = asyncio.run(_legacy_binding(binding_id, create=True))
    command.upgrade(config, "c9d1e4f6a2b3")
    assert asyncio.run(_legacy_binding(binding_id, create=False, scoped=True)) == (*original, None)
    command.downgrade(config, "a88f69d7c8a0")
    assert asyncio.run(_legacy_binding(binding_id, create=False)) == original
    command.upgrade(config, "head")
    assert asyncio.run(_legacy_binding(binding_id, create=False, scoped=True)) == (*original, None)
    command.check(config)
