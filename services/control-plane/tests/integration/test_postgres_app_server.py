import asyncio
import os
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from obsion.common.time import utc_now
from obsion.config import get_settings
from obsion.db.models import (
    AppServerRequest,
    Event,
    Organization,
    OutboxMessage,
    Run,
    Thread,
    Turn,
    User,
    Workspace,
)
from obsion.domain.enums import ActorType
from obsion.persistence.app_server_requests import AppServerRequestStore, params_fingerprint
from obsion.persistence.events import EventDraft, EventStore
from obsion.security.identity import Principal


@pytest.mark.asyncio
async def test_app_server_cursor_and_idempotency_are_concurrent_and_database_guarded() -> None:
    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL invariant tests are opt-in")

    engine = create_async_engine(get_settings().database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    organization_id = uuid4()
    user_id = uuid4()
    workspace_id = uuid4()
    thread_id = uuid4()
    turn_id = uuid4()
    run_id = uuid4()
    now = utc_now()
    principal = Principal(
        id=user_id,
        organization_id=organization_id,
        external_id="app-server-invariant-owner",
        display_name="App Server invariant owner",
        permissions=frozenset({"*"}),
    )

    async with engine.begin() as connection:
        await connection.execute(
            insert(Organization).values(
                id=organization_id,
                slug=f"app-server-invariant-{organization_id}",
                name="App Server invariant",
                active=True,
                settings={},
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            insert(User).values(
                id=user_id,
                organization_id=organization_id,
                external_id=principal.external_id,
                email=f"{user_id}@example.invalid",
                display_name=principal.display_name,
                active=True,
                attributes={},
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            insert(Workspace).values(
                id=workspace_id,
                organization_id=organization_id,
                name="App Server workspace",
                description="",
                owner_id=user_id,
                classification="INTERNAL",
                visibility="PRIVATE",
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            insert(Thread).values(
                id=thread_id,
                organization_id=organization_id,
                workspace_id=workspace_id,
                title="App Server thread",
                status="ACTIVE",
                created_by=user_id,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            insert(Turn).values(
                id=turn_id,
                organization_id=organization_id,
                thread_id=thread_id,
                ordinal=1,
                created_by=user_id,
                input_text="Test concurrent stream ordering",
                sanitized_input="Test concurrent stream ordering",
                context_refs=[],
                attachment_refs=[],
                created_at=now,
            )
        )
        await connection.execute(
            insert(Run).values(
                id=run_id,
                organization_id=organization_id,
                turn_id=turn_id,
                # This fixture exercises EventStore sequencing directly and must not be
                # claimed by an independently running RunWorker sharing the database.
                status="WAITING_USER",
                intent={},
                plan={},
                max_steps=30,
                timeout_seconds=300,
                max_input_tokens=120_000,
                max_output_tokens=16_000,
                max_cost_amount=Decimal("10"),
                step_count=0,
                input_tokens=0,
                output_tokens=0,
                cost_amount=Decimal("0"),
                aggregate_version=0,
                created_at=now,
                updated_at=now,
            )
        )

    async def append_event(index: int) -> None:
        async with sessions() as session, session.begin():
            await EventStore().append(
                session,
                EventDraft(
                    name="capability.requested",
                    aggregate_type="run" if index % 2 == 0 else "artifact",
                    aggregate_id=run_id if index % 2 == 0 else uuid4(),
                    organization_id=organization_id,
                    correlation_id=run_id,
                    actor_type=ActorType.SYSTEM,
                    actor_id=None,
                    run_id=run_id,
                    payload={
                        "capability": "test.concurrent.read",
                        "version": 1,
                        "resource": {"index": index},
                    },
                ),
            )

    await asyncio.gather(*(append_event(index) for index in range(12)))
    async with sessions() as session:
        events = list(
            await session.scalars(
                select(Event).where(Event.run_id == run_id).order_by(Event.run_sequence)
            )
        )
    assert [event.run_sequence for event in events] == list(range(1, 13))
    assert len({event.id for event in events}) == 12

    async def claim_request() -> tuple[bool, dict[str, Any]]:
        async with sessions() as session, session.begin():
            claim = await AppServerRequestStore().claim(
                session,
                principal,
                client_request_id="concurrent-request",
                method="thread.create",
                fingerprint=params_fingerprint(
                    {"workspace_id": str(workspace_id), "title": "Only once"}
                ),
                retention_hours=24,
            )
            if claim.replayed_response is not None:
                return True, claim.replayed_response
            outcome = {"result": {"thread_id": str(thread_id)}}
            await AppServerRequestStore().complete(session, claim.record, outcome)
            return False, outcome

    claims = await asyncio.gather(claim_request(), claim_request())
    assert sorted(replayed for replayed, _ in claims) == [False, True]
    assert claims[0][1] == claims[1][1]

    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            request_id = await connection.scalar(
                select(AppServerRequest.id).where(
                    AppServerRequest.organization_id == organization_id
                )
            )
            assert request_id is not None
            for statement in (
                update(AppServerRequest)
                .where(AppServerRequest.id == request_id)
                .values(response={"result": {"thread_id": "rewritten"}}),
                update(AppServerRequest)
                .where(AppServerRequest.id == request_id)
                .values(method="run.cancel"),
                delete(AppServerRequest).where(AppServerRequest.id == request_id),
            ):
                savepoint = await connection.begin_nested()
                with pytest.raises(DBAPIError):
                    await connection.execute(statement)
                await savepoint.rollback()

            expired_id = uuid4()
            await connection.execute(
                insert(AppServerRequest).values(
                    id=expired_id,
                    organization_id=organization_id,
                    principal_id=user_id,
                    client_request_id="expired-request",
                    method="thread.create",
                    params_fingerprint="a" * 64,
                    response={"result": {}},
                    created_at=now - timedelta(hours=2),
                    completed_at=now - timedelta(hours=2),
                    expires_at=now - timedelta(hours=1),
                )
            )
            await connection.execute(
                delete(AppServerRequest).where(AppServerRequest.id == expired_id)
            )
        finally:
            await transaction.rollback()

    async with engine.begin() as connection:
        await connection.execute(
            delete(OutboxMessage).where(
                OutboxMessage.event_id.in_(
                    select(Event.id).where(Event.organization_id == organization_id)
                )
            )
        )
        await connection.exec_driver_sql("ALTER TABLE events DISABLE TRIGGER trg_events_immutable")
        await connection.exec_driver_sql(
            "ALTER TABLE app_server_requests DISABLE TRIGGER trg_app_server_requests_guard"
        )
        await connection.execute(delete(Organization).where(Organization.id == organization_id))
        await connection.exec_driver_sql(
            "ALTER TABLE app_server_requests ENABLE TRIGGER trg_app_server_requests_guard"
        )
        await connection.exec_driver_sql("ALTER TABLE events ENABLE TRIGGER trg_events_immutable")
    await engine.dispose()
