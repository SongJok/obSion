import hashlib
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy import and_, delete, exists, false, func, literal_column, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from obsion.artifacts.store import ObjectStore, StoredObject
from obsion.common.errors import AuthorizationError, NotFoundError, ObsionError, ValidationError
from obsion.common.ids import new_id
from obsion.common.time import utc_now
from obsion.config import Settings
from obsion.db.models import Document, DocumentChunk, DocumentChunkGrant, DocumentVersion
from obsion.domain.enums import Classification
from obsion.knowledge.connector_contract import provenance_fields_from_version
from obsion.knowledge.parsers import chunk_document, parse_document
from obsion.model_gateway.gateway import ModelGateway
from obsion.security.identity import Principal
from obsion.telemetry import retrieval_duration

_TOKEN = re.compile(r"[\w\u3400-\u9fff]+", re.UNICODE)


def bounded_search_limit(limit: int, maximum: int) -> int:
    return max(1, min(limit, maximum))


_CLASSIFICATION_LEVEL = {
    Classification.PUBLIC: 0,
    Classification.INTERNAL: 1,
    Classification.CONFIDENTIAL: 2,
    Classification.RESTRICTED: 3,
}
_ACL_LIST_KEYS = {
    "users",
    "roles",
    "departments",
    "deny_users",
    "deny_roles",
    "deny_departments",
}
_ACL_KEYS = _ACL_LIST_KEYS | {"organization"}


@dataclass(frozen=True, slots=True)
class SearchHit:
    chunk_id: UUID
    document_id: UUID
    version: int
    title: str
    source: str
    heading_path: list[str]
    content: str
    score: float
    classification: Classification
    external_id: str | None = None
    revision_id: str | None = None
    connector_name: str | None = None
    operation: str | None = None


def _authorized(principal: Principal, classification: Classification, acl: dict[str, Any]) -> bool:
    if str(principal.id) in set(acl.get("deny_users", [])):
        return False
    if principal.roles.intersection(acl.get("deny_roles", [])):
        return False
    if principal.department and principal.department in set(acl.get("deny_departments", [])):
        return False
    if str(principal.id) in set(acl.get("users", [])):
        return True
    if principal.roles.intersection(acl.get("roles", [])):
        return True
    if principal.department and principal.department in set(acl.get("departments", [])):
        return True
    if acl.get("organization") is True and _CLASSIFICATION_LEVEL[classification] <= 1:
        return True
    required_permission = f"knowledge.read.{classification.value.lower()}"
    return principal.can(required_permission)


def validate_document_acl(acl: dict[str, Any]) -> dict[str, Any]:
    return _validate_acl(acl)


def _validate_acl(acl: dict[str, Any]) -> dict[str, Any]:
    unknown = set(acl) - _ACL_KEYS
    if unknown:
        raise ValidationError(
            "document_acl_invalid",
            "Document ACL contains unsupported fields",
            fields=sorted(unknown),
        )
    if "organization" in acl and not isinstance(acl["organization"], bool):
        raise ValidationError(
            "document_acl_invalid", "The organization ACL field must be a boolean"
        )
    normalized: dict[str, Any] = {"organization": bool(acl.get("organization", False))}
    for key in _ACL_LIST_KEYS:
        value = acl.get(key, [])
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ValidationError(
                "document_acl_invalid", f"The {key} ACL field must be a list of strings"
            )
        normalized[key] = sorted({item.strip() for item in value})
    return normalized


def _grant_rows(
    *,
    organization_id: UUID,
    chunk_id: UUID,
    acl: dict[str, Any],
) -> list[DocumentChunkGrant]:
    subjects: set[tuple[str, str, str]] = set()
    for key, subject_type in (
        ("users", "USER"),
        ("roles", "ROLE"),
        ("departments", "DEPARTMENT"),
    ):
        subjects.update(("ALLOW", subject_type, value) for value in acl[key])
    for key, subject_type in (
        ("deny_users", "USER"),
        ("deny_roles", "ROLE"),
        ("deny_departments", "DEPARTMENT"),
    ):
        subjects.update(("DENY", subject_type, value) for value in acl[key])
    if acl["organization"]:
        subjects.add(("ALLOW", "ORGANIZATION", str(organization_id)))
    now = utc_now()
    return [
        DocumentChunkGrant(
            organization_id=organization_id,
            chunk_id=chunk_id,
            effect=effect,
            subject_type=subject_type,
            subject_value=subject_value,
            created_at=now,
        )
        for effect, subject_type, subject_value in sorted(subjects)
    ]


