from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import ConflictError
from obsion.common.ids import new_id
from obsion.common.time import utc_now
from obsion.contracts.events.validation import (
    build_event_envelope,
    prepare_event_draft,
    validate_event_envelope,
)
from obsion.db.models import AggregateHead, Event, OutboxMessage, Run
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

        run: Run | None = None
        run_sequence: int | None = None
        if draft.run_id is not None:
            run = await session.scalar(
                select(Run)
                .where(
                    Run.id == draft.run_id,
                    Run.organization_id == draft.organization_id,
                )
                .with_for_update()
            )
            if run is None:
                raise ConflictError(
                    "event_run_missing",
                    "A Run-associated event requires an existing Run",
                    run_id=str(draft.run_id),
                )
            run_sequence = run.aggregate_version + 1

        head = await session.scalar(
            select(AggregateHead)
            .where(
                AggregateHead.organization_id == draft.organization_id,
                AggregateHead.aggregate_type == draft.aggregate_type,
                AggregateHead.aggregate_id == draft.aggregate_id,
            )
            .with_for_update()
        )
        sequence = (head.sequence if head is not None else 0) + 1

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

        if run is not None:
            assert run_sequence is not None
            run.aggregate_version = run_sequence
        if head is None:
            head = AggregateHead(
                organization_id=draft.organization_id,
                aggregate_type=draft.aggregate_type,
                aggregate_id=draft.aggregate_id,
                sequence=sequence,
                updated_at=now,
            )
            session.add(head)
        else:
            head.sequence = sequence
            head.updated_at = now

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
