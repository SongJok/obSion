import json
from contextlib import suppress
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import (
    DocumentIngestedView,
    DocumentView,
    KnowledgeHitView,
    KnowledgeSearchRequest,
)
from obsion.common.errors import ObsionError, ValidationError
from obsion.config import Settings
from obsion.domain.enums import Classification
from obsion.knowledge.service import KnowledgeService
from obsion.security.auth import get_app_settings, get_principal, get_session
from obsion.security.identity import Principal

router = APIRouter(tags=["knowledge"])


def _service(request: Request, settings: Settings = Depends(get_app_settings)) -> KnowledgeService:
    return KnowledgeService(settings, request.app.state.object_store)


@router.post(
    "/knowledge/documents",
    response_model=DocumentIngestedView,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_document(
    file: UploadFile = File(),
    source: str = Form(min_length=1, max_length=200),
    external_id: str = Form(min_length=1, max_length=500),
    title: str = Form(min_length=1, max_length=500),
    classification: Classification = Form(default=Classification.INTERNAL),
    acl: str = Form(default='{"organization": true}'),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: KnowledgeService = Depends(_service),
) -> DocumentIngestedView:
    try:
        parsed_acl = json.loads(acl)
        if not isinstance(parsed_acl, dict):
            raise ValueError
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValidationError("document_acl_invalid", "Document ACL must be a JSON object") from exc
    content = await file.read()
    version = None
    try:
        async with session.begin():
            document, version, count = await service.ingest(
                session,
                principal,
                source=source,
                external_id=external_id,
                title=title,
                media_type=file.content_type or "application/octet-stream",
                filename=file.filename or external_id,
                content=content,
                classification=classification,
                acl=parsed_acl,
            )
    except Exception:
        if version is not None and version.content_ref is not None:
            with suppress(ObsionError):
                await service.store.delete(version.content_ref)
        raise
    return DocumentIngestedView(
        document=DocumentView.model_validate(document),
        version_id=version.id,
        version=version.version,
        chunk_count=count,
    )


@router.post("/knowledge/search", response_model=list[KnowledgeHitView])
async def search_knowledge(
    request: KnowledgeSearchRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: KnowledgeService = Depends(_service),
) -> list[KnowledgeHitView]:
    hits = await service.search(session, principal, request.query, limit=request.limit)
    return [KnowledgeHitView.model_validate(hit) for hit in hits]


@router.get("/knowledge/documents/{document_id}", response_model=DocumentView)
async def get_document(
    document_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: KnowledgeService = Depends(_service),
) -> DocumentView:
    document, _ = await service.get_document(session, principal, document_id)
    return DocumentView.model_validate(document)


@router.get("/knowledge/documents/{document_id}/content")
async def download_document(
    document_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: KnowledgeService = Depends(_service),
) -> Response:
    document, version, stored = await service.get_content(session, principal, document_id)
    return Response(
        content=stored.data,
        media_type=version.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="document-{document.id}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "ETag": f'"{version.checksum_sha256}"',
        },
    )
