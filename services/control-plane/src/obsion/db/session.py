from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from obsion.config import Settings


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
