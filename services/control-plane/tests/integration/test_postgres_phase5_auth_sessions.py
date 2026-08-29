import os
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from obsion.config import get_settings
from obsion.db.models import AuthSession, Organization, User
from obsion.persistence.auth_sessions import AuthSessionStore


@pytest.mark.asyncio
async def test_postgres_browser_session_is_hashed_tenant_bound_and_revocable() -> None:
    if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
        pytest.skip("PostgreSQL invariant tests are opt-in")

    settings = get_settings()
    engine = create_async_engine(settings.database_url, poolclass=NullPool)
    connection = await engine.connect()
    transaction = await connection.begin()
    session = AsyncSession(bind=connection, expire_on_commit=False, autoflush=False)
    organization_id = uuid4()
    user_id = uuid4()
    store = AuthSessionStore()
    try:
        session.add(
            Organization(
                id=organization_id,
                slug=f"phase5-session-{organization_id}",
                name="Phase 5 session",
                active=True,
                settings={},
            )
        )
        await session.flush()
        session.add(
            User(
                id=user_id,
                organization_id=organization_id,
                external_id=f"phase5-{user_id}",
                email=f"{user_id}@example.invalid",
                display_name="Phase 5 user",
                active=True,
                attributes={},
            )
        )
        await session.flush()

        issued = await store.create(
            session,
            organization_id=organization_id,
            user_id=user_id,
            ttl_seconds=600,
        )
        stored = await session.scalar(
            select(AuthSession).where(AuthSession.organization_id == organization_id)
        )

        assert stored is not None
        assert stored.user_id == user_id
        assert len(stored.token_digest) == 64
        assert issued.token not in stored.token_digest
        assert await store.resolve(session, issued.token) is stored
        assert await store.revoke(session, issued.token)
        assert await store.resolve(session, issued.token) is None
    finally:
        await session.close()
        await transaction.rollback()
        await connection.close()
        await engine.dispose()
