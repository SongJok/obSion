from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import false, select, update
from sqlalchemy.dialects.postgresql import Insert as PostgreSQLInsert
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import Insert as SQLiteInsert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_dirty, set_committed_value
from sqlalchemy.orm.util import identity_key

from obsion.common.errors import ConflictError
from obsion.common.ids import new_id
from obsion.common.time import utc_now
from obsion.contracts.events.validation import (
    PreparedEventDraft,
    build_event_envelope,
    prepare_event_draft,
    validate_event_envelope,
)
from obsion.db.models import AggregateHead, Event, OutboxMessage, Run, Thread, Turn
from obsion.domain.enums import ActorType, Classification


@dataclass(frozen=True, slots=True)
class EventDraft:
    name: str
    aggregate_type: str
    aggregate_id: UUID
    organization_id: UUID
    correlation_id: UUID
    actor_type: ActorType
    actor_id: UUID | None
    payload: dict[str, Any] = field(default_factory=dict)
    run_id: UUID | None = None
    causation_id: UUID | None = None
    classification: Classification = Classification.INTERNAL
    schema_version: int = 1
    created_at: datetime | None = None


class EventStore:
    async def append(self, session: AsyncSession, draft: EventDraft) -> Event:
        now = draft.created_at or utc_now()
        event_id = new_id()
        prepared = prepare_event_draft(
            event_id=event_id,
            name=draft.name,
            schema_version=draft.schema_version,
            organization_id=draft.organization_id,
            aggregate_type=draft.aggregate_type,
            aggregate_id=draft.aggregate_id,
            run_id=draft.run_id,
            causation_id=draft.causation_id,
            correlation_id=draft.correlation_id,
            actor_type=draft.actor_type,
            actor_id=draft.actor_id,
            classification=draft.classification,
            payload=draft.payload,
            created_at=now,
        )

        # SAVEPOINT 前保留调用者状态；事件失败即使被捕获也不能提交部分序号。
        await session.flush()
        if session.get_bind().dialect.name == "sqlite":
            # sqlite3 legacy 模式不以 SELECT 开始物理事务；空写避免 SAVEPOINT 自行提交。
            await session.execute(
                update(AggregateHead)
                .where(false())
                .values(sequence=AggregateHead.sequence)
                .execution_options(synchronize_session=False)
            )
        async with session.begin_nested():
            event = await self._append_prepared(session, draft, prepared, now)
        # 只有 SAVEPOINT 成功后才同步已有缓存；失败不能留下未提交的计数。
        if draft.run_id is not None:
            run = session.identity_map.get(identity_key(Run, draft.run_id))
            if run is not None:
                set_committed_value(run, "aggregate_version", event.run_sequence)
                flag_dirty(run)
        head = session.identity_map.get(
            identity_key(AggregateHead, (draft.aggregate_type, draft.aggregate_id))
        )
        if head is not None:
            set_committed_value(head, "sequence", event.sequence)
            set_committed_value(head, "updated_at", now)
            flag_dirty(head)
        # 无属性历史变化，不发 UPDATE；登记事务状态使调用者 SAVEPOINT 回滚时缓存失效。
        await session.flush()
        return event

    async def _append_prepared(
        self,
        session: AsyncSession,
        draft: EventDraft,
        prepared: PreparedEventDraft,
        now: datetime,
    ) -> Event:
        run_sequence: int | None = None
        if draft.run_id is not None:
            run_sequence = await session.scalar(
                update(Run)
                .where(
                    Run.id == draft.run_id,
                    Run.organization_id == draft.organization_id,
                )
                .values(aggregate_version=Run.aggregate_version + 1)
                .returning(Run.aggregate_version)
                .execution_options(synchronize_session=False)
            )
            if run_sequence is None:
                raise ConflictError(
                    "event_run_missing",
                    "A Run-associated event requires an existing Run",
                    run_id=str(draft.run_id),
                )

        dialect = session.get_bind().dialect.name
        insert_head: PostgreSQLInsert | SQLiteInsert
        if dialect == "postgresql":
            insert_head = postgresql_insert(AggregateHead)
        elif dialect == "sqlite":
            insert_head = sqlite_insert(AggregateHead)
        else:
            raise ValueError("Event sequence allocation requires PostgreSQL or SQLite")
        sequence = await session.scalar(
            insert_head.values(
                organization_id=draft.organization_id,
                aggregate_type=draft.aggregate_type,
                aggregate_id=draft.aggregate_id,
                sequence=1,
                updated_at=now,
            )
            .on_conflict_do_update(
                index_elements=[AggregateHead.aggregate_type, AggregateHead.aggregate_id],
                set_={"sequence": AggregateHead.sequence + 1, "updated_at": now},
                where=AggregateHead.organization_id == draft.organization_id,
            )
            .returning(AggregateHead.sequence)
            .execution_options(synchronize_session=False)
        )
        if sequence is None:
            raise ValueError("Event aggregate belongs to another organization")

        event = Event(
            id=prepared.event_id,
            organization_id=draft.organization_id,
            aggregate_type=draft.aggregate_type,
            aggregate_id=draft.aggregate_id,
            sequence=sequence,
            name=draft.name,
            run_id=draft.run_id,
            run_sequence=run_sequence,
            causation_id=draft.causation_id,
            correlation_id=draft.correlation_id,
            actor_type=draft.actor_type,
            actor_id=draft.actor_id,
            schema_version=draft.schema_version,
            classification=draft.classification,
            payload=prepared.payload,
            created_at=prepared.created_at,
        )
        envelope = build_event_envelope(
            event_id=event.id,
            organization_id=event.organization_id,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            sequence=event.sequence,
            name=event.name,
            run_id=event.run_id,
            run_sequence=event.run_sequence,
            causation_id=event.causation_id,
            correlation_id=event.correlation_id,
            actor_type=event.actor_type,
            actor_id=event.actor_id,
            schema_version=event.schema_version,
            classification=event.classification,
            payload=event.payload,
            created_at=event.created_at,
        )
        validate_event_envelope(
            envelope,
            event_name=event.name,
            schema_version=event.schema_version,
        )

        session.add(event)
        session.add(
            OutboxMessage(
                event_id=event.id,
                topic=f"obsion.{draft.name}",
                payload=envelope,
                created_at=prepared.created_at,
                attempt_count=0,
            )
        )
        await session.flush()
        return event

    async def list_run(
        self,
        session: AsyncSession,
        organization_id: UUID,
        run_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> list[Event]:
        result = await session.scalars(
            select(Event)
            .where(
                Event.organization_id == organization_id,
                Event.run_id == run_id,
                Event.run_sequence > after_sequence,
            )
            .order_by(Event.run_sequence)
            .limit(limit)
        )
        return list(result)

    async def list_workspace(
        self,
        session: AsyncSession,
        organization_id: UUID,
        workspace_id: UUID,
        *,
        limit: int = 500,
    ) -> list[Event]:
        result = await session.scalars(
            select(Event)
            .join(Run, Run.id == Event.run_id)
            .join(Turn, Turn.id == Run.turn_id)
            .join(Thread, Thread.id == Turn.thread_id)
            .where(
                Event.organization_id == organization_id,
                Run.organization_id == organization_id,
                Thread.workspace_id == workspace_id,
                Event.run_id.is_not(None),
            )
            .order_by(Event.created_at.desc())
            .limit(limit)
        )
        return list(result)

    async def list_aggregate(
        self,
        session: AsyncSession,
        organization_id: UUID,
        aggregate_type: str,
        aggregate_id: UUID,
        *,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> list[Event]:
        result = await session.scalars(
            select(Event)
            .where(
                Event.organization_id == organization_id,
                Event.aggregate_type == aggregate_type,
                Event.aggregate_id == aggregate_id,
                Event.sequence > after_sequence,
            )
            .order_by(Event.sequence)
            .limit(limit)
        )
        return list(result)
