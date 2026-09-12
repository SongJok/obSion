"""Read bounded, ordered pages of the caller's current authorized document chunks."""

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import NotFoundError
from obsion.db.models import Document, DocumentChunk, DocumentVersion
from obsion.knowledge.connector_contract import provenance_fields_from_version
from obsion.knowledge.service import KnowledgeService, _authorization_clause
from obsion.knowledge.source_access import external_access_clause
from obsion.security.classification import maximum_classification
from obsion.security.identity import Principal

INPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["operation", "document_id", "version"],
    "properties": {
        "operation": {"const": "document.read"},
        "document_id": {"type": "string", "format": "uuid"},
        "version": {"type": "integer", "minimum": 1},
        "offset": {"type": "integer", "minimum": 0, "maximum": 10000},
        "limit": {"type": "integer", "minimum": 1, "maximum": 8},
    },
}
OUTPUT_SCHEMA = {
    "type": "object",
    "required": ["operation", "document_id", "version", "hits", "count", "next_offset"],
    "properties": {
        "operation": {"const": "document.read"},
        "document_id": {"type": "string", "format": "uuid"},
        "version": {"type": "integer"},
        "hits": {"type": "array"},
        "count": {"type": "integer"},
        "next_offset": {"type": ["integer", "null"]},
    },
}


async def read_document_page(
    session: AsyncSession, principal: Principal, payload: dict[str, Any]
) -> dict[str, Any]:
    document_id = UUID(payload["document_id"])
    document, version = await KnowledgeService.get_document(session, principal, document_id)
    if version.version != payload["version"]:
        raise NotFoundError("Document version", document_id)
    offset = int(payload.get("offset", 0))
    limit = max(1, min(int(payload.get("limit", 4)), 8))
    chunks = list(
        await session.scalars(
            select(DocumentChunk)
            .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
            .join(Document, Document.id == DocumentVersion.document_id)
            .where(
                DocumentChunk.organization_id == principal.organization_id,
                Document.organization_id == principal.organization_id,
                Document.id == document_id,
                DocumentVersion.id == version.id,
                DocumentVersion.version == Document.current_version,
                Document.deleted_at.is_(None),
                _authorization_clause(principal),
                external_access_clause(principal),
            )
            .order_by(DocumentChunk.ordinal, DocumentChunk.id)
            .offset(offset)
            .limit(limit + 1)
        )
    )
    provenance = provenance_fields_from_version(
        source=document.source,
        external_id=document.external_id,
        metadata=version.metadata_json if isinstance(version.metadata_json, dict) else None,
    )
    return {
        "operation": "document.read",
        "document_id": str(document_id),
        "version": version.version,
        "scope": "current_authorized_document_chunks",
        "classification": maximum_classification(
            document.classification, *(c.classification for c in chunks[:limit])
        ).value,
        "hits": [
            {
                "chunk_id": str(chunk.id),
                "document_id": str(document_id),
                "version": version.version,
                "title": document.title,
                "source": document.source,
                "heading_path": chunk.heading_path,
                "content": chunk.content,
                **provenance,
            }
            for chunk in chunks[:limit]
        ],
        "count": min(len(chunks), limit),
        "next_offset": offset + limit if len(chunks) > limit else None,
    }
