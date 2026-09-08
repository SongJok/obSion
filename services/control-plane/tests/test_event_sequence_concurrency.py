"""真实 SQLite 事务与旧 ORM identity map 的确定性回归。"""

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

from obsion.db.session import Database


@pytest.fixture
async def sequence_database(app_settings):
    database = Database(app_settings)
    try:
        yield database
    finally:
        await database.dispose()


async def test_sequence_does_not_reuse_stale_identity_or_discard_pending_state(sequence_database):
    await stale_identity(sequence_database)


@pytest.mark.parametrize("run_stream", [True, False])
@pytest.mark.parametrize("existing", [True, False])
async def test_concurrent_sequence_allocation_is_gapless(sequence_database, run_stream, existing):
    await concurrent_append(sequence_database, run_stream=run_stream, existing=existing)


async def test_rollback_reverts_event_outbox_and_counters(sequence_database):
    await rollback_allocation(sequence_database)


async def test_caught_envelope_failure_leaves_no_sequence_gap(sequence_database):
    await rejected_envelope_can_retry(sequence_database)


async def test_caller_savepoint_rollback_does_not_leave_cached_counter(sequence_database):
    await caller_savepoint_rollback(sequence_database)


async def test_persistence_failure_can_retry_without_partial_sequence(sequence_database):
    await persistence_failure_can_retry(sequence_database)


async def test_sequence_allocation_cannot_claim_foreign_aggregate(sequence_database):
    await reject_cross_organization(sequence_database)
