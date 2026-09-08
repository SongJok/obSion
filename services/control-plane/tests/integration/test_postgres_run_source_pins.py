"""Explicitly opted-in, disposable PostgreSQL only; commits synthetic records."""

import asyncio
import os

import pytest
from sqlalchemy import func, select, text, update
from test_run_source_pins import _fact_counts, _pin, _setup

from obsion.common.errors import AuthorizationError
from obsion.config import Environment, Settings
from obsion.db.models import Connector, Role
from obsion.db.project_source_models import ProjectSourceRevocation
from obsion.db.session import Database


@pytest.fixture
async def pin_database():
    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL source pin concurrency requires explicit opt-in")
    settings = Settings(_env_file=None, environment=Environment.TEST)
    assert settings.database_url.startswith("postgresql+asyncpg://")
    database = Database(settings)
    try:
        yield database
    finally:
        await database.dispose()


async def test_concurrent_pin_has_one_event_audit_and_version(pin_database):
    async with pin_database.sessions() as session, session.begin():
        item = await _setup(session)

    async def invoke():
        async with pin_database.sessions() as session, session.begin():
            result = await _pin(session, item)
            return result.outcome, result.pin.id

    results = await asyncio.wait_for(asyncio.gather(*(invoke() for _ in range(12))), 30)
    assert [outcome for outcome, _ in results].count("CREATED") == 1
    assert [outcome for outcome, _ in results].count("UNCHANGED") == 11
    assert len({pin_id for _, pin_id in results}) == 1
    async with pin_database.sessions() as session:
        # Every replay reauthorizes, but only the first creates a version/event/audit.
        assert await _fact_counts(session, item.organization.id) == [1, 1, 12, 1]


@pytest.mark.parametrize("revocation", ["source", "role"])
async def test_waiting_pin_rechecks_revocation_after_connector_lock(pin_database, revocation):
    async with pin_database.sessions() as session, session.begin():
        item = await _setup(session)
    started = asyncio.Event()
    worker_pid = None

    async def invoke():
        nonlocal worker_pid
        async with pin_database.sessions() as session, session.begin():
            worker_pid = await session.scalar(select(func.pg_backend_pid()))
            started.set()
            with pytest.raises(AuthorizationError):
                await _pin(session, item)

    task = None
    try:
        async with pin_database.sessions() as revoker, revoker.begin():
            await revoker.scalar(
                select(Connector.id).where(Connector.id == item.connector.id).with_for_update()
            )
            if revocation == "source":
                revoker.add(
                    ProjectSourceRevocation(
                        organization_id=item.organization.id,
                        source_id=item.source.id,
                        revoked_by=item.principal.id,
                        reason_code="SECURITY_REVOKED",
                    )
                )
            else:
                await revoker.execute(
                    update(Role).where(Role.id == item.role.id).values(permissions=[])
                )
            await revoker.flush()
            task = asyncio.create_task(invoke())
            await asyncio.wait_for(started.wait(), 5)
            # Observe an actual database lock wait rather than guessing by sleep.
            async with asyncio.timeout(5):
                while not await revoker.scalar(
                    text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": worker_pid}
                ):
                    assert not task.done(), "Pin bypassed the source management lock"
                    await asyncio.sleep(0.01)
        await asyncio.wait_for(task, 10)
        async with pin_database.sessions() as session:
            assert await _fact_counts(session, item.organization.id) == [0, 0, 0, 0]
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
