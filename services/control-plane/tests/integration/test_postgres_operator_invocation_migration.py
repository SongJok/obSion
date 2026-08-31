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
_PREVIOUS_REVISION = "f62c1a9e4d20"
_PHASE79_REVISION = "a79c4d2e8f10"


def test_operator_invocation_migration_upgrades_downgrades_and_reupgrades() -> None:
    if os.getenv("OBSION_RUN_PHASE79_MIGRATION_TEST") != "1":
        pytest.skip("destructive PostgreSQL Phase 79 migration test is opt-in")

    config = _alembic_config()
    command.upgrade(config, _PREVIOUS_REVISION)
    assert asyncio.run(_snapshot()) is None

    command.upgrade(config, _PHASE79_REVISION)
    first = asyncio.run(_snapshot())
    assert first is not None

    command.downgrade(config, _PREVIOUS_REVISION)
    assert asyncio.run(_snapshot()) is None

    command.upgrade(config, _PHASE79_REVISION)
    assert asyncio.run(_snapshot()) == first


def _alembic_config() -> Config:
    config = Config(str(_REPOSITORY_ROOT / "services/control-plane/alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
    return config


async def _snapshot() -> dict[str, Any] | None:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            table_exists = await connection.scalar(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.tables
                        WHERE table_schema = 'public'
                          AND table_name = 'operator_capability_invocations'
                    )
                    """
                )
            )
            if not table_exists:
                function_exists = await connection.scalar(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT 1 FROM pg_proc
                            WHERE proname =
                                'obsion_guard_operator_capability_invocation_mutation'
                        )
                        """
                    )
                )
                assert function_exists is False
                return None

            columns = list(
                (
                    await connection.execute(
                        text(
                            """
                            SELECT column_name, data_type, is_nullable
                            FROM information_schema.columns
                            WHERE table_schema = 'public'
                              AND table_name = 'operator_capability_invocations'
                            ORDER BY ordinal_position
                            """
                        )
                    )
                ).tuples()
            )
            constraints = list(
                (
                    await connection.execute(
                        text(
                            """
                            SELECT conname
                            FROM pg_constraint
                            WHERE conrelid = 'operator_capability_invocations'::regclass
                            ORDER BY conname
                            """
                        )
                    )
                ).scalars()
            )
            triggers = list(
                (
                    await connection.execute(
                        text(
                            """
                            SELECT tgname
                            FROM pg_trigger
                            WHERE tgrelid = 'operator_capability_invocations'::regclass
                              AND NOT tgisinternal
                            ORDER BY tgname
                            """
                        )
                    )
                ).scalars()
            )
            return {
                "columns": columns,
                "constraints": constraints,
                "triggers": triggers,
            }
    finally:
        await engine.dispose()
