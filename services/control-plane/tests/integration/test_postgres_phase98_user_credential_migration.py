from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from obsion.config import get_settings

_REPOSITORY_ROOT = Path(__file__).parents[4]
_PREVIOUS_REVISION = "b88f1c4d5e60"
_PHASE98_REVISION = "d7f31a9c4b28"


def test_user_credential_migration_upgrades_downgrades_and_reupgrades() -> None:
    if os.getenv("OBSION_RUN_PHASE98_MIGRATION_TEST") != "1":
        pytest.skip("destructive PostgreSQL Phase 98 migration test is opt-in")

    config = _alembic_config()
    command.upgrade(config, _PREVIOUS_REVISION)
    assert asyncio.run(_snapshot()) is None

    command.upgrade(config, _PHASE98_REVISION)
    first = asyncio.run(_snapshot())
    assert first is not None

    command.downgrade(config, _PREVIOUS_REVISION)
    assert asyncio.run(_snapshot()) is None

    command.upgrade(config, _PHASE98_REVISION)
    assert asyncio.run(_snapshot()) == first


def _alembic_config() -> Config:
    config = Config(str(_REPOSITORY_ROOT / "services/control-plane/alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
    return config


async def _snapshot() -> dict[str, Any] | None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            exists = await connection.scalar(
                text("SELECT to_regclass('public.user_credentials') IS NOT NULL")
            )
            if not exists:
                return None
            columns = list(
                (
                    await connection.execute(
                        text(
                            """
                            SELECT column_name, data_type, is_nullable
                            FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'user_credentials'
                            ORDER BY ordinal_position
                            """
                        )
                    )
                ).tuples()
            )
            constraints = sorted(
                await connection.scalars(
                    text(
                        """
                        SELECT conname
                        FROM pg_constraint
                        WHERE conrelid = 'user_credentials'::regclass
                        """
                    )
                )
            )
            indexes = sorted(
                await connection.scalars(
                    text(
                        """
                        SELECT indexname
                        FROM pg_indexes
                        WHERE schemaname = 'public'
                          AND tablename = 'user_credentials'
                        """
                    )
                )
            )
    finally:
        await engine.dispose()

    assert set(constraints) >= {
        "ck_user_credentials_nonempty_encoded_secret",
        "ck_user_credentials_nonnegative_failed_attempts",
        "fk_user_credentials_org_user",
        "fk_user_credentials_organization_id_organizations",
        "pk_user_credentials",
        "uq_user_credentials_org_user",
    }
    assert set(indexes) >= {
        "ix_user_credentials_organization_id",
        "ix_user_credentials_user_id",
    }
    return {"columns": columns, "constraints": constraints, "indexes": indexes}
