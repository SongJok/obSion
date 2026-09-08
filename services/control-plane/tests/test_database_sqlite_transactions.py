"""运行时 Database 的 SQLite SAVEPOINT 事务与普通读取兼容性。"""

import pytest
import pytest_asyncio
from sqlalchemy import text

from obsion.config import Settings
from obsion.db.session import Database


@pytest_asyncio.fixture
async def database(tmp_path):
    database = Database(
        Settings(
            _env_file=None,
            environment="test",
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'transactions.db'}",
        )
    )
    try:
        async with database.engine.begin() as connection:
            await connection.execute(text("CREATE TABLE fixture (id INTEGER PRIMARY KEY)"))
        yield database
    finally:
        await database.dispose()


@pytest.mark.parametrize("initial_write", [False, True])
@pytest.mark.parametrize("commit", [False, True])
async def test_savepoint_release_remains_owned_by_outer_transaction(
    database, initial_write, commit
):
    async with database.sessions() as session:
        await session.begin()
        if initial_write:
            await session.execute(text("INSERT INTO fixture VALUES (1)"))
        else:
            assert await session.scalar(text("SELECT count(*) FROM fixture")) == 0
        async with session.begin_nested():
            await session.execute(text("INSERT INTO fixture VALUES (2)"))
        if commit:
            await session.commit()
        else:
            await session.rollback()
    async with database.sessions() as independent:
        actual = list((await independent.scalars(text("SELECT id FROM fixture ORDER BY id"))).all())
    assert actual == (([1, 2] if initial_write else [2]) if commit else [])


async def test_nested_rollback_keeps_outer_and_later_savepoint_writes(database):
    async with database.sessions() as session, session.begin():
        await session.execute(text("INSERT INTO fixture VALUES (1)"))
        with pytest.raises(RuntimeError, match="synthetic-rollback"):
            async with session.begin_nested():
                await session.execute(text("INSERT INTO fixture VALUES (2)"))
                raise RuntimeError("synthetic-rollback")
        async with session.begin_nested():
            await session.execute(text("INSERT INTO fixture VALUES (3)"))
    async with database.sessions() as independent:
        assert list(
            (await independent.scalars(text("SELECT id FROM fixture ORDER BY id"))).all()
        ) == [
            1,
            3,
        ]


async def test_plain_read_keeps_legacy_behavior_without_blocking_independent_writer(database):
    async with database.engine.connect() as reader:
        assert await reader.scalar(text("SELECT count(*) FROM fixture")) == 0
        driver = (await reader.get_raw_connection()).driver_connection
        assert driver.in_transaction is False
        async with database.engine.begin() as writer:
            await writer.execute(text("INSERT INTO fixture VALUES (1)"))
        # 普通读取保留原有非 repeatable 行为；不声称 SQLite 提供 PostgreSQL 隔离语义。
        assert await reader.scalar(text("SELECT count(*) FROM fixture")) == 1
