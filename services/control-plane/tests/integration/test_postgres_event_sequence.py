"""仅对显式选择、已迁移的一次性 PostgreSQL 验证事件序号。

并发场景会提交合成记录；不删除不可变事件，禁止连接业务数据库。
"""

import os

import pytest
from event_sequence_scenarios import (
    caller_savepoint_rollback,
    concurrent_append,
    persistence_failure_can_retry,
    reject_cross_organization,
    rejected_envelope_can_retry,
    rollback_allocation,
    stale_identity,
)

from obsion.config import Environment, Settings
from obsion.db.session import Database


@pytest.fixture
async def postgres_sequence_database():
    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL invariant tests are opt-in")
    settings = Settings(_env_file=None, environment=Environment.TEST)
    assert settings.database_url.startswith("postgresql"), "Disposable PostgreSQL required"
    database = Database(settings)
    try:
        yield database
    finally:
        await database.dispose()


async def test_postgres_sequence_preserves_pending_state_and_refreshes_counter(
    postgres_sequence_database,
):
    await stale_identity(postgres_sequence_database)


@pytest.mark.parametrize("run_stream", [True, False])
@pytest.mark.parametrize("existing", [True, False])
async def test_postgres_concurrent_sequence_allocation_is_gapless(
    postgres_sequence_database, run_stream, existing
):
    await concurrent_append(postgres_sequence_database, run_stream=run_stream, existing=existing)


async def test_postgres_rollback_reverts_event_outbox_and_counters(postgres_sequence_database):
    await rollback_allocation(postgres_sequence_database)


async def test_postgres_caught_envelope_failure_leaves_no_sequence_gap(postgres_sequence_database):
    await rejected_envelope_can_retry(postgres_sequence_database)


async def test_postgres_caller_savepoint_rollback_restores_counter(postgres_sequence_database):
    await caller_savepoint_rollback(postgres_sequence_database)


async def test_postgres_persistence_failure_can_retry(postgres_sequence_database):
    await persistence_failure_can_retry(postgres_sequence_database)


async def test_postgres_sequence_rejects_foreign_aggregate(postgres_sequence_database):
    await reject_cross_organization(postgres_sequence_database)
