from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from obsion.config import get_settings

_PHASE1_TABLES = frozenset(
    {
        "artifacts",
        "audit_logs",
        "claims",
        "events",
        "evidence",
        "run_steps",
        "runs",
        "threads",
        "turns",
        "users",
        "workspaces",
    }
)


async def test_phase1_canonical_tables_are_migrated_with_primary_keys() -> None:
    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL invariant tests are opt-in")

    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            migrated_tables = set(
                await connection.scalars(
                    text(
                        """
                        SELECT c.relname
                        FROM pg_class AS c
                        JOIN pg_namespace AS n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'public' AND c.relkind = 'r'
                        """
                    )
                )
            )
            primary_key_tables = set(
                await connection.scalars(
                    text(
                        """
                        SELECT DISTINCT c.relname
                        FROM pg_class AS c
                        JOIN pg_namespace AS n ON n.oid = c.relnamespace
                        JOIN pg_constraint AS con
                          ON con.conrelid = c.oid
                         AND con.contype = 'p'
                        WHERE n.nspname = 'public' AND c.relkind = 'r'
                        """
                    )
                )
            )
    finally:
        await engine.dispose()

    assert migrated_tables >= _PHASE1_TABLES
    assert primary_key_tables >= _PHASE1_TABLES
    assert "audit_records" not in migrated_tables
