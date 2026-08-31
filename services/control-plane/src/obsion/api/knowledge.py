import json
from contextlib import suppress
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.dependencies import get_capability_gateway
from obsion.api.schemas import (
    ConfluencePageIngestedView,
    ConfluencePageIngestRequest,
    ConfluenceSpacePageView,
    ConfluenceSpaceSyncRequest,
    ConfluenceSpaceSyncView,
    ConfluenceSpaceView,
    DingTalkDocumentIngestedView,
    DingTalkDocumentIngestRequest,
    DingTalkWorkspaceNodeView,
    DingTalkWorkspaceSyncRequest,
    DingTalkWorkspaceSyncView,
    DingTalkWorkspaceView,
    DocumentIngestedView,
    DocumentView,
    FeishuDocumentIngestedView,
    FeishuDocumentIngestRequest,
    FeishuSpaceSyncRequest,
    FeishuSpaceSyncView,
    FeishuWikiNodeView,
    FeishuWikiSpaceView,
    KnowledgeHitView,
    KnowledgeSearchRequest,
    WeComDocumentIngestedView,
    WeComDocumentIngestRequest,
    WeComSpaceNodeView,
    WeComSpaceSyncRequest,
    WeComSpaceSyncView,
    WeComSpaceView,
)
from obsion.capabilities.gateway import (
    CapabilityGateway,
    GatewayResult,
    GatewayStatus,
    OperatorGatewayRequest,
)
from obsion.capabilities.vendor_knowledge import (
    KNOWLEDGE_SOURCE_CONTAINERS,
    KNOWLEDGE_SOURCE_ITEMS,
)
from obsion.common.errors import ObsionError, ValidationError
from obsion.common.ids import new_id
from obsion.config import Environment, Settings
from obsion.contracts.errors.catalog import get_error_code
from obsion.db.models import Document
from obsion.domain.enums import Classification
from obsion.knowledge.service import KnowledgeService
from obsion.security.auth import get_app_settings, get_principal, get_session
from obsion.security.identity import Principal

router = APIRouter(tags=["knowledge"])


def _service(request: Request, settings: Settings = Depends(get_app_settings)) -> KnowledgeService:
    return KnowledgeService(settings, request.app.state.object_store)


def _operator_correlation_id(request: Request) -> UUID:
    value = getattr(request.state, "correlation_id", None)
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as exc:
            raise ValidationError(
                "capability_input_invalid",
                "Vendor Knowledge source operations require a UUID X-Request-ID",
            ) from exc
    return new_id()


async def _invoke_vendor_write(
    request: Request,
    session: AsyncSession,
    principal: Principal,
    gateway: CapabilityGateway,
    *,
    capability_name: str,
    source: str,
    payload: dict[str, Any],
    resource: dict[str, Any],
) -> dict[str, Any]:
    return await _invoke_vendor_operator(
        request,
        session,
        principal,
        gateway,
        capability_name=capability_name,
        source=source,
        payload=payload,
        resource=resource,
    )


async def _invoke_vendor_browse(
    request: Request,
    session: AsyncSession,
    principal: Principal,
    gateway: CapabilityGateway,
    *,
    capability_name: str,
    source: str,
    container_id: str | None = None,
) -> dict[str, Any]:
    payload = {"operation": capability_name}
    resource: dict[str, Any] = {}
    if container_id is not None:
        payload["container_id"] = container_id
        resource["container_id"] = container_id
    return await _invoke_vendor_operator(
        request,
        session,
        principal,
        gateway,
        capability_name=capability_name,
        source=source,
        payload=payload,
        resource=resource,
    )


async def _invoke_vendor_operator(
    request: Request,
    session: AsyncSession,
    principal: Principal,
    gateway: CapabilityGateway,
    *,
    capability_name: str,
    source: str,
    payload: dict[str, Any],
    resource: dict[str, Any],
) -> dict[str, Any]:
    result = await gateway.invoke_operator(
        session,
        OperatorGatewayRequest(
            principal=principal,
            capability_name=capability_name,
            payload=payload,
            resource={"index": "organization", "source": source, **resource},
            environment=_operator_connector_environment(request.app.state.settings),
            correlation_id=_operator_correlation_id(request),
            context={"surface": "vendor-knowledge-rest"},
        ),
    )
    return _operator_gateway_output(result)


