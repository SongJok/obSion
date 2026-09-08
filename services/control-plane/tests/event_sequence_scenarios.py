"""合成事件序号场景，供 SQLite 与一次性 PostgreSQL 共用。"""

import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError

from obsion.common.errors import ConflictError, ValidationError
from obsion.common.time import utc_now
from obsion.db.models import (
    AggregateHead,
    Event,
    Organization,
    OutboxMessage,
    Run,
    Thread,
    Turn,
    User,
    Workspace,
)
from obsion.domain.enums import ActorType, RunStatus
from obsion.persistence.events import EventDraft, EventStore


async def seed(database):
    organization_id, user_id, workspace_id, thread_id, turn_id, run_id = [uuid4() for _ in range(6)]
    async with database.sessions() as session, session.begin():
        session.add(
            Organization(id=organization_id, slug=f"sequence-{organization_id}", name="序号测试")
        )
        await session.flush()
        session.add(
            User(
                id=user_id,
                organization_id=organization_id,
                external_id=str(user_id),
                email=f"{user_id}@example.invalid",
                display_name="合成主体",
            )
        )
        await session.flush()
        session.add(
            Workspace(
                id=workspace_id, organization_id=organization_id, owner_id=user_id, name="序号测试"
            )
        )
        await session.flush()
        session.add(
            Thread(
                id=thread_id,
                organization_id=organization_id,
                workspace_id=workspace_id,
                created_by=user_id,
                title="序号测试",
            )
        )
        await session.flush()
        session.add(
            Turn(
                id=turn_id,
                organization_id=organization_id,
                thread_id=thread_id,
                ordinal=1,
                created_by=user_id,
                input_text="测试",
                sanitized_input="测试",
                created_at=utc_now(),
            )
        )
        await session.flush()
        session.add(Run(id=run_id, organization_id=organization_id, turn_id=turn_id))
    return EventDraft(
        name="run.started",
        aggregate_type="run",
        aggregate_id=run_id,
        organization_id=organization_id,
        correlation_id=run_id,
        actor_type=ActorType.SYSTEM,
        actor_id=None,
        run_id=run_id,
        payload={"worker": "synthetic"},
    )


async def stale_identity(database):
    draft = await seed(database)
    store = EventStore()
    async with database.sessions() as session, session.begin():
        await store.append(session, draft)
    async with database.sessions() as stale:
        run = await stale.get(Run, draft.run_id)
        head = await stale.get(AggregateHead, (draft.aggregate_type, draft.aggregate_id))
        assert run.aggregate_version == head.sequence == 1
        await stale.commit()
        async with database.sessions() as fresh, fresh.begin():
            await store.append(fresh, draft)
        assert run.aggregate_version == head.sequence == 1
        async with stale.begin():
            run.status = RunStatus.RUNNING
            run.plan = {"preserve": "pending-local-change"}
            result = await store.append(stale, draft)
            assert result.run_sequence == result.sequence == 3
            assert run.aggregate_version == head.sequence == 3
            assert run.plan == {"preserve": "pending-local-change"}
    async with database.sessions() as session:
        run = await session.get(Run, draft.run_id)
        assert run.status == RunStatus.RUNNING
        assert run.plan == {"preserve": "pending-local-change"}
        assert run.aggregate_version == 3


async def concurrent_append(database, *, run_stream, existing):
    draft = await seed(database)
    if not run_stream:
        draft = replace(
            draft,
            name="capability.requested",
            run_id=None,
            aggregate_type="test",
            aggregate_id=uuid4(),
            payload={"capability": "test.read", "version": 1, "resource": {}},
        )
    store = EventStore()
    if existing:
        async with database.sessions() as session, session.begin():
            await store.append(session, draft)
    ready = asyncio.Event()

    async def append_one():
        await ready.wait()
        async with database.sessions() as session, session.begin():
            return (await store.append(session, draft)).sequence

    tasks = [asyncio.create_task(append_one()) for _ in range(12)]
    ready.set()
    try:
        sequences = await asyncio.wait_for(asyncio.gather(*tasks), timeout=30)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    offset = int(existing)
    assert sorted(sequences) == list(range(1 + offset, 13 + offset))
    async with database.sessions() as session:
        events = list(
            await session.scalars(
                select(Event)
                .where(Event.aggregate_id == draft.aggregate_id)
                .order_by(Event.sequence)
            )
        )
        outbox = list(
            await session.scalars(
                select(OutboxMessage)
                .join(Event, Event.id == OutboxMessage.event_id)
                .where(Event.aggregate_id == draft.aggregate_id)
            )
        )
        assert len(events) == len(outbox) == 12 + offset
        assert {item.event_id for item in outbox} == {item.id for item in events}
        if run_stream:
            assert [item.run_sequence for item in events] == list(range(1, 13 + offset))
        else:
            assert all(item.run_sequence is None for item in events)


