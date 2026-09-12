"""Manage a user's fixed DingTalk sources without exposing host credentials."""

from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.knowledge_sync_schemas import (
    ControlKnowledgeSource,
    KnowledgeSourceConnection,
    KnowledgeSourceCounts,
    KnowledgeSourceItemPage,
    KnowledgeSourceItemView,
    KnowledgeSourcePage,
    KnowledgeSourceView,
    RegisterKnowledgeSource,
)
from obsion.capabilities.dingtalk_managed import MANAGED_CONNECTOR_TYPE
from obsion.common.errors import AuthorizationError, NotFoundError, ObsionError
from obsion.common.time import ensure_utc, utc_now
from obsion.config import Environment, Settings
from obsion.db.models import (
    Connector,
    Document,
    ImPrincipalBinding,
    KnowledgeSyncItem,
    KnowledgeSyncSource,
)
from obsion.db.project_source_models import (
    ConnectorConfigurationVersion,
    ConnectorVersionRevocation,
)
from obsion.domain.enums import ActorType, ConnectorStatus, RiskLevel
from obsion.knowledge.service import KnowledgeService
from obsion.knowledge.source_access import external_access_clause
from obsion.knowledge.sync import KnowledgeSyncService, _same_connection
from obsion.persistence.audit import AuditDraft
from obsion.security.auth import get_app_settings, get_principal, get_session
from obsion.security.identity import Principal

router = APIRouter(
    prefix="/knowledge/sources/dingtalk/managed", tags=["knowledge-source-management"]
)


def source_service(
    request: Request, settings: Settings = Depends(get_app_settings)
) -> KnowledgeSyncService:
    if settings.environment not in {Environment.DEVELOPMENT, Environment.TEST}:
        raise AuthorizationError("knowledge_write_denied", "当前环境尚未开放宿主管理的文档同步")
    return KnowledgeSyncService(KnowledgeService(settings, request.app.state.object_store))


async def _perform[T](
    session: AsyncSession,
    principal: Principal,
    service: KnowledgeSyncService,
    operation: str,
    work: Callable[[UUID], Awaitable[T]],
    source_id: UUID | None = None,
    connector_id: UUID | None = None,
) -> T:
    """Keep Policy and denial audit outside a rolled-back mutation savepoint."""
    correlation = uuid4()
    error: ObsionError | None = None
    result: T
    decision: UUID | None = None
    async with session.begin():
        try:
            resource: dict[str, Any] = {"source": "dingtalk-managed", "operation": operation}
            if source_id is not None:
                resource["source_id"] = str(source_id)
                owned = await session.scalar(
                    select(KnowledgeSyncSource).where(
                        KnowledgeSyncSource.id == source_id,
                        KnowledgeSyncSource.organization_id == principal.organization_id,
                        KnowledgeSyncSource.user_id == principal.id,
                    )
                )
                if owned is not None:
                    resource["corp_id"] = owned.corp_id
            if connector_id is not None:
                resource["connector_id"] = str(connector_id)
                connector = await session.scalar(
                    select(Connector).where(
                        Connector.id == connector_id,
                        Connector.organization_id == principal.organization_id,
                    )
                )
                if connector is not None and isinstance(connector.configuration, dict):
                    resource["corp_id"] = connector.configuration.get("corp_id")
            decision = await service._authorize(session, principal, resource, correlation)
            async with session.begin_nested():
                result = await work(correlation)
        except ObsionError as exc:
            error = exc
            if decision is not None:
                await service.audit.write(
                    session,
                    AuditDraft(
                        organization_id=principal.organization_id,
                        correlation_id=correlation,
                        actor_type=ActorType.USER,
                        actor_id=principal.id,
                        action="knowledge.write",
                        resource_type="knowledge_source",
                        resource_id=str(source_id) if source_id else None,
                        outcome="DENIED",
                        policy_decision_id=decision,
                        risk_level=RiskLevel.L2,
                        metadata={"operation": operation, "error_code": exc.code},
                    ),
                )
    if error is not None:
        raise error
    return result