def _operator_connector_environment(settings: Settings) -> str:
    # Test applications intentionally reuse the seeded development adapters. No
    # staging or production environment may fall back across a connector boundary.
    if settings.environment == Environment.TEST:
        return Environment.DEVELOPMENT.value
    return settings.environment.value


def _operator_gateway_output(result: GatewayResult) -> dict[str, Any]:
    if result.status == GatewayStatus.COMPLETED and result.output is not None:
        return result.output
    code = result.error_code or "capability_failed"
    definition = get_error_code(code)
    fallback_status = 500
    if code == "capability_input_invalid":
        fallback_status = 422
    elif code == "capability_rate_limited":
        fallback_status = 429
    elif result.status == GatewayStatus.DENIED:
        fallback_status = 403
    elif code in {
        "capability_failed",
        "capability_timeout",
        "capability_transport_unavailable",
        "rate_limit_unavailable",
    }:
        fallback_status = 503
    raise ObsionError(
        code=code,
        message=result.error_message or "The operator Capability request failed",
        status_code=definition.http_status or fallback_status,
    )


async def _ingested_document(
    session: AsyncSession,
    principal: Principal,
    output: dict[str, Any],
) -> Document:
    document = await session.get(Document, UUID(str(output["document_id"])))
    if document is None or document.organization_id != principal.organization_id:
        raise RuntimeError("Gateway returned an unavailable Knowledge document")
    return document


def _ingest_payload(
    *,
    document_id: str,
    classification: Classification,
    acl: dict[str, Any],
    inherit_acl: bool,
    title: str | None = None,
    obj_type: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "operation": "knowledge.ingest",
        "document_id": document_id,
        "classification": classification.value,
        "inherit_acl": inherit_acl,
    }
    if acl:
        payload["acl"] = acl
    if title is not None:
        payload["title"] = title
    if obj_type is not None:
        payload["obj_type"] = obj_type
    return payload


def _sync_payload(
    *,
    space_id: str,
    classification: Classification,
    acl: dict[str, Any],
    inherit_acl: bool,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "operation": "knowledge.sync",
        "space_id": space_id,
        "classification": classification.value,
        "inherit_acl": inherit_acl,
    }
    if acl:
        payload["acl"] = acl
    return payload


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