async def rollback_allocation(database):
    draft = await seed(database)
    store = EventStore()
    async with database.sessions() as session:
        await session.get(Run, draft.run_id)
        first = await store.append(session, draft)
        assert first.run_sequence == first.sequence == 1
        first_id = first.id
        await session.rollback()
    async with database.sessions() as session, session.begin():
        assert (await session.get(Run, draft.run_id)).aggregate_version == 0
        assert await session.get(AggregateHead, (draft.aggregate_type, draft.aggregate_id)) is None
        assert (
            await session.scalar(
                select(func.count()).select_from(Event).where(Event.run_id == draft.run_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(OutboxMessage)
                .where(OutboxMessage.event_id == first_id)
            )
            == 0
        )
        event = await store.append(session, draft)
        assert event.sequence == event.run_sequence == 1


async def rejected_envelope_can_retry(database):
    draft = await seed(database)
    store = EventStore()
    async with database.sessions() as session, session.begin():
        await store.append(session, draft)
        run = await session.get(Run, draft.run_id)
        head = await session.get(AggregateHead, (draft.aggregate_type, draft.aggregate_id))
        with pytest.MonkeyPatch.context() as patch:

            def reject(*args, **kwargs):
                raise ValidationError("event_envelope_schema_invalid", "合成拒绝")

            patch.setattr("obsion.persistence.events.validate_event_envelope", reject)
            with pytest.raises(ValidationError):
                await store.append(session, draft)
        assert run.aggregate_version == head.sequence == 1
        assert not session.new and not session.dirty
        second = await store.append(session, draft)
        assert second.sequence == second.run_sequence == 2
        assert run.aggregate_version == head.sequence == 2
    async with database.sessions() as session:
        events = list(await session.scalars(select(Event).where(Event.run_id == draft.run_id)))
        assert len(events) == 2


async def caller_savepoint_rollback(database):
    draft = await seed(database)
    store = EventStore()
    async with database.sessions() as session, session.begin():
        await store.append(session, draft)
        run = await session.get(Run, draft.run_id)
        head = await session.get(AggregateHead, (draft.aggregate_type, draft.aggregate_id))
        savepoint = await session.begin_nested()
        event = await store.append(session, draft)
        event_id = event.id
        assert run.aggregate_version == head.sequence == 2
        await savepoint.rollback()
        for instance, field in ((run, "aggregate_version"), (head, "sequence")):
            if field in inspect(instance).expired_attributes:
                await session.refresh(instance, [field])
            assert getattr(instance, field) == 1
        assert await session.get(Event, event_id) is None
        assert (
            await session.scalar(select(OutboxMessage).where(OutboxMessage.event_id == event_id))
            is None
        )
        assert (await store.append(session, draft)).run_sequence == 2


async def persistence_failure_can_retry(database):
    draft = await seed(database)
    store = EventStore()
    async with database.sessions() as session, session.begin():
        first = await store.append(session, draft)
        first_id = first.id
        session.expunge(first)
        run = await session.get(Run, draft.run_id)
        head = await session.get(AggregateHead, (draft.aggregate_type, draft.aggregate_id))
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr("obsion.persistence.events.new_id", lambda: first_id)
            with pytest.raises(IntegrityError):
                await store.append(session, draft)
        assert session.is_active
        assert run.aggregate_version == head.sequence == 1
        assert not session.new and not session.dirty
        second = await store.append(session, draft)
        assert second.sequence == second.run_sequence == 2
    async with database.sessions() as session:
        events = list(await session.scalars(select(Event).where(Event.run_id == draft.run_id)))
        outbox = list(
            await session.scalars(
                select(OutboxMessage).join(Event).where(Event.run_id == draft.run_id)
            )
        )
        assert len(events) == len(outbox) == 2
        assert {item.id for item in events} == {item.event_id for item in outbox}


async def reject_cross_organization(database):
    draft = await seed(database)
    other = await seed(database)
    store = EventStore()
    async with database.sessions() as session, session.begin():
        await store.append(session, draft)
        with pytest.raises(ConflictError) as missing:
            await store.append(session, replace(draft, organization_id=other.organization_id))
        assert missing.value.code == "event_run_missing"
        with pytest.raises(ValueError, match="another organization"):
            await store.append(
                session, replace(draft, organization_id=other.organization_id, run_id=other.run_id)
            )
        assert (await session.get(Run, other.run_id)).aggregate_version == 0
        assert (await store.append(session, draft)).run_sequence == 2