async def _owned_source(
    session: AsyncSession, principal: Principal, source_id: UUID
) -> KnowledgeSyncSource:
    source: KnowledgeSyncSource | None = await session.scalar(
        select(KnowledgeSyncSource).where(
            KnowledgeSyncSource.id == source_id,
            KnowledgeSyncSource.organization_id == principal.organization_id,
            KnowledgeSyncSource.user_id == principal.id,
        )
    )
    if source is None:
        raise NotFoundError("Knowledge source", source_id)
    return source


async def _available_documents(
    session: AsyncSession, principal: Principal, source_id: UUID
) -> set[UUID]:
    return set(
        (
            await session.scalars(
                select(Document.id)
                .join(
                    KnowledgeSyncItem,
                    KnowledgeSyncItem.document_id == Document.id,
                )
                .where(KnowledgeSyncItem.source_id == source_id, external_access_clause(principal))
            )
        ).all()
    )


async def _view(
    session: AsyncSession, principal: Principal, source: KnowledgeSyncSource
) -> KnowledgeSourceView:
    version = await session.get(ConnectorConfigurationVersion, source.connector_version_id)
    assert version is not None
    connector = await session.get(Connector, version.connector_id)
    assert connector is not None
    rows = (
        await session.execute(
            select(KnowledgeSyncItem.kind, KnowledgeSyncItem.status, func.count())
            .where(KnowledgeSyncItem.source_id == source.id)
            .group_by(KnowledgeSyncItem.kind, KnowledgeSyncItem.status)
        )
    ).all()
    counts = KnowledgeSourceCounts()
    ready = 0
    for kind, status, count in rows:
        if kind == "folder":
            counts.containers += count
            continue
        if status == "MISSING":
            counts.removed += count
            continue
        counts.files += count
        if status == "READY":
            ready += count
        elif status == "PENDING":
            counts.pending += count
        elif status == "PARTIAL":
            counts.partial += count
        elif status in {"FAILED", "DENIED"}:
            counts.failed += count
    counts.available = len(await _available_documents(session, principal, source.id))
    counts.awaiting_access_check = max(0, ready - counts.available)
    connection_changed = (
        connector.status != ConnectorStatus.ACTIVE
        or not _same_connection(connector, version)
        or await session.get(ConnectorVersionRevocation, version.id) is not None
    )
    state: Any
    if not source.active:
        state = "PAUSED"
    elif connection_changed or source.last_error_code:
        state = "ATTENTION"
    elif source.lease_expires_at and ensure_utc(source.lease_expires_at) > utc_now():
        state = "SYNCING"
    elif source.scan_state or ensure_utc(source.next_poll_at) <= utc_now():
        state = "QUEUED"
    elif counts.partial or counts.failed or counts.awaiting_access_check:
        state = "ATTENTION"
    else:
        state = "CURRENT"
    return KnowledgeSourceView(
        id=source.id,
        connector_id=connector.id,
        name=connector.name,
        corp_id=source.corp_id,
        active=source.active,
        state=state,
        counts=counts,
        last_scan_completed_at=source.last_success_at,
        next_check_at=source.next_poll_at,
        issue="connection_changed" if connection_changed else source.last_error_code,
    )


@router.get("/connections", response_model=list[KnowledgeSourceConnection])
async def connections(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: KnowledgeSyncService = Depends(source_service),
) -> list[KnowledgeSourceConnection]:
    async def work(_: UUID) -> list[KnowledgeSourceConnection]:
        # No external calls: only already configured, same-user bindings are offered.
        connectors = (
            await session.scalars(
                select(Connector)
                .where(
                    Connector.organization_id == principal.organization_id,
                    Connector.connector_type == MANAGED_CONNECTOR_TYPE,
                    Connector.environment == "development",
                    Connector.status == ConnectorStatus.ACTIVE,
                )
                .order_by(Connector.name, Connector.id)
            )
        ).all()
        result = []
        for connector in connectors:
            config: Any = connector.configuration
            if not isinstance(config, dict):
                continue
            try:
                installation_id = UUID(str(config.get("installation_id")))
            except ValueError:
                continue
            bindings = (
                await session.scalars(
                    select(ImPrincipalBinding)
                    .where(
                        ImPrincipalBinding.organization_id == principal.organization_id,
                        ImPrincipalBinding.user_id == principal.id,
                        ImPrincipalBinding.installation_id == installation_id,
                        ImPrincipalBinding.sender_id == config.get("user_id"),
                        ImPrincipalBinding.active.is_(True),
                        ImPrincipalBinding.revoked_at.is_(None),
                    )
                    .limit(2)
                )
            ).all()
            if len(bindings) != 1:
                continue
            try:
                binding, installation = await service._identity(
                    session, principal, connector, bindings[0].id
                )
            except AuthorizationError:
                continue
            result.append(
                KnowledgeSourceConnection(
                    connector_id=connector.id,
                    binding_id=bindings[0].id,
                    name=connector.name,
                    corp_id=installation.corp_id,
                )
            )
        return result

    return await _perform(session, principal, service, "connections", work)


