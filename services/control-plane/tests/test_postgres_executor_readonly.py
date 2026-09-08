"""只读配置必须显式为布尔真；拒绝发生在连接数据库之前。"""

import asyncio
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from obsion.capabilities.connectors import ConnectorContext, PostgresReadOnlyExecutor
from obsion.common.errors import ValidationError
from obsion.config import Settings
from obsion.db.models import Connector
from obsion.security.identity import Principal


@pytest.mark.parametrize("read_only", [None, False, 0, 1, "true", "false", "", [], {}])
def test_executor_rejects_non_boolean_readonly_before_connect(
    monkeypatch: pytest.MonkeyPatch, read_only: object
) -> None:
    connect = AsyncMock()
    monkeypatch.setattr("obsion.capabilities.connectors.asyncpg.connect", connect)
    configuration = {"role": "read_replica"}
    if read_only is not None:
        configuration["read_only"] = read_only
    connector = Connector(name="test", configuration=configuration)
    context = ConnectorContext(Principal(uuid4(), uuid4(), "test", "测试"), None, None)
    executor = PostgresReadOnlyExecutor(Settings(_env_file=None))
    with pytest.raises(ValidationError, match="read-only replica"):
        asyncio.run(executor.invoke(connector, {}, "synthetic-test-dsn", context))
    connect.assert_not_awaited()


@pytest.mark.parametrize("parameters", [None, 42, False, "invalid", {}])
def test_executor_explicit_readonly_reaches_payload_validation(
    monkeypatch: pytest.MonkeyPatch,
    parameters: object,
) -> None:
    connect = AsyncMock()
    monkeypatch.setattr("obsion.capabilities.connectors.asyncpg.connect", connect)
    connector = Connector(name="test", configuration={"role": "read_replica", "read_only": True})
    context = ConnectorContext(Principal(uuid4(), uuid4(), "test", "测试"), None, None)
    executor = PostgresReadOnlyExecutor(Settings(_env_file=None))
    with pytest.raises(ValidationError, match="SQL and parameters are required"):
        asyncio.run(
            executor.invoke(
                connector,
                {"sql": "SELECT id FROM facts LIMIT 1", "parameters": parameters},
                "synthetic-test-dsn",
                context,
            )
        )
    connect.assert_not_awaited()
