import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from obsion.config import Environment, Settings
from obsion.db.base import Base
from obsion.db.session import Database
from obsion.main import create_app

TEST_BEARER_TOKEN = "obsion-phase2-test-bearer-token"  # noqa: S105


async def _create_schema(settings: Settings) -> None:
    database = Database(settings)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await database.dispose()


@pytest.fixture
def app_settings(tmp_path: Path) -> Settings:
    settings = Settings(
        environment=Environment.TEST,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'obsion-test.db'}",
        allowed_origins=["http://testserver"],
        dev_bearer_token=TEST_BEARER_TOKEN,
        run_worker_concurrency=2,
        event_stream_heartbeat_seconds=5,
    )
    asyncio.run(_create_schema(settings))
    return settings


@pytest.fixture
def client(app_settings: Settings) -> Iterator[TestClient]:
    with TestClient(
        create_app(app_settings),
        headers={"Authorization": f"Bearer {TEST_BEARER_TOKEN}"},
    ) as test_client:
        yield test_client