@router.get("", response_model=KnowledgeSourcePage)
async def sources(
    cursor: UUID | None = None,
    limit: int = Query(default=30, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: KnowledgeSyncService = Depends(source_service),
) -> KnowledgeSourcePage:
    async def work(_: UUID) -> KnowledgeSourcePage:
        query = select(KnowledgeSyncSource).where(
            KnowledgeSyncSource.organization_id == principal.organization_id,
            KnowledgeSyncSource.user_id == principal.id,
        )
        if cursor:
            query = query.where(KnowledgeSyncSource.id > cursor)
        rows = (
            await session.scalars(query.order_by(KnowledgeSyncSource.id).limit(limit + 1))
        ).all()
        return KnowledgeSourcePage(
            items=[await _view(session, principal, row) for row in rows[:limit]],
            next_cursor=rows[limit - 1].id if len(rows) > limit else None,
        )

    return await _perform(session, principal, service, "list", work)


@router.post("", response_model=KnowledgeSourceView, status_code=201)
async def register(
    body: RegisterKnowledgeSource,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: KnowledgeSyncService = Depends(source_service),
) -> KnowledgeSourceView:
    async def work(correlation: UUID) -> KnowledgeSourceView:
        source = await service.create_source(
            session,
            principal,
            connector_id=body.connector_id,
            binding_id=body.binding_id,
            correlation_id=correlation,
        )
        return await _view(session, principal, source)

    return await _perform(
        session, principal, service, "register", work, connector_id=body.connector_id
    )


@router.post("/{source_id}/control", response_model=KnowledgeSourceView)
async def control(
    source_id: UUID,
    body: ControlKnowledgeSource,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: KnowledgeSyncService = Depends(source_service),
) -> KnowledgeSourceView:
    async def work(correlation: UUID) -> KnowledgeSourceView:
        if body.operation == "pause":
            await service.disable_source(
                session, principal, source_id=source_id, correlation_id=correlation
            )
        else:
            await service.schedule_source(
                session,
                principal,
                source_id=source_id,
                correlation_id=correlation,
                resume=body.operation == "resume",
            )
        return await _view(session, principal, await _owned_source(session, principal, source_id))

    return await _perform(
        session,
        principal,
        service,
        {"pause": "disable", "resume": "resume", "sync": "synchronize"}[body.operation],
        work,
        source_id,
    )


@router.get("/{source_id}/items", response_model=KnowledgeSourceItemPage)
async def source_items(
    source_id: UUID,
    cursor: UUID | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: KnowledgeSyncService = Depends(source_service),
) -> KnowledgeSourceItemPage:
    async def work(_: UUID) -> KnowledgeSourceItemPage:
        await _owned_source(session, principal, source_id)
        available = await _available_documents(session, principal, source_id)
        query = select(KnowledgeSyncItem).where(KnowledgeSyncItem.source_id == source_id)
        if cursor:
            query = query.where(KnowledgeSyncItem.id > cursor)
        rows = (await session.scalars(query.order_by(KnowledgeSyncItem.id).limit(limit + 1))).all()
        return KnowledgeSourceItemPage(
            items=[
                KnowledgeSourceItemView(
                    id=row.id,
                    title=row.title,
                    kind=row.kind,
                    status=row.status,
                    available=row.document_id in available,
                    document_id=row.document_id if row.document_id in available else None,
                    revision=row.source_revision,
                    checked_at=row.checked_at,
                    issues=[*row.gaps, *([row.last_error_code] if row.last_error_code else [])],
                )
                for row in rows[:limit]
            ],
            next_cursor=rows[limit - 1].id if len(rows) > limit else None,
        )

    return await _perform(session, principal, service, "items", work, source_id)
