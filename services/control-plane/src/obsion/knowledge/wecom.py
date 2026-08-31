"""Operator and Capability ingest of WeCom documents into Organization Knowledge."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.capabilities.wecom_docs import (
    WECOM_KNOWLEDGE_SOURCE,
    WeComDocsClient,
    WeComDocument,
    WeComSpace,
    WeComSpaceNode,
    assert_wecom_docs_egress,
    fetch_authorized_wecom_document,
    is_wecom_docs_connector,
    normalize_document_id,
    normalize_space_id,
    resolve_wecom_docs_credentials,
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
                "WeCom permission inheritance did not produce an explicit ACL",
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


async def resolve_wecom_docs_connector(
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
        statement = statement.where(Connector.name == "obsion-wecom-docs")
    connector = await session.scalar(statement)
    if connector is None or not is_wecom_docs_connector(connector):
        raise NotFoundError("Connector", connector_id or "obsion-wecom-docs")
    return connector


def _require_knowledge_write(principal: Principal) -> None:
    if not principal.can("knowledge.write"):
        raise AuthorizationError("knowledge_write_denied", "Document ingestion is not permitted")


def _client(connector: Connector, credential: str | None, transport: Any) -> WeComDocsClient:
    corp_id, corp_secret = resolve_wecom_docs_credentials(connector, credential)
    return WeComDocsClient(corp_id=corp_id, corp_secret=corp_secret, transport=transport)


async def ingest_wecom_document(
    session: AsyncSession,
    principal: Principal,
    service: KnowledgeService,
    *,
    document_id: str,
    title: str | None = None,
    classification: Classification = Classification.INTERNAL,
    acl: dict[str, Any] | None = None,
    inherit_acl: bool = False,
    connector: Connector | None = None,
    credential: str | None = None,
    transport: Any = None,
    extra_metadata: dict[str, Any] | None = None,
) -> tuple[Document, DocumentVersion, int, WeComDocument]:
    _require_knowledge_write(principal)
    token = normalize_document_id(document_id)
    connector = connector or await resolve_wecom_docs_connector(session, principal.organization_id)
    if not is_wecom_docs_connector(connector):
        raise ValidationError(
            "wecom_docs_operation_invalid",
            "The connector is not a WeCom docs Knowledge source",
        )
    assert_wecom_docs_egress(connector)
    corp_id, corp_secret = resolve_wecom_docs_credentials(connector, credential)
    fetched = await fetch_authorized_wecom_document(
        corp_id=corp_id,
        corp_secret=corp_secret,
        document_id=token,
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
        source=WECOM_KNOWLEDGE_SOURCE,
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
                source=WECOM_KNOWLEDGE_SOURCE,
                external_id=fetched.document_id,
                revision_id=fetched.revision_id,
                connector_name=connector.name,
                connector_id=str(connector.id),
                operation="knowledge.ingest",
                sync_scope_id=fetched.space_id,
            ),
        ),
    )
    return document, version, count, fetched


def ingest_result_payload(
    document: Document,
    version: DocumentVersion,
    chunk_count: int,
    fetched: WeComDocument,
    *,
    connector: Connector | None = None,
) -> dict[str, Any]:
    payload = {
        "document_id": str(document.id),
        "version_id": str(version.id),
        "source": document.source,
        "external_id": document.external_id,
        "title": document.title,
        "version": version.version,
        "chunk_count": chunk_count,
        "revision_id": fetched.revision_id,
        "space_id": fetched.space_id,
        "operation": "knowledge.ingest",
    }
    if connector is not None:
        payload["connector_name"] = connector.name
        payload["connector_id"] = str(connector.id)
    return payload


async def describe_wecom_space(
    session: AsyncSession,
    principal: Principal,
    space_id: str,
    *,
    connector: Connector | None = None,
    credential: str | None = None,
    transport: Any = None,
) -> WeComSpace:
    _require_knowledge_write(principal)
    connector = connector or await resolve_wecom_docs_connector(session, principal.organization_id)
    assert_wecom_docs_egress(connector)
    client = _client(connector, credential, transport)
    try:
        return await client.describe_space(space_id)
    finally:
        await client.aclose()


async def list_wecom_space_nodes(
    session: AsyncSession,
    principal: Principal,
    space_id: str,
    *,
    connector: Connector | None = None,
    credential: str | None = None,
    transport: Any = None,
    tracker: SyncBudgetTracker | None = None,
) -> list[WeComSpaceNode]:
    _require_knowledge_write(principal)
    connector = connector or await resolve_wecom_docs_connector(session, principal.organization_id)
    assert_wecom_docs_egress(connector)
    client = _client(connector, credential, transport)
    try:
        return await client.list_space_nodes(space_id, tracker=tracker)
    finally:
        await client.aclose()


async def sync_wecom_space(
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
    space = normalize_space_id(space_id)
    connector = connector or await resolve_wecom_docs_connector(session, principal.organization_id)
    budget = KnowledgeConnectorBudget.from_connector(connector)
    tracker = SyncBudgetTracker(budget)
    nodes = await list_wecom_space_nodes(
        session,
        principal,
        space,
        connector=connector,
        credential=credential,
        transport=transport,
        tracker=tracker,
    )
    ingested: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for node in nodes:
        if node.node_type != "document" or not node.document_id:
            skipped.append(
                {
                    "node_id": node.node_id,
                    "document_id": node.document_id,
                    "reason": "wecom_docs_node_type_unsupported",
                    "node_type": node.node_type,
                }
            )
            continue
        try:
            document, version, count, fetched = await ingest_wecom_document(
                session,
                principal,
                service,
                document_id=node.document_id,
                title=node.title,
                classification=classification,
                acl=acl,
                inherit_acl=inherit_acl,
                connector=connector,
                credential=credential,
                transport=transport,
                extra_metadata={
                    "wecom_space_id": space,
                    "wecom_node_id": node.node_id,
                },
            )
            ingested.append(
                ingest_result_payload(document, version, count, fetched, connector=connector)
            )
        except ObsionError as exc:
            failed.append(
                {
                    "node_id": node.node_id,
                    "document_id": node.document_id,
                    "code": exc.code,
                    "message": str(exc),
                }
            )
    return attach_sync_result_envelope(
        result={
            "operation": "knowledge.sync",
            "space_id": space,
            "ingested": ingested,
            "skipped": skipped,
            "failed": failed,
            "ingested_count": len(ingested),
            "skipped_count": len(skipped),
            "failed_count": len(failed),
        },
        budget=tracker.snapshot(),
        provenance=VendorKnowledgeProvenance(
            source=WECOM_KNOWLEDGE_SOURCE,
            external_id=space,
            revision_id=None,
            connector_name=connector.name,
            connector_id=str(connector.id),
            operation="knowledge.sync",
            sync_scope_id=space,
        ),
    )
