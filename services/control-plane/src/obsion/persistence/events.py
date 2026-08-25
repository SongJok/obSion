from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.ids import new_id
from obsion.common.time import utc_now
from obsion.db.models import AggregateHead, Event, OutboxMessage
from obsion.domain.enums import ActorType, Classification
from obsion.security.redaction import redact


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
        head = await session.scalar(
            select(AggregateHead)
            .where(
                AggregateHead.organization_id == draft.organization_id,
                AggregateHead.aggregate_type == draft.aggregate_type,
                AggregateHead.aggregate_id == draft.aggregate_id,
            )
            .with_for_update()
        )
        now = draft.created_at or utc_now()
        if head is None:
            head = AggregateHead(
                organization_id=draft.organization_id,
                aggregate_type=draft.aggregate_type,
                aggregate_id=draft.aggregate_id,
                sequence=0,
                updated_at=now,
            )
            session.add(head)
            await session.flush()
        head.sequence += 1
        head.updated_at = now

        event = Event(
            id=new_id(),
            organization_id=draft.organization_id,
            aggregate_type=draft.aggregate_type,
            aggregate_id=draft.aggregate_id,
            sequence=head.sequence,
            name=draft.name,
            run_id=draft.run_id,
            causation_id=draft.causation_id,
            correlation_id=draft.correlation_id,
            actor_type=draft.actor_type,
            actor_id=draft.actor_id,
            schema_version=draft.schema_version,
            classification=draft.classification,
            payload=redact(draft.payload),
            created_at=now,
        )
        session.add(event)
        session.add(
            OutboxMessage(
                event_id=event.id,
                topic=f"obsion.{draft.name}",
                payload={
                    "event_id": str(event.id),
                    "name": event.name,
                    "aggregate_type": event.aggregate_type,
                    "aggregate_id": str(event.aggregate_id),
                    "sequence": event.sequence,
                    "organization_id": str(event.organization_id),
                    "run_id": str(event.run_id) if event.run_id else None,
                    "correlation_id": str(event.correlation_id),
                    "created_at": now.isoformat(),
                    "payload": event.payload,
                },
                created_at=now,
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
                Event.sequence > after_sequence,
            )
            .order_by(Event.created_at, Event.sequence)
            .limit(limit)
        )
        return list(result)