def _subject_clause(
    grant: type[DocumentChunkGrant], principal: Principal, *, include_organization: bool
) -> ColumnElement[bool]:
    clauses: list[ColumnElement[bool]] = [
        and_(grant.subject_type == "USER", grant.subject_value == str(principal.id))
    ]
    if principal.roles:
        clauses.append(
            and_(grant.subject_type == "ROLE", grant.subject_value.in_(sorted(principal.roles)))
        )
    if principal.department:
        clauses.append(
            and_(
                grant.subject_type == "DEPARTMENT",
                grant.subject_value == principal.department,
            )
        )
    if include_organization:
        clauses.append(
            and_(
                grant.subject_type == "ORGANIZATION",
                grant.subject_value == str(principal.organization_id),
            )
        )
    return or_(*clauses)


def _authorization_clause(principal: Principal) -> ColumnElement[bool]:
    deny = exists(
        select(DocumentChunkGrant.chunk_id).where(
            DocumentChunkGrant.chunk_id == DocumentChunk.id,
            DocumentChunkGrant.organization_id == principal.organization_id,
            DocumentChunkGrant.effect == "DENY",
            _subject_clause(DocumentChunkGrant, principal, include_organization=False),
        )
    )
    direct_allow = exists(
        select(DocumentChunkGrant.chunk_id).where(
            DocumentChunkGrant.chunk_id == DocumentChunk.id,
            DocumentChunkGrant.organization_id == principal.organization_id,
            DocumentChunkGrant.effect == "ALLOW",
            _subject_clause(DocumentChunkGrant, principal, include_organization=False),
        )
    )
    organization_allow = and_(
        DocumentChunk.classification.in_([Classification.PUBLIC, Classification.INTERNAL]),
        exists(
            select(DocumentChunkGrant.chunk_id).where(
                DocumentChunkGrant.chunk_id == DocumentChunk.id,
                DocumentChunkGrant.organization_id == principal.organization_id,
                DocumentChunkGrant.effect == "ALLOW",
                DocumentChunkGrant.subject_type == "ORGANIZATION",
                DocumentChunkGrant.subject_value == str(principal.organization_id),
            )
        ),
    )
    permitted = [
        classification
        for classification in Classification
        if principal.can(f"knowledge.read.{classification.value.lower()}")
    ]
    permission_allow: ColumnElement[bool] = (
        DocumentChunk.classification.in_(permitted) if permitted else false()
    )
    return and_(~deny, or_(direct_allow, organization_allow, permission_allow))


