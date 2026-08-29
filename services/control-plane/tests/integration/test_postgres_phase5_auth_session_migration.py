import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from obsion.config import get_settings

_REPOSITORY_ROOT = Path(__file__).parents[4]
_PHASE2_REVISION = "8d3f2a1c7b90"
_PHASE5_REVISION = "19c6b2e4a7d1"


def test_phase5_auth_session_migration_round_trip() -> None:
    if os.getenv("OBSION_RUN_PHASE5_MIGRATION_TEST") != "1":
        pytest.skip("destructive PostgreSQL Phase 5 migration test is opt-in")

    config = _alembic_config()
    command.downgrade(config, "base")
    command.upgrade(config, _PHASE2_REVISION)
    assert asyncio.run(_table_exists()) is False

    command.upgrade(config, _PHASE5_REVISION)
    first = asyncio.run(_session_schema())
    command.downgrade(config, _PHASE2_REVISION)
    assert asyncio.run(_table_exists()) is False
    command.upgrade(config, _PHASE5_REVISION)
    assert asyncio.run(_session_schema()) == first


def _alembic_config() -> Config:
    config = Config(str(_REPOSITORY_ROOT / "services/control-plane/alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
    return config


async def _table_exists() -> bool:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            return bool(await connection.scalar(text("SELECT to_regclass('public.auth_sessions')")))
    finally:
        await engine.dispose()


async def _session_schema() -> dict[str, list[str]]:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            constraints = sorted(
                await connection.scalars(
                    text(
                        """
                        SELECT conname
                        FROM pg_constraint
                        WHERE conrelid = 'auth_sessions'::regclass
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
                        WHERE schemaname = 'public' AND tablename = 'auth_sessions'
                        """
                    )
                )
            )
    finally:
        await engine.dispose()

    expected_constraints = {
        "ck_auth_sessions_valid_auth_session_digest",
        "fk_auth_sessions_org_user",
        "fk_auth_sessions_organization_id_organizations",
        "pk_auth_sessions",
        "uq_auth_sessions_token_digest",
    }
    expected_indexes = {
        "ix_auth_sessions_expires_at",
        "ix_auth_sessions_organization_id",
        "ix_auth_sessions_revoked_at",
        "ix_auth_sessions_user_id",
    }
    assert set(constraints) >= expected_constraints
    assert set(indexes) >= expected_indexes
    return {
        "constraints": constraints,
        "indexes": indexes,
    }
