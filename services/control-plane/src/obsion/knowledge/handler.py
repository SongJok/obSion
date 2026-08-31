from collections.abc import Awaitable, Callable
from typing import Any

from obsion.artifacts.store import ObjectStore
from obsion.capabilities.connectors import ConnectorContext, ConnectorResult
from obsion.config import Settings
from obsion.db.models import Connector
from obsion.db.session import Database
from obsion.knowledge.service import KnowledgeService, bounded_search_limit


def create_knowledge_search_handler(
    database: Database, settings: Settings, store: ObjectStore
) -> Callable[[dict[str, Any], Connector, ConnectorContext], Awaitable[ConnectorResult]]:
    service = KnowledgeService(settings, store)

    async def handler(
        payload: dict[str, Any], connector: Connector, context: ConnectorContext
    ) -> ConnectorResult:
        async with database.sessions() as session:
            operation = str(payload.get("operation") or "knowledge.search")
            sources = ("ticket",) if operation == "ticket.search" else None
            exclude_sources = None if sources is not None else ("ticket",)
            raw_limit = payload.get("limit", 8)
            try:
                requested_limit = int(raw_limit)
            except (TypeError, ValueError):
                requested_limit = 8
            hits = await service.search(
                session,
                context.principal,
                str(payload["query"]),
                limit=bounded_search_limit(requested_limit, settings.knowledge_max_results),
                sources=sources,
                exclude_sources=exclude_sources,
            )
        return ConnectorResult(
            data={
                "query": payload["query"],
                "hits": [
                    {
                        "chunk_id": str(hit.chunk_id),
                        "document_id": str(hit.document_id),
                        "version": hit.version,
                        "title": hit.title,
                        "source": hit.source,
                        "heading_path": hit.heading_path,
                        "content": hit.content,
                        "score": hit.score,
                        "external_id": hit.external_id,
                        "revision_id": hit.revision_id,
                        "connector_name": hit.connector_name,
                        "operation": hit.operation,
                    }
                    for hit in hits
                ],
                "count": len(hits),
            },
            source=connector.name,
            resource="authorized-document-index",
        )

    return handler
