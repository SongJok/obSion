"""Opt-in against a disposable, already migrated PostgreSQL database.

Durable audit/turn records are deliberately retained for inspection; run only in
an operator-owned disposable test database, never a production tenant.
"""

import asyncio
import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from obsion.api.im_inbox_schemas import (
    CreateImInstallation,
    CreateInstallationBinding,
    TrustedImInbound,
)
from obsion.application.im_inbox import ImInboxService
from obsion.application.workspaces import WorkspaceService
from obsion.bootstrap import bootstrap_development_identity
from obsion.config import Environment, Settings
from obsion.db.im_models import ImInboxMessage
from obsion.db.models import Connector, Run, Turn
from obsion.domain.enums import ConnectorStatus
from obsion.security.auth import load_principal_by_id


@pytest.mark.asyncio
async def test_postgres_im_inbox_hundred_duplicates_and_restart() -> None:
    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL invariant tests are opt-in")
    settings = Settings(
        _env_file=None,
        environment=Environment.TEST,
        dev_organization_id=uuid4(),
        dev_user_id=uuid4(),
    )
    assert settings.database_url.startswith("postgresql"), "Disposable PostgreSQL required"
    engine = create_async_engine(settings.database_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    service = ImInboxService(WorkspaceService(settings))
    try:
        async with sessions() as session, session.begin():
            await bootstrap_development_identity(session, settings)
            actor = await load_principal_by_id(
                session, settings.dev_organization_id, settings.dev_user_id
            )
            connector = Connector(
                organization_id=actor.organization_id,
                name="im-inbox-test",
                connector_type="dingtalk-docs",
                environment="test",
                status=ConnectorStatus.ACTIVE,
            )
            session.add(connector)
            await session.flush()
            installation = await service.create_installation(
                session,
                actor,
                CreateImInstallation(
                    provider="dingtalk",
                    external_corp_id=str(uuid4()),
                    external_app_id="test-app",
                    connector_id=connector.id,
                    adapter_principal_id=actor.id,
                    verification_source="disposable-postgres-fixture",
                ),
            )
            await service.bind(
                session,
                actor,
                installation.id,
                CreateInstallationBinding(
                    sender_id="sender",
                    user_id=actor.id,
                ),
            )
        message = TrustedImInbound(
            vendor_event_id="event",
            sender_id="sender",
            conversation_id="conversation",
            conversation_type="group",
            text="hello",
        )

        async def receive() -> UUID:
            async with sessions() as session, session.begin():
                inbox, conflict = await service.receive(session, actor, installation.id, message)
                assert not conflict
                return inbox.id

        receipt_ids = await asyncio.gather(*(receive() for _ in range(100)))
        assert len(set(receipt_ids)) == 1

        async def process() -> UUID:
            async with sessions() as session, session.begin():
                inbox = await service.process(session, actor, installation.id, receipt_ids[0])
                assert inbox.run_id is not None
                return inbox.run_id

        run_ids = await asyncio.gather(*(process() for _ in range(100)))
        assert len(set(run_ids)) == 1
        # New engine, pool, application service and sessions replay durable state.
        await engine.dispose()
        service = ImInboxService(WorkspaceService(settings))
        assert await receive() == receipt_ids[0]
        assert await process() == run_ids[0]
        async with sessions() as session, session.begin():
            _, conflict = await service.receive(
                session,
                actor,
                installation.id,
                message.model_copy(update={"text": "different"}),
            )
            assert conflict
        async with sessions() as session:
            for model in (ImInboxMessage, Turn, Run):
                count = await session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.organization_id == actor.organization_id)
                )
                assert count == 1
            inbox = await session.get(ImInboxMessage, receipt_ids[0])
            assert inbox is not None
            # SQL-level uniqueness, not only application serialization.
            values = {
                column.name: getattr(inbox, column.name)
                for column in ImInboxMessage.__table__.columns
            }
            values["id"] = uuid4()
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    await session.execute(insert(ImInboxMessage).values(**values))
    finally:
        await engine.dispose()
