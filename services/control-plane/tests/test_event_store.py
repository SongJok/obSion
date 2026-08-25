from uuid import uuid4

from sqlalchemy import select

from obsion.config import Settings
from obsion.db.models import Event, OutboxMessage
from obsion.db.session import Database
from obsion.domain.enums import ActorType
from obsion.persistence.events import EventDraft, EventStore


async def test_event_append_is_ordered_redacted_and_transactional(app_settings: Settings) -> None:
    database = Database(app_settings)
    organization_id = uuid4()
    aggregate_id = uuid4()
    store = EventStore()
    async with database.sessions() as session, session.begin():
        for index in range(3):
            await store.append(
                session,
                EventDraft(
                    name=f"test.event.{index}",
                    aggregate_type="test",
                    aggregate_id=aggregate_id,
                    organization_id=organization_id,
                    correlation_id=aggregate_id,
                    actor_type=ActorType.SYSTEM,
                    actor_id=None,
                    payload={"index": index, "api_key": "never-store-this"},
                ),
            )
    async with database.sessions() as session:
        events = list(
            await session.scalars(
                select(Event).where(Event.aggregate_id == aggregate_id).order_by(Event.sequence)
            )
        )
        outbox = list(
            await session.scalars(select(OutboxMessage).order_by(OutboxMessage.created_at))
        )
    await database.dispose()
    assert [event.sequence for event in events] == [1, 2, 3]
    assert [event.payload["api_key"] for event in events] == ["[REDACTED]"] * 3
    assert [message.event_id for message in outbox] == [event.id for event in events]
