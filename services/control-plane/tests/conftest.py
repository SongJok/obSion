import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from obsion.config import Environment, Settings
from obsion.db.base import Base
from obsion.db.session import Database
from obsion.main import create_app

# Repository-local developer settings must never influence any test, including
# tests that construct Settings directly instead of using app_settings.
Settings.model_config["env_file"] = None

TEST_BEARER_TOKEN = "obsion-phase2-test-bearer-token"  # noqa: S105


async def _create_schema(settings: Settings) -> None:
    database = Database(settings)
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await database.dispose()


@pytest.fixture
def app_settings(tmp_path: Path) -> Settings:
    settings = Settings(
        _env_file=None,
        environment=Environment.TEST,
        release_revision=None,
        release_image_digest=None,
        # Synthetic index fixtures deliberately opt in; dedicated live tests assert no fallback.
        enterprise_knowledge_mode="indexed-development",
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'obsion-test.db'}",
        allowed_origins=["http://testserver"],
        dev_bearer_token=TEST_BEARER_TOKEN,
        model_allowed_ids=None,
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
