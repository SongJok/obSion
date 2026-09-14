"""Real PostgreSQL source receipts and Run/worker concurrency; disposable only."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from postgres_isolation import isolated_postgres_database
from sqlalchemy import inspect, text

from obsion.config import Settings
from obsion.db.models import KnowledgeSyncSource
from obsion.main import create_app


@pytest.fixture
def live_client():
    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("Live knowledge requires disposable PostgreSQL opt-in")
    with isolated_postgres_database(os.environ["OBSION_DATABASE_URL"]) as url:
        settings = Settings(
            _env_file=None,
            environment="test",
            database_url=url,
            dev_bearer_token="live-knowledge-test",
            allowed_origins=["http://testserver"],
        )
        with TestClient(
            create_app(settings), headers={"Authorization": "Bearer live-knowledge-test"}
        ) as client:
            yield client


def test_fresh_reads_and_unchanged_versions(live_client, monkeypatch):
    from test_live_knowledge import (
        test_each_question_requires_new_governed_reads_even_when_content_unchanged,
    )

    test_each_question_requires_new_governed_reads_even_when_content_unchanged(
        live_client, monkeypatch, False
    )


def test_run_transaction_does_not_block_source_worker(live_client, monkeypatch):
    from test_live_knowledge import assert_live_harness_can_wait_while_external_worker_commits

    assert_live_harness_can_wait_while_external_worker_commits(live_client, monkeypatch)


def test_receipt_migration_preserves_sources_and_never_backfills_freshness(live_client):
    from test_live_knowledge import (
        test_resume_preserves_actual_scan_start_and_legacy_scan_has_no_receipt,
    )

    test_resume_preserves_actual_scan_start_and_legacy_scan_has_no_receipt(live_client)

    async def identity():
        async with live_client.app.state.database.sessions() as session:
            row = (
                await session.execute(
                    text("SELECT id, generation, corp_id FROM knowledge_sync_sources")
                )
            ).one()
            return tuple(row)

    original = live_client.portal.call(identity)
    root = Path(__file__).resolve().parents[4]
    environment = {k: v for k, v in os.environ.items() if not k.startswith(("OBSION_", "PYTEST_"))}
    environment.update(
        {
            "OBSION_ENVIRONMENT": "test",
            "OBSION_DATABASE_URL": live_client.app.state.settings.database_url,
        }
    )

    def migrate(*arguments):
        with tempfile.TemporaryDirectory(prefix="obsion-live-migration-") as directory:
            result = subprocess.run(  # noqa: S603
                [
                    sys.executable,
                    "-m",
                    "alembic",
                    "-c",
                    str(root / "services/control-plane/alembic.ini"),
                    *arguments,
                ],
                cwd=directory,
                env=environment,
                capture_output=True,
                check=False,
            )
        assert result.returncode == 0, result.stderr.decode()

    migrate("downgrade", "b4c6d8e0f2a4")
    assert live_client.portal.call(identity) == original

    async def columns():
        async with live_client.app.state.database.engine.connect() as connection:
            return await connection.run_sync(
                lambda c: {col["name"] for col in inspect(c).get_columns("knowledge_sync_sources")}
            )

    assert "completed_generation" not in live_client.portal.call(columns)
    migrate("upgrade", "head")
    assert live_client.portal.call(identity) == original

    async def restored():
        async with live_client.app.state.database.sessions() as session:
            source = await session.get(KnowledgeSyncSource, original[0])
            assert source.scan_started_at is None
            assert source.completed_scan_started_at is None and source.completed_generation is None

    live_client.portal.call(restored)
    migrate("check")
