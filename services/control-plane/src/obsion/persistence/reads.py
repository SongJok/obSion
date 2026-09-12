"""Explicit read-only service boundary retaining authorization audit decisions."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import ObsionError
from obsion.db.session import Database


@asynccontextmanager
async def audited_read_session(database: Database) -> AsyncIterator[AsyncSession]:
    """Only for read handlers: commit Policy/Audit even on a registered denial.

    Never use this for mutations. Their rollback/denial behavior belongs to
    their own unit of work. Unexpected exceptions still roll back this session.
    """
    rejected: ObsionError | None = None
    async with database.sessions() as session:
        async with session.begin():
            try:
                yield session
            except ObsionError as error:
                rejected = error
        if rejected is not None:
            raise rejected