@router.post(
    "/knowledge/sources/feishu/documents",
    response_model=FeishuDocumentIngestedView,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_feishu_source_document(
    payload: FeishuDocumentIngestRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    gateway: CapabilityGateway = Depends(get_capability_gateway),
) -> FeishuDocumentIngestedView:
    output = await _invoke_vendor_write(
        request,
        session,
        principal,
        gateway,
        capability_name="knowledge.ingest",
        source="feishu",
        payload=_ingest_payload(
            document_id=payload.document_id,
            obj_type=payload.obj_type,
            title=payload.title,
            classification=payload.classification,
            acl=payload.acl,
            inherit_acl=payload.inherit_acl,
        ),
        resource={"document_id": payload.document_id},
    )
    document = await _ingested_document(session, principal, output)
    return FeishuDocumentIngestedView(
        document=DocumentView.model_validate(document),
        version_id=UUID(str(output["version_id"])),
        version=int(output["version"]),
        chunk_count=int(output["chunk_count"]),
        source=str(output["source"]),
        external_id=str(output["external_id"]),
        revision_id=(str(output["revision_id"]) if output.get("revision_id") is not None else None),
        obj_type=str(output["obj_type"]),
    )


@router.get(
    "/knowledge/sources/feishu/spaces",
    response_model=list[FeishuWikiSpaceView],
)
async def list_feishu_source_spaces(
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    gateway: CapabilityGateway = Depends(get_capability_gateway),
) -> list[FeishuWikiSpaceView]:
    output = await _invoke_vendor_browse(
        request,
        session,
        principal,
        gateway,
        capability_name=KNOWLEDGE_SOURCE_CONTAINERS,
        source="feishu",
    )
    return [
        FeishuWikiSpaceView(
            space_id=str(space["container_id"]),
            name=str(space["name"]),
            description=str(space["description"]),
        )
        for space in output["containers"]
    ]


@router.get(
    "/knowledge/sources/feishu/spaces/{space_id}/nodes",
    response_model=list[FeishuWikiNodeView],
)
async def list_feishu_source_nodes(
    space_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    gateway: CapabilityGateway = Depends(get_capability_gateway),
) -> list[FeishuWikiNodeView]:
    output = await _invoke_vendor_browse(
        request,
        session,
        principal,
        gateway,
        capability_name=KNOWLEDGE_SOURCE_ITEMS,
        source="feishu",
        container_id=space_id,
    )
    return [
        FeishuWikiNodeView(
            space_id=str(node["container_id"]),
            node_token=str(node["item_id"]),
            obj_token=str(node["document_id"]),
            obj_type=str(node["item_type"]),
            title=str(node["title"]),
        )
        for node in output["items"]
    ]


@router.post(
    "/knowledge/sources/feishu/spaces/{space_id}/sync",
    response_model=FeishuSpaceSyncView,
    status_code=status.HTTP_201_CREATED,
)
async def sync_feishu_source_space(
    space_id: str,
    payload: FeishuSpaceSyncRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    gateway: CapabilityGateway = Depends(get_capability_gateway),
) -> FeishuSpaceSyncView:
    output = await _invoke_vendor_write(
        request,
        session,
        principal,
        gateway,
        capability_name="knowledge.sync",
        source="feishu",
        payload=_sync_payload(
            space_id=space_id,
            classification=payload.classification,
            acl=payload.acl,
            inherit_acl=payload.inherit_acl,
        ),
        resource={"space_id": space_id},
    )
    return FeishuSpaceSyncView.model_validate(output)


@router.post(
    "/knowledge/sources/dingtalk/documents",
    response_model=DingTalkDocumentIngestedView,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_dingtalk_source_document(
    payload: DingTalkDocumentIngestRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    gateway: CapabilityGateway = Depends(get_capability_gateway),
) -> DingTalkDocumentIngestedView:
    output = await _invoke_vendor_write(
        request,
        session,
        principal,
        gateway,
        capability_name="knowledge.ingest",
        source="dingtalk",
        payload=_ingest_payload(
            document_id=payload.document_id,
            title=payload.title,
            classification=payload.classification,
            acl=payload.acl,
            inherit_acl=payload.inherit_acl,
        ),
        resource={"document_id": payload.document_id},
    )
    document = await _ingested_document(session, principal, output)
    return DingTalkDocumentIngestedView(
        document=DocumentView.model_validate(document),
        version_id=UUID(str(output["version_id"])),
        version=int(output["version"]),
        chunk_count=int(output["chunk_count"]),
        source=str(output["source"]),
        external_id=str(output["external_id"]),
        revision_id=(str(output["revision_id"]) if output.get("revision_id") is not None else None),
        workspace_id=(
            str(output["workspace_id"]) if output.get("workspace_id") is not None else None
        ),
    )


@router.get(
    "/knowledge/sources/dingtalk/workspaces",
    response_model=list[DingTalkWorkspaceView],
)
async def list_dingtalk_source_workspaces(
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    gateway: CapabilityGateway = Depends(get_capability_gateway),
) -> list[DingTalkWorkspaceView]:
    output = await _invoke_vendor_browse(
        request,
        session,
        principal,
        gateway,
        capability_name=KNOWLEDGE_SOURCE_CONTAINERS,
        source="dingtalk",
    )
    return [
        DingTalkWorkspaceView(
            workspace_id=str(space["container_id"]),
            name=str(space["name"]),
            description=str(space["description"]),
        )
        for space in output["containers"]
    ]


@router.get(
    "/knowledge/sources/dingtalk/workspaces/{workspace_id}/nodes",
    response_model=list[DingTalkWorkspaceNodeView],
)
async def list_dingtalk_source_nodes(
    workspace_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    gateway: CapabilityGateway = Depends(get_capability_gateway),
) -> list[DingTalkWorkspaceNodeView]:
    output = await _invoke_vendor_browse(
        request,
        session,
        principal,
        gateway,
        capability_name=KNOWLEDGE_SOURCE_ITEMS,
        source="dingtalk",
        container_id=workspace_id,
    )
    return [
        DingTalkWorkspaceNodeView(
            workspace_id=str(node["container_id"]),
            node_id=str(node["item_id"]),
            document_id=str(node["document_id"]),
            node_type=str(node["item_type"]),
            title=str(node["title"]),
        )
        for node in output["items"]
    ]


@router.post(
    "/knowledge/sources/dingtalk/workspaces/{workspace_id}/sync",
    response_model=DingTalkWorkspaceSyncView,
)
async def sync_dingtalk_source_workspace(
    workspace_id: str,
    payload: DingTalkWorkspaceSyncRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    gateway: CapabilityGateway = Depends(get_capability_gateway),
) -> DingTalkWorkspaceSyncView:
    output = await _invoke_vendor_write(
        request,
        session,
        principal,
        gateway,
        capability_name="knowledge.sync",
        source="dingtalk",
        payload=_sync_payload(
            space_id=workspace_id,
            classification=payload.classification,
            acl=payload.acl,
            inherit_acl=payload.inherit_acl,
        ),
        resource={"space_id": workspace_id},
    )
    return DingTalkWorkspaceSyncView.model_validate(output)


@router.post(
    "/knowledge/sources/wecom/documents",
    response_model=WeComDocumentIngestedView,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_wecom_source_document(
    payload: WeComDocumentIngestRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    gateway: CapabilityGateway = Depends(get_capability_gateway),
) -> WeComDocumentIngestedView:
    output = await _invoke_vendor_write(
        request,
        session,
        principal,
        gateway,
        capability_name="knowledge.ingest",
        source="wecom",
        payload=_ingest_payload(
            document_id=payload.document_id,
            title=payload.title,
            classification=payload.classification,
            acl=payload.acl,
            inherit_acl=payload.inherit_acl,
        ),
        resource={"document_id": payload.document_id},
    )
    document = await _ingested_document(session, principal, output)
    return WeComDocumentIngestedView(
        document=DocumentView.model_validate(document),
        version_id=UUID(str(output["version_id"])),
        version=int(output["version"]),
        chunk_count=int(output["chunk_count"]),
        source=str(output["source"]),
        external_id=str(output["external_id"]),
        revision_id=(str(output["revision_id"]) if output.get("revision_id") is not None else None),
        space_id=(str(output["space_id"]) if output.get("space_id") is not None else None),
    )


@router.get(
    "/knowledge/sources/wecom/spaces/{space_id}",
    response_model=WeComSpaceView,
)
async def get_wecom_source_space(
    space_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    gateway: CapabilityGateway = Depends(get_capability_gateway),
) -> WeComSpaceView:
    output = await _invoke_vendor_browse(
        request,
        session,
        principal,
        gateway,
        capability_name=KNOWLEDGE_SOURCE_CONTAINERS,
        source="wecom",
        container_id=space_id,
    )
    space = output["containers"][0]
    return WeComSpaceView(
        space_id=str(space["container_id"]),
        name=str(space["name"]),
        description=str(space["description"]),
    )


@router.get(
    "/knowledge/sources/wecom/spaces/{space_id}/nodes",
    response_model=list[WeComSpaceNodeView],
)
async def list_wecom_source_nodes(
    space_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    gateway: CapabilityGateway = Depends(get_capability_gateway),
) -> list[WeComSpaceNodeView]:
    output = await _invoke_vendor_browse(
        request,
        session,
        principal,
        gateway,
        capability_name=KNOWLEDGE_SOURCE_ITEMS,
        source="wecom",
        container_id=space_id,
    )
    return [
        WeComSpaceNodeView(
            space_id=str(node["container_id"]),
            node_id=str(node["item_id"]),
            document_id=str(node["document_id"]),
            node_type=str(node["item_type"]),
            title=str(node["title"]),
        )
        for node in output["items"]
    ]


@router.post(
    "/knowledge/sources/wecom/spaces/{space_id}/sync",
    response_model=WeComSpaceSyncView,
)
async def sync_wecom_source_space(
    space_id: str,
    payload: WeComSpaceSyncRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    gateway: CapabilityGateway = Depends(get_capability_gateway),
) -> WeComSpaceSyncView:
    output = await _invoke_vendor_write(
        request,
        session,
        principal,
        gateway,
        capability_name="knowledge.sync",
        source="wecom",
        payload=_sync_payload(
            space_id=space_id,
            classification=payload.classification,
            acl=payload.acl,
            inherit_acl=payload.inherit_acl,
        ),
        resource={"space_id": space_id},
    )
    return WeComSpaceSyncView.model_validate(output)


@router.post(
    "/knowledge/sources/confluence/pages",
    response_model=ConfluencePageIngestedView,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_confluence_source_page(
    payload: ConfluencePageIngestRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    gateway: CapabilityGateway = Depends(get_capability_gateway),
) -> ConfluencePageIngestedView:
    output = await _invoke_vendor_write(
        request,
        session,
        principal,
        gateway,
        capability_name="knowledge.ingest",
        source="confluence",
        payload=_ingest_payload(
            document_id=payload.page_id,
            title=payload.title,
            classification=payload.classification,
            acl=payload.acl,
            inherit_acl=payload.inherit_acl,
        ),
        resource={"page_id": payload.page_id},
    )
    document = await _ingested_document(session, principal, output)
    return ConfluencePageIngestedView(
        document=DocumentView.model_validate(document),
        version_id=UUID(str(output["version_id"])),
        version=int(output["version"]),
        chunk_count=int(output["chunk_count"]),
        source=str(output["source"]),
        external_id=str(output["external_id"]),
        revision_id=(str(output["revision_id"]) if output.get("revision_id") is not None else None),
        space_id=(str(output["space_id"]) if output.get("space_id") is not None else None),
    )


@router.get(
    "/knowledge/sources/confluence/spaces",
    response_model=list[ConfluenceSpaceView],
)
async def list_confluence_source_spaces(
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    gateway: CapabilityGateway = Depends(get_capability_gateway),
) -> list[ConfluenceSpaceView]:
    output = await _invoke_vendor_browse(
        request,
        session,
        principal,
        gateway,
        capability_name=KNOWLEDGE_SOURCE_CONTAINERS,
        source="confluence",
    )
    return [
        ConfluenceSpaceView(
            space_id=str(space["container_id"]),
            key=str(space["key"]),
            name=str(space["name"]),
        )
        for space in output["containers"]
    ]


@router.get(
    "/knowledge/sources/confluence/spaces/{space_id}/pages",
    response_model=list[ConfluenceSpacePageView],
)
async def list_confluence_source_pages(
    space_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    gateway: CapabilityGateway = Depends(get_capability_gateway),
) -> list[ConfluenceSpacePageView]:
    output = await _invoke_vendor_browse(
        request,
        session,
        principal,
        gateway,
        capability_name=KNOWLEDGE_SOURCE_ITEMS,
        source="confluence",
        container_id=space_id,
    )
    return [
        ConfluenceSpacePageView(
            space_id=str(page["container_id"]),
            page_id=str(page["document_id"]),
            title=str(page["title"]),
            status=str(page["status"]),
        )
        for page in output["items"]
    ]


@router.post(
    "/knowledge/sources/confluence/spaces/{space_id}/sync",
    response_model=ConfluenceSpaceSyncView,
    status_code=status.HTTP_201_CREATED,
)
async def sync_confluence_source_space(
    space_id: str,
    payload: ConfluenceSpaceSyncRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    gateway: CapabilityGateway = Depends(get_capability_gateway),
) -> ConfluenceSpaceSyncView:
    output = await _invoke_vendor_write(
        request,
        session,
        principal,
        gateway,
        capability_name="knowledge.sync",
        source="confluence",
        payload=_sync_payload(
            space_id=space_id,
            classification=payload.classification,
            acl=payload.acl,
            inherit_acl=payload.inherit_acl,
        ),
        resource={"space_id": space_id},
    )
    return ConfluenceSpaceSyncView.model_validate(output)


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
