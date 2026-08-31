"""Operator and Capability ingest of Feishu documents into Organization Knowledge."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.capabilities.feishu_docs import (
    FEISHU_KNOWLEDGE_SOURCE,
    FeishuDocsClient,
    FeishuDocument,
    FeishuWikiNode,
    FeishuWikiSpace,
    assert_feishu_docs_egress,
    fetch_authorized_feishu_document,
    is_feishu_docs_connector,
    normalize_document_id,
    normalize_obj_type,
    normalize_space_id,
    resolve_feishu_docs_credentials,
)
from obsion.common.errors import AuthorizationError, NotFoundError, ObsionError, ValidationError
from obsion.db.models import Connector, Document, DocumentVersion
from obsion.domain.enums import Classification, ConnectorStatus
from obsion.knowledge.connector_contract import (
    KnowledgeConnectorBudget,
    SyncBudgetTracker,
    VendorKnowledgeProvenance,
    attach_ingest_provenance,
    attach_sync_result_envelope,
)
from obsion.knowledge.service import KnowledgeService, validate_document_acl
from obsion.security.identity import Principal


def resolve_ingest_acl(
    *,
    requested: dict[str, Any] | None,
    inherited: dict[str, Any] | None,
    inherit_acl: bool,
) -> dict[str, Any]:
    if inherit_acl:
        if not inherited:
            raise ValidationError(
                "document_acl_required",
                "Feishu permission inheritance did not produce an explicit ACL",
            )
        merged = validate_document_acl(inherited)
        if requested:
            extra = validate_document_acl(requested)
            merged = validate_document_acl(
                {
                    "organization": bool(extra["organization"] or merged["organization"]),
                    "users": [*merged["users"], *extra["users"]],
                    "roles": [*merged["roles"], *extra["roles"]],
                    "departments": [*merged["departments"], *extra["departments"]],
                    "deny_users": [*merged["deny_users"], *extra["deny_users"]],
                    "deny_roles": [*merged["deny_roles"], *extra["deny_roles"]],
                    "deny_departments": [*merged["deny_departments"], *extra["deny_departments"]],
                }
            )
        return merged
    if not requested:
        raise ValidationError("document_acl_required", "An explicit document ACL is required")
    return validate_document_acl(requested)


async def resolve_feishu_docs_connector(
    session: AsyncSession,
    organization_id: UUID,
    *,
    connector_id: UUID | None = None,
) -> Connector:
    statement = select(Connector).where(
        Connector.organization_id == organization_id,
        Connector.status == ConnectorStatus.ACTIVE,
    )
    if connector_id is not None:
        statement = statement.where(Connector.id == connector_id)
    else:
        statement = statement.where(Connector.name == "obsion-feishu-docs")
    connector = await session.scalar(statement)
    if connector is None or not is_feishu_docs_connector(connector):
        raise NotFoundError("Connector", connector_id or "obsion-feishu-docs")
    return connector


async def ingest_feishu_document(
    session: AsyncSession,
    principal: Principal,
    service: KnowledgeService,
    *,
    document_id: str,
    obj_type: str = "auto",
    title: str | None = None,
    classification: Classification = Classification.INTERNAL,
    acl: dict[str, Any] | None = None,
    inherit_acl: bool = False,
    connector: Connector | None = None,
    credential: str | None = None,
    transport: Any = None,
    extra_metadata: dict[str, Any] | None = None,
) -> tuple[Document, DocumentVersion, int, FeishuDocument]:
    token = normalize_document_id(document_id)
    resolved_type = normalize_obj_type(obj_type, document_id=token)
    connector = connector or await resolve_feishu_docs_connector(session, principal.organization_id)
    if not is_feishu_docs_connector(connector):
        raise ValidationError(
            "feishu_docs_operation_invalid",
            "The connector is not a Feishu docs Knowledge source",
        )
    assert_feishu_docs_egress(connector)
    app_id, app_secret = resolve_feishu_docs_credentials(connector, credential)
    fetched = await fetch_authorized_feishu_document(
        app_id=app_id,
        app_secret=app_secret,
        document_id=token,
        obj_type=resolved_type,
        inherit_acl=inherit_acl,
        transport=transport,
    )
    resolved_acl = resolve_ingest_acl(
        requested=acl,
        inherited=fetched.inherited_acl,
        inherit_acl=inherit_acl,
    )
    document, version, count = await service.ingest(
        session,
        principal,
        source=FEISHU_KNOWLEDGE_SOURCE,
        external_id=fetched.document_id,
        title=(title or fetched.title).strip() or fetched.document_id,
        media_type="text/plain",
        filename=f"{fetched.document_id}.txt",
        content=fetched.content.encode("utf-8"),
        classification=classification,
        acl=resolved_acl,
        extra_metadata=attach_ingest_provenance(
            extra_metadata={**fetched.as_metadata(), **(extra_metadata or {})},
            provenance=VendorKnowledgeProvenance(
                source=FEISHU_KNOWLEDGE_SOURCE,
                external_id=fetched.document_id,
                revision_id=fetched.revision_id,
                connector_name=connector.name,
                connector_id=str(connector.id),
                operation="knowledge.ingest",
                sync_scope_id=None,
            ),
        ),
    )
    return document, version, count, fetched


def ingest_result_payload(
    document: Document,
    version: DocumentVersion,
    chunk_count: int,
    fetched: FeishuDocument,
) -> dict[str, Any]:
    return {
        "document_id": str(document.id),
        "version_id": str(version.id),
        "source": document.source,
        "external_id": document.external_id,
        "title": document.title,
        "version": version.version,
        "chunk_count": chunk_count,
        "revision_id": fetched.revision_id,
        "obj_type": fetched.obj_type,
        "operation": "knowledge.ingest",
    }


def _feishu_client(
    connector: Connector, credential: str | None, transport: Any
) -> FeishuDocsClient:
    app_id, app_secret = resolve_feishu_docs_credentials(connector, credential)
    return FeishuDocsClient(app_id=app_id, app_secret=app_secret, transport=transport)


def _require_knowledge_write(principal: Principal) -> None:
    if not principal.can("knowledge.write"):
        raise AuthorizationError("knowledge_write_denied", "Document ingestion is not permitted")


async def list_feishu_spaces(
    session: AsyncSession,
    principal: Principal,
    *,
    connector: Connector | None = None,
    credential: str | None = None,
    transport: Any = None,
    tracker: SyncBudgetTracker | None = None,
) -> list[FeishuWikiSpace]:
    _require_knowledge_write(principal)
    connector = connector or await resolve_feishu_docs_connector(session, principal.organization_id)
    assert_feishu_docs_egress(connector)
    client = _feishu_client(connector, credential, transport)
    try:
        return await client.list_spaces(tracker=tracker)
    finally:
        await client.aclose()


async def list_feishu_nodes(
    session: AsyncSession,
    principal: Principal,
    space_id: str,
    *,
    connector: Connector | None = None,
    credential: str | None = None,
    transport: Any = None,
    tracker: SyncBudgetTracker | None = None,
) -> list[FeishuWikiNode]:
    _require_knowledge_write(principal)
    connector = connector or await resolve_feishu_docs_connector(session, principal.organization_id)
    assert_feishu_docs_egress(connector)
    client = _feishu_client(connector, credential, transport)
    try:
        return await client.list_nodes(space_id, tracker=tracker)
    finally:
        await client.aclose()


async def sync_feishu_space(
    session: AsyncSession,
    principal: Principal,
    service: KnowledgeService,
    *,
    space_id: str,
    classification: Classification = Classification.INTERNAL,
    acl: dict[str, Any] | None = None,
    inherit_acl: bool = False,
    connector: Connector | None = None,
    credential: str | None = None,
    transport: Any = None,
) -> dict[str, Any]:
    _require_knowledge_write(principal)
    resolved_space = normalize_space_id(space_id)
    connector = connector or await resolve_feishu_docs_connector(session, principal.organization_id)
    budget = KnowledgeConnectorBudget.from_connector(connector)
    tracker = SyncBudgetTracker(budget)
    nodes = await list_feishu_nodes(
        session,
        principal,
        resolved_space,
        connector=connector,
        credential=credential,
        transport=transport,
        tracker=tracker,
    )
    ingested: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for node in nodes:
        if node.obj_type != "docx":
            skipped.append(
                {
                    "node_token": node.node_token,
                    "obj_type": node.obj_type,
                    "title": node.title,
                    "reason": "feishu_docs_obj_type_unsupported",
                }
            )
            continue
        try:
            document, version, count, fetched = await _ingest_space_node(
                session,
                principal,
                service,
                node=node,
                space_id=resolved_space,
                classification=classification,
                acl=acl,
                inherit_acl=inherit_acl,
                connector=connector,
                credential=credential,
                transport=transport,
            )
            payload = ingest_result_payload(document, version, count, fetched)
            payload["node_token"] = node.node_token
            payload["space_id"] = resolved_space
            payload["connector_name"] = connector.name
            ingested.append(payload)
        except ObsionError as exc:
            failed.append(
                {
                    "node_token": node.node_token,
                    "obj_token": node.obj_token,
                    "title": node.title,
                    "error_code": exc.code,
                }
            )
    return attach_sync_result_envelope(
        result={
            "operation": "knowledge.sync",
            "space_id": resolved_space,
            "ingested": ingested,
            "skipped": skipped,
            "failed": failed,
            "ingested_count": len(ingested),
            "skipped_count": len(skipped),
            "failed_count": len(failed),
        },
        budget=tracker.snapshot(),
        provenance=VendorKnowledgeProvenance(
            source=FEISHU_KNOWLEDGE_SOURCE,
            external_id=resolved_space,
            revision_id=None,
            connector_name=connector.name,
            connector_id=str(connector.id),
            operation="knowledge.sync",
            sync_scope_id=resolved_space,
        ),
    )


async def _ingest_space_node(
    session: AsyncSession,
    principal: Principal,
    service: KnowledgeService,
    *,
    node: FeishuWikiNode,
    space_id: str,
    classification: Classification,
    acl: dict[str, Any] | None,
    inherit_acl: bool,
    connector: Connector,
    credential: str | None,
    transport: Any,
) -> tuple[Document, DocumentVersion, int, FeishuDocument]:
    extra_metadata = {
        "feishu_space_id": space_id,
        "feishu_node_token": node.node_token,
    }
    if hasattr(session, "begin_nested"):
        async with session.begin_nested():
            return await ingest_feishu_document(
                session,
                principal,
                service,
                document_id=node.obj_token,
                obj_type="docx",
                title=node.title,
                classification=classification,
                acl=acl,
                inherit_acl=inherit_acl,
                connector=connector,
                credential=credential,
                transport=transport,
                extra_metadata=extra_metadata,
            )
    return await ingest_feishu_document(
        session,
        principal,
        service,
        document_id=node.obj_token,
        obj_type="docx",
        title=node.title,
        classification=classification,
        acl=acl,
        inherit_acl=inherit_acl,
        connector=connector,
        credential=credential,
        transport=transport,
        extra_metadata=extra_metadata,
    )
