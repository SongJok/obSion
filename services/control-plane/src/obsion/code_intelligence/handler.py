from collections.abc import Awaitable, Callable
from typing import Any

from obsion.capabilities.connectors import ConnectorContext, ConnectorResult
from obsion.code_intelligence.service import CodeIntelligenceService
from obsion.config import Settings
from obsion.db.models import Connector
from obsion.db.session import Database


def create_code_graph_handler(
    database: Database, settings: Settings
) -> Callable[[dict[str, Any], Connector, ConnectorContext], Awaitable[ConnectorResult]]:
    service = CodeIntelligenceService(settings)

    async def handler(
        payload: dict[str, Any], connector: Connector, context: ConnectorContext
    ) -> ConnectorResult:
        async with database.sessions() as session:
            data = await service.invoke(session, context.principal, payload)
        return ConnectorResult(
            data=data,
            source=connector.name,
            resource="authorized-code-graph",
        )

    return handler
