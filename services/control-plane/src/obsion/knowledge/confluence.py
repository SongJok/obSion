"""Operator and Capability ingest of Confluence pages into Organization Knowledge."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.capabilities.confluence import (
    CONFLUENCE_KNOWLEDGE_SOURCE,
    ConfluenceClient,
    ConfluencePage,
    ConfluenceSpace,
    ConfluenceSpacePage,
    assert_confluence_egress,
    fetch_authorized_confluence_page,
    is_confluence_connector,
    normalize_page_id,
    normalize_space_id,
    resolve_confluence_credentials,
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
                "Confluence restriction inheritance did not produce an explicit ACL",
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


async def resolve_confluence_connector(
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
        statement = statement.where(Connector.name == "obsion-confluence")
    connector = await session.scalar(statement)
    if connector is None or not is_confluence_connector(connector):
        raise NotFoundError("Connector", connector_id or "obsion-confluence")
    return connector


def _require_knowledge_write(principal: Principal) -> None:
    if not principal.can("knowledge.write"):
        raise AuthorizationError("knowledge_write_denied", "Document ingestion is not permitted")


def _client(connector: Connector, credential: str | None, transport: Any) -> ConfluenceClient:
    email, token, site_host = resolve_confluence_credentials(connector, credential)
    return ConfluenceClient(
        email=email,
        api_token=token,
        site_host=site_host,
        transport=transport,
    )


async def ingest_confluence_page(
    session: AsyncSession,
    principal: Principal,
    service: KnowledgeService,
    *,
    page_id: str,
    title: str | None = None,
    classification: Classification = Classification.INTERNAL,
    acl: dict[str, Any] | None = None,
    inherit_acl: bool = False,
    connector: Connector | None = None,
    credential: str | None = None,
    transport: Any = None,
    extra_metadata: dict[str, Any] | None = None,
) -> tuple[Document, DocumentVersion, int, ConfluencePage]:
    token = normalize_page_id(page_id)
    connector = connector or await resolve_confluence_connector(session, principal.organization_id)
    if not is_confluence_connector(connector):
        raise ValidationError(
            "confluence_operation_invalid",
            "The connector is not a Confluence Knowledge source",
        )
    assert_confluence_egress(connector)
    email, api_token, site_host = resolve_confluence_credentials(connector, credential)
    fetched = await fetch_authorized_confluence_page(
        email=email,
        api_token=api_token,
        site_host=site_host,
        page_id=token,
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
        source=CONFLUENCE_KNOWLEDGE_SOURCE,
        external_id=fetched.page_id,
        title=(title or fetched.title).strip() or fetched.page_id,
        media_type="text/html",
        filename=f"{fetched.page_id}.html",
        content=fetched.content.encode("utf-8"),
        classification=classification,
        acl=resolved_acl,
        extra_metadata=attach_ingest_provenance(
            extra_metadata={**fetched.as_metadata(), **(extra_metadata or {})},
            provenance=VendorKnowledgeProvenance(
                source=CONFLUENCE_KNOWLEDGE_SOURCE,
                external_id=fetched.page_id,
                revision_id=fetched.version,
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
    fetched: ConfluencePage,
) -> dict[str, Any]:
    return {
        "document_id": str(document.id),
        "version_id": str(version.id),
        "source": document.source,
        "external_id": document.external_id,
        "title": document.title,
        "version": version.version,
        "chunk_count": chunk_count,
        "revision_id": fetched.version,
        "space_id": fetched.space_id,
        "operation": "knowledge.ingest",
    }


async def list_confluence_spaces(
    session: AsyncSession,
    principal: Principal,
    *,
    connector: Connector | None = None,
    credential: str | None = None,
    transport: Any = None,
    tracker: SyncBudgetTracker | None = None,
) -> list[ConfluenceSpace]:
    _require_knowledge_write(principal)
    connector = connector or await resolve_confluence_connector(session, principal.organization_id)
    assert_confluence_egress(connector)
    client = _client(connector, credential, transport)
    try:
        return await client.list_spaces(tracker=tracker)
    finally:
        await client.aclose()


async def list_confluence_pages(
    session: AsyncSession,
    principal: Principal,
    space_id: str,
    *,
    connector: Connector | None = None,
    credential: str | None = None,
    transport: Any = None,
    tracker: SyncBudgetTracker | None = None,
) -> list[ConfluenceSpacePage]:
    _require_knowledge_write(principal)
    connector = connector or await resolve_confluence_connector(session, principal.organization_id)
    assert_confluence_egress(connector)
    client = _client(connector, credential, transport)
    try:
        return await client.list_pages(space_id, tracker=tracker)
    finally:
        await client.aclose()


async def sync_confluence_space(
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
    connector = connector or await resolve_confluence_connector(session, principal.organization_id)
    budget = KnowledgeConnectorBudget.from_connector(connector)
    tracker = SyncBudgetTracker(budget)
    pages = await list_confluence_pages(
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
    for page in pages:
        if page.status != "current":
            skipped.append(
                {
                    "page_id": page.page_id,
                    "title": page.title,
                    "status": page.status,
                    "reason": "confluence_page_id_invalid",
                }
            )
            continue
        try:
            document, version, count, fetched = await _ingest_space_page(
                session,
                principal,
                service,
                page=page,
                space_id=resolved_space,
                classification=classification,
                acl=acl,
                inherit_acl=inherit_acl,
                connector=connector,
                credential=credential,
                transport=transport,
            )
            payload = ingest_result_payload(document, version, count, fetched)
            payload["space_id"] = resolved_space
            payload["connector_name"] = connector.name
            ingested.append(payload)
        except ObsionError as exc:
            failed.append(
                {
                    "page_id": page.page_id,
                    "title": page.title,
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
            source=CONFLUENCE_KNOWLEDGE_SOURCE,
            external_id=resolved_space,
            revision_id=None,
            connector_name=connector.name,
            connector_id=str(connector.id),
            operation="knowledge.sync",
            sync_scope_id=resolved_space,
        ),
    )


async def _ingest_space_page(
    session: AsyncSession,
    principal: Principal,
    service: KnowledgeService,
    *,
    page: ConfluenceSpacePage,
    space_id: str,
    classification: Classification,
    acl: dict[str, Any] | None,
    inherit_acl: bool,
    connector: Connector,
    credential: str | None,
    transport: Any,
) -> tuple[Document, DocumentVersion, int, ConfluencePage]:
    extra_metadata = {"confluence_space_id": space_id}
    if hasattr(session, "begin_nested"):
        async with session.begin_nested():
            return await ingest_confluence_page(
                session,
                principal,
                service,
                page_id=page.page_id,
                title=page.title,
                classification=classification,
                acl=acl,
                inherit_acl=inherit_acl,
                connector=connector,
                credential=credential,
                transport=transport,
                extra_metadata=extra_metadata,
            )
    return await ingest_confluence_page(
        session,
        principal,
        service,
        page_id=page.page_id,
        title=page.title,
        classification=classification,
        acl=acl,
        inherit_acl=inherit_acl,
        connector=connector,
        credential=credential,
        transport=transport,
        extra_metadata=extra_metadata,
    )