class KnowledgeService:
    def __init__(
        self,
        settings: Settings,
        store: ObjectStore,
        model_gateway: ModelGateway | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.model_gateway = model_gateway or ModelGateway(settings)

    async def ingest(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        source: str,
        external_id: str,
        title: str,
        media_type: str,
        filename: str,
        content: bytes,
        classification: Classification,
        acl: dict[str, Any],
        extra_metadata: dict[str, Any] | None = None,
    ) -> tuple[Document, DocumentVersion, int]:
        if not principal.can("knowledge.write"):
            raise AuthorizationError(
                "knowledge_write_denied", "Document ingestion is not permitted"
            )
        if len(content) > self.settings.document_max_upload_bytes:
            raise ValidationError(
                "document_too_large",
                "The document exceeds the configured upload limit",
                max_bytes=self.settings.document_max_upload_bytes,
            )
        if not acl:
            raise ValidationError("document_acl_required", "An explicit document ACL is required")
        acl = _validate_acl(acl)
        parsed = parse_document(content, media_type, filename)
        checksum = hashlib.sha256(content).hexdigest()
        document = await session.scalar(
            select(Document)
            .where(
                Document.organization_id == principal.organization_id,
                Document.source == source,
                Document.external_id == external_id,
            )
            .with_for_update()
        )
        if document is None:
            document = Document(
                organization_id=principal.organization_id,
                source=source,
                external_id=external_id,
                title=title,
                classification=classification,
                acl=acl,
                current_version=0,
            )
            session.add(document)
            await session.flush()
        elif document.current_version > 0:
            current = await session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.document_id == document.id,
                    DocumentVersion.version == document.current_version,
                )
            )
            if current is not None and current.checksum_sha256 == checksum:
                # Re-ingesting identical bytes is still an authorization mutation:
                # callers may tighten ACL/classification without creating a new
                # content version. Rebuild chunk grants atomically before returning.
                document.title = title
                document.classification = classification
                document.acl = acl
                document.deleted_at = None
                current_chunks = list(
                    await session.scalars(
                        select(DocumentChunk).where(
                            DocumentChunk.organization_id == principal.organization_id,
                            DocumentChunk.document_version_id == current.id,
                        )
                    )
                )
                if current_chunks:
                    chunk_ids = [chunk.id for chunk in current_chunks]
                    await session.execute(
                        delete(DocumentChunkGrant).where(
                            DocumentChunkGrant.organization_id == principal.organization_id,
                            DocumentChunkGrant.chunk_id.in_(chunk_ids),
                        )
                    )
                    for chunk in current_chunks:
                        chunk.classification = classification
                        chunk.acl = acl
                        session.add_all(
                            _grant_rows(
                                organization_id=principal.organization_id,
                                chunk_id=chunk.id,
                                acl=acl,
                            )
                        )
                await session.flush()
                return document, current, len(current_chunks)

        version_number = document.current_version + 1
        version_id = new_id()
        storage_key = f"{principal.organization_id}/knowledge/{document.id}/{version_id}"
        version = DocumentVersion(
            id=version_id,
            organization_id=principal.organization_id,
            document_id=document.id,
            version=version_number,
            media_type=media_type,
            extracted_text=parsed.text,
            checksum_sha256=checksum,
            content_ref=storage_key,
            parser_version=parsed.parser_version,
            metadata_json={
                **parsed.metadata,
                "filename": filename,
                **(extra_metadata or {}),
            },
            created_at=utc_now(),
        )
        session.add(version)
        await session.flush()
        chunks = chunk_document(parsed.text)
        embeddings: list[list[float] | None] = [None] * len(chunks)
        embedding_refs: list[str | None] = [None] * len(chunks)
        if self.settings.knowledge_embedding_profile and chunks:
            batch_size = self.settings.knowledge_embedding_batch_size
            for offset in range(0, len(chunks), batch_size):
                batch = chunks[offset : offset + batch_size]
                result = await self.model_gateway.embed(
                    session,
                    organization_id=principal.organization_id,
                    profile_name=self.settings.knowledge_embedding_profile,
                    texts=[chunk_text for _, chunk_text in batch],
                    classification=classification,
                )
                for index, vector in enumerate(result.embeddings, start=offset):
                    embeddings[index] = vector
                    embedding_refs[index] = f"model-endpoint://{result.endpoint_id}"
        chunk_rows: list[DocumentChunk] = []
        for ordinal, (heading_path, chunk_text) in enumerate(chunks, start=1):
            chunk_row = DocumentChunk(
                organization_id=principal.organization_id,
                document_version_id=version.id,
                ordinal=ordinal,
                heading_path=heading_path,
                content=chunk_text,
                token_count=max(1, len(chunk_text) // 4),
                classification=classification,
                acl=acl,
                embedding_ref=embedding_refs[ordinal - 1],
                embedding=embeddings[ordinal - 1],
                created_at=utc_now(),
            )
            session.add(chunk_row)
            chunk_rows.append(chunk_row)
        await session.flush()
        for chunk_row in chunk_rows:
            session.add_all(
                _grant_rows(
                    organization_id=principal.organization_id,
                    chunk_id=chunk_row.id,
                    acl=acl,
                )
            )
        try:
            await self.store.put(
                storage_key,
                content,
                media_type=media_type,
                metadata={"sha256": checksum, "classification": classification.value},
            )
        except Exception:
            await self.store.delete(storage_key)
            raise
        document.title = title
        document.classification = classification
        document.acl = acl
        document.current_version = version_number
        document.deleted_at = None
        return document, version, len(chunks)

    async def search(
        self,
        session: AsyncSession,
        principal: Principal,
        query: str,
        *,
        limit: int = 8,
        sources: tuple[str, ...] | None = None,
        exclude_sources: tuple[str, ...] | None = None,
    ) -> list[SearchHit]:
        terms = [term.lower() for term in _TOKEN.findall(query) if term.strip()]
        if not terms:
            raise ValidationError(
                "knowledge_query_empty", "The search query has no searchable terms"
            )
        limit = bounded_search_limit(limit, self.settings.knowledge_max_results)
        source_filters: list[ColumnElement[bool]] = []
        if sources:
            source_filters.append(Document.source.in_(sources))
        if exclude_sources:
            source_filters.append(Document.source.notin_(exclude_sources))
        base_filters = (
            DocumentChunk.organization_id == principal.organization_id,
            Document.organization_id == principal.organization_id,
            Document.deleted_at.is_(None),
            DocumentVersion.version == Document.current_version,
            _authorization_clause(principal),
            *source_filters,
        )
        dialect = session.get_bind().dialect.name
        started = perf_counter()
        try:
            if dialect == "postgresql":
                hits = await self._search_postgresql(
                    session,
                    principal,
                    query,
                    terms,
                    base_filters,
                    limit,
                )
            else:
                lexical_candidates = (
                    await session.execute(
                        select(DocumentChunk, DocumentVersion, Document)
                        .join(
                            DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id
                        )
                        .join(Document, Document.id == DocumentVersion.document_id)
                        .where(*base_filters)
                        .limit(self.settings.knowledge_max_candidates)
                    )
                ).all()
                scored: list[SearchHit] = []
                query_lower = query.lower()
                for chunk, version, document in lexical_candidates:
                    content_lower = chunk.content.lower()
                    term_score = sum(content_lower.count(term) for term in set(terms))
                    phrase_bonus = 4 if query_lower in content_lower else 0
                    coverage = sum(1 for term in set(terms) if term in content_lower) / len(
                        set(terms)
                    )
                    score = term_score + phrase_bonus + coverage * 3
                    if score <= 0:
                        continue
                    scored.append(
                        SearchHit(
                            chunk_id=chunk.id,
                            document_id=document.id,
                            version=version.version,
                            title=document.title,
                            source=document.source,
                            heading_path=chunk.heading_path,
                            content=chunk.content,
                            score=round(score, 4),
                            classification=chunk.classification,
                            **provenance_fields_from_version(
                                source=document.source,
                                external_id=document.external_id,
                                metadata=version.metadata_json
                                if isinstance(version.metadata_json, dict)
                                else None,
                            ),
                        )
                    )
                scored.sort(key=lambda hit: (-hit.score, hit.title, str(hit.chunk_id)))
                hits = scored[:limit]
            return hits
        finally:
            retrieval_duration.record((perf_counter() - started) * 1000, {"backend": dialect})

    async def _search_postgresql(
        self,
        session: AsyncSession,
        principal: Principal,
        query: str,
        terms: list[str],
        base_filters: tuple[ColumnElement[bool], ...],
        limit: int,
    ) -> list[SearchHit]:
        candidates = min(self.settings.knowledge_max_candidates, max(limit * 8, 50))
        language: ColumnElement[Any] = literal_column("'simple'::regconfig")
        # Natural questions commonly contain words absent from any one chunk. Use an
        # OR candidate query for recall, then apply deterministic term coverage below.
        unique_terms = list(dict.fromkeys(terms))[:32]
        search_query = func.to_tsquery(language, " | ".join(unique_terms))
        search_vector = func.to_tsvector(language, DocumentChunk.content)
        lexical_score = func.ts_rank_cd(search_vector, search_query).label("lexical_score")
        lexical_rows = (
            await session.execute(
                select(DocumentChunk, DocumentVersion, Document, lexical_score)
                .join(DocumentVersion, DocumentVersion.id == DocumentChunk.document_version_id)
                .join(Document, Document.id == DocumentVersion.document_id)
                .where(*base_filters, search_vector.op("@@")(search_query))
                .order_by(lexical_score.desc(), DocumentChunk.id)
                .limit(candidates)
            )
        ).all()

        vector_rows: list[Any] = []
        if self.settings.knowledge_embedding_profile:
            embedded = await self.model_gateway.embed(
                session,
                organization_id=principal.organization_id,
                profile_name=self.settings.knowledge_embedding_profile,
                texts=[query],
                classification=Classification.INTERNAL,
            )
            distance = DocumentChunk.embedding.cosine_distance(embedded.embeddings[0]).label(
                "distance"
            )
            vector_rows = list(
                (
                    await session.execute(
                        select(DocumentChunk, DocumentVersion, Document, distance)
                        .join(
                            DocumentVersion,
                            DocumentVersion.id == DocumentChunk.document_version_id,
                        )
                        .join(Document, Document.id == DocumentVersion.document_id)
                        .where(*base_filters, DocumentChunk.embedding.is_not(None))
                        .order_by(distance.asc(), DocumentChunk.id)
                        .limit(candidates)
                    )
                ).all()
            )

        ranked: dict[UUID, tuple[DocumentChunk, DocumentVersion, Document, float]] = {}
        for rank, (chunk, version, document, _) in enumerate(vector_rows, start=1):
            ranked[chunk.id] = (chunk, version, document, 1 / (60 + rank))
        for rank, (chunk, version, document, _) in enumerate(lexical_rows, start=1):
            prior = ranked.get(chunk.id)
            score = (prior[3] if prior else 0.0) + 1 / (60 + rank)
            ranked[chunk.id] = (chunk, version, document, score)

        query_lower = query.lower()
        term_set = set(terms)
        hits: list[SearchHit] = []
        for chunk, version, document, score in ranked.values():
            content_lower = chunk.content.lower()
            coverage = sum(term in content_lower for term in term_set) / len(term_set)
            reranked = score + coverage * 0.004 + (0.002 if query_lower in content_lower else 0)
            hits.append(
                SearchHit(
                    chunk_id=chunk.id,
                    document_id=document.id,
                    version=version.version,
                    title=document.title,
                    source=document.source,
                    heading_path=chunk.heading_path,
                    content=chunk.content,
                    score=round(reranked, 6),
                    classification=chunk.classification,
                    **provenance_fields_from_version(
                        source=document.source,
                        external_id=document.external_id,
                        metadata=version.metadata_json
                        if isinstance(version.metadata_json, dict)
                        else None,
                    ),
                )
            )
        hits.sort(key=lambda hit: (-hit.score, hit.title, str(hit.chunk_id)))
        return hits[:limit]

    async def get_document(
        self, session: AsyncSession, principal: Principal, document_id: UUID
    ) -> tuple[Document, DocumentVersion]:
        row = (
            await session.execute(
                select(Document, DocumentVersion)
                .join(
                    DocumentVersion,
                    (DocumentVersion.document_id == Document.id)
                    & (DocumentVersion.version == Document.current_version),
                )
                .where(
                    Document.id == document_id,
                    Document.organization_id == principal.organization_id,
                    Document.deleted_at.is_(None),
                )
            )
        ).one_or_none()
        if row is None:
            raise NotFoundError("Document", document_id)
        document, version = row._tuple()
        if not _authorized(principal, document.classification, document.acl):
            raise NotFoundError("Document", document_id)
        return document, version

    async def get_content(
        self, session: AsyncSession, principal: Principal, document_id: UUID
    ) -> tuple[Document, DocumentVersion, StoredObject]:
        document, version = await self.get_document(session, principal, document_id)
        if version.content_ref is None:
            raise NotFoundError("Document content", document_id)
        stored = await self.store.get(version.content_ref)
        if hashlib.sha256(stored.data).hexdigest() != version.checksum_sha256:
            raise ObsionError(
                "document_integrity_failed",
                "Document content failed integrity verification",
                status_code=503,
            )
        return document, version, stored
