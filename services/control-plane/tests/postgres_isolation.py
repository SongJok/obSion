"""Dedicated migrated databases for tests that exercise a global work queue.

Only opt-in disposable PostgreSQL tests call this helper. The supplied database
is used as the administrative connection and is never dropped or truncated.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool


@contextmanager
def isolated_postgres_database(administration_url: str) -> Iterator[str]:
    if os.environ.get("OBSION_RUN_POSTGRES_TESTS") != "1":
        raise RuntimeError("Disposable PostgreSQL tests require explicit opt-in")
    name = "obsion_test_" + uuid4().hex
    url = make_url(administration_url)
    if url.drivername != "postgresql+asyncpg":
        raise ValueError("Disposable PostgreSQL tests require the asyncpg driver")
    target = url.set(database=name).render_as_string(hide_password=False)

    async def administer(statement: str) -> None:
        engine = create_async_engine(url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
        try:
            async with engine.connect() as connection:
                await connection.execute(text(statement))
        finally:
            await engine.dispose()

    # SQL identifiers are generated UUID hex, never derived from a caller value.
    asyncio.run(administer(f'CREATE DATABASE "{name}"'))
    try:
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith(("OBSION_", "PYTEST_"))
        }
        environment.update({"OBSION_ENVIRONMENT": "test", "OBSION_DATABASE_URL": target})
        configuration = Path(__file__).resolve().parents[1] / "alembic.ini"
        with tempfile.TemporaryDirectory(prefix="obsion-isolated-pg-") as directory:
            migrated = subprocess.run(  # noqa: S603
                [sys.executable, "-m", "alembic", "-c", str(configuration), "upgrade", "head"],
                cwd=directory,
                env=environment,
                capture_output=True,
                check=False,
            )
        if migrated.returncode:
            raise RuntimeError("Isolated PostgreSQL database migration failed")
        yield target
    finally:
        asyncio.run(administer(f'DROP DATABASE "{name}" WITH (FORCE)'))
