"""Short PostgreSQL transaction fence for final source-authorized publication."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import ObsionError


async def acquire_source_publication_fence(session: AsyncSession, organization_id: UUID) -> None:
    """Hold through commit/rollback; acquire before any authorization reads.

    SQLite remains a test/development adapter and does not claim this PostgreSQL
    concurrency guarantee. A missing PostgreSQL migration fails closed.
    """
    if session.get_bind().dialect.name == "sqlite":
        return
    acquired = await session.scalar(
        text("SELECT obsion_acquire_source_fence(:organization_id)"),
        {"organization_id": organization_id},
    )
    if acquired is not True:
        raise ObsionError(
            "authorization_fence_unavailable",
            "无法在当前事务中确认资料权限，请稍后重试。",
            status_code=503,
        )
