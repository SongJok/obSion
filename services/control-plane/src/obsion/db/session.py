from collections.abc import AsyncIterator
from typing import Protocol, cast

from sqlalchemy import Connection, event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from obsion.config import Settings


class _SQLiteTransactionState(Protocol):
    @property
    def in_transaction(self) -> bool: ...


def _begin_sqlite_savepoint(connection: Connection, name: str | None) -> None:
    # legacy 驱动不为 SAVEPOINT 开启事务；只在此边界补齐，保留普通读取行为。
    driver = cast(_SQLiteTransactionState, connection.connection.driver_connection)
    if not driver.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")


class Database:
    def __init__(self, settings: Settings) -> None:
        engine_kwargs: dict[str, object] = {
            "echo": settings.database_echo,
            "pool_pre_ping": True,
        }
        if not settings.database_url.startswith("sqlite"):
            engine_kwargs.update(
                pool_size=settings.database_pool_size,
                max_overflow=settings.database_pool_max_overflow,
            )
        self.engine: AsyncEngine = create_async_engine(settings.database_url, **engine_kwargs)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine.sync_engine, "savepoint", _begin_sqlite_savepoint)
        self.sessions = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )

    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.sessions() as session:
            yield session

    async def dispose(self) -> None:
        await self.engine.dispose()
