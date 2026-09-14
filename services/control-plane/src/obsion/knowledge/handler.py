from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from obsion.artifacts.store import ObjectStore
from obsion.capabilities.connectors import ConnectorContext, ConnectorResult
from obsion.common.time import utc_now
from obsion.config import Settings
from obsion.db.models import Connector, Run
from obsion.db.session import Database
from obsion.domain.enums import Classification
from obsion.domain.task_context import selected_document_scope
from obsion.knowledge.document_read import read_document_page
from obsion.knowledge.live import LiveKnowledgeUnavailable, await_live_sources
from obsion.knowledge.service import KnowledgeService, bounded_search_limit
from obsion.knowledge.source_access import MANAGED_KNOWLEDGE_SOURCE
from obsion.security.classification import maximum_classification


def create_knowledge_search_handler(
    database: Database, settings: Settings, store: ObjectStore
) -> Callable[[dict[str, Any], Connector, ConnectorContext], Awaitable[ConnectorResult]]:
    service = KnowledgeService(settings, store)

    async def handler(
        payload: dict[str, Any], connector: Connector, context: ConnectorContext
    ) -> ConnectorResult:
        operation = str(payload.get("operation") or "knowledge.search")
        run = (
            await context.session.get(Run, context.run_id)
            if context.run_id is not None and context.session is not None
            else None
        )
        proof = None
        needs_live = settings.enterprise_knowledge_mode == "live" and operation != "ticket.search"
        # Explicitly selected native uploads are user-provided documents, not a
        # fallback enterprise source. An arbitrary payload cannot grant this scope.
        if needs_live and operation == "document.read" and run is not None:
            selected = selected_document_scope(run.intent)
            if any(item["document_id"] == payload.get("document_id") for item in selected):
                async with database.sessions() as session:
                    document, _ = await service.get_document(
                        session, context.principal, UUID(payload["document_id"])
                    )
                    needs_live = document.source == MANAGED_KNOWLEDGE_SOURCE
        if needs_live:
            try:
                proof = await await_live_sources(
                    database,
                    context.principal,
                    requested_at=run.created_at if run is not None else utc_now(),
                    wait_seconds=settings.knowledge_live_wait_seconds,
                )
            except LiveKnowledgeUnavailable as exc:
                state = {"status": "BLOCKED", "reason": str(exc)}
                if run is not None:
                    run.plan = {**run.plan, "knowledge_live": state}
                unavailable = {
                    "query": payload.get("query", ""),
                    "hits": [],
                    "count": 0,
                    "freshness": state,
                }
                if operation == "document.read":
                    unavailable.update(
                        operation=operation,
                        document_id=payload["document_id"],
                        version=payload["version"],
                        next_offset=None,
                    )
                return ConnectorResult(
                    data=unavailable,
                    source=connector.name,
                    resource="external-knowledge-unavailable",
                )
            if run is not None:
                run.plan = {**run.plan, "knowledge_live": proof.as_dict()}
        async with database.sessions() as session:
            if operation == "document.read":
                page = await read_document_page(
                    session, context.principal, payload, live_proof=proof
                )
                if proof is not None:
                    page["freshness"] = proof.as_dict()
                return ConnectorResult(
                    data=page,
                    source=connector.name,
                    resource="authorized-document-index",
                    classification=Classification(page["classification"]),
                )
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
                live_proof=proof,
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
                **({"freshness": proof.as_dict()} if proof is not None else {}),
            },
            source=connector.name,
            resource="authorized-document-index",
            classification=maximum_classification(*(hit.classification for hit in hits)),
        )

    return handler
