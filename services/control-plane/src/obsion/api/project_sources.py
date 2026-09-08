"""受治理的来源元数据管理；没有网络、源码或 Agent 委托入口。"""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.project_source_schemas import (
    CreateSourceVersionRequest,
    ProjectSourceInventoryView,
    RegisterProjectSourceRequest,
    RevokeProjectSourceRequest,
    SourceManagementView,
    SourceVersionInventoryView,
)
from obsion.api.schemas import ErrorBody
from obsion.application.project_sources import ProjectSourceService, SourceManagementResult
from obsion.common.errors import AuthorizationError, ConflictError, ValidationError
from obsion.config import Environment, Settings
from obsion.db.models import Connector
from obsion.db.project_source_models import (
    ConnectorConfigurationVersion,
    ConnectorVersionRevocation,
    ProjectSource,
    ProjectSourceRevocation,
)
from obsion.security.auth import get_app_settings, get_principal, get_session
from obsion.security.identity import Principal


def _create_router() -> APIRouter:
    return APIRouter(
        prefix="/admin/project-sources",
        tags=["project-source-management"],
        responses={
            403: {"model": ErrorBody, "description": "认证、权限或来源状态不允许该操作。"},
            409: {"model": ErrorBody, "description": "固定来源已绑定到不同标识。"},
        },
    )


router = _create_router()


def _require_inventory_access(principal: Principal) -> None:
    if not principal.can("project_source.read"):
        raise AuthorizationError("admin_access_denied", "Administration access is not permitted")


@router.get("/versions", response_model=list[SourceVersionInventoryView])
async def list_source_versions(
    include_revoked: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[SourceVersionInventoryView]:
    """List frozen connector versions without returning configuration or secrets."""
    _require_inventory_access(principal)
    statement = (
        select(
            ConnectorConfigurationVersion,
            Connector,
            ConnectorVersionRevocation,
        )
        .join(
            Connector,
            (Connector.id == ConnectorConfigurationVersion.connector_id)
            & (Connector.organization_id == ConnectorConfigurationVersion.organization_id),
        )
        .outerjoin(
            ConnectorVersionRevocation,
            (ConnectorVersionRevocation.connector_version_id == ConnectorConfigurationVersion.id)
            & (
                ConnectorVersionRevocation.organization_id
                == ConnectorConfigurationVersion.organization_id
            ),
        )
        .where(ConnectorConfigurationVersion.organization_id == principal.organization_id)
        .order_by(ConnectorConfigurationVersion.created_at.desc(), ConnectorConfigurationVersion.id)
    )
    rows = (await session.execute(statement)).all()
    views: list[SourceVersionInventoryView] = []
    for version, connector, revocation in rows:
        if revocation is not None and not include_revoked:
            continue
        views.append(
            SourceVersionInventoryView(
                id=version.id,
                connector_id=version.connector_id,
                connector_name=connector.name,
                connector_type=version.connector_type,
                environment=version.environment,
                created_by=version.created_by,
                created_at=version.created_at,
                revoked_at=revocation.revoked_at if revocation is not None else None,
                revocation_reason=revocation.reason_code if revocation is not None else None,
                active=revocation is None,
            )
        )
    return views


@router.get("", response_model=list[ProjectSourceInventoryView])
async def list_project_sources(
    workspace_id: UUID | None = Query(default=None),
    repository_id: UUID | None = Query(default=None),
    include_revoked: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[ProjectSourceInventoryView]:
    """List fixed project-to-provider bindings for operator reconciliation."""
    _require_inventory_access(principal)
    statement = (
        select(
            ProjectSource,
            ConnectorConfigurationVersion,
            Connector,
            ProjectSourceRevocation,
            ConnectorVersionRevocation,
        )
        .join(
            ConnectorConfigurationVersion,
            (ConnectorConfigurationVersion.id == ProjectSource.connector_version_id)
            & (ConnectorConfigurationVersion.organization_id == ProjectSource.organization_id),
        )
        .join(
            Connector,
            (Connector.id == ConnectorConfigurationVersion.connector_id)
            & (Connector.organization_id == ProjectSource.organization_id),
        )
        .outerjoin(
            ProjectSourceRevocation,
            (ProjectSourceRevocation.source_id == ProjectSource.id)
            & (ProjectSourceRevocation.organization_id == ProjectSource.organization_id),
        )
        .outerjoin(
            ConnectorVersionRevocation,
            (ConnectorVersionRevocation.connector_version_id == ProjectSource.connector_version_id)
            & (ConnectorVersionRevocation.organization_id == ProjectSource.organization_id),
        )
        .where(ProjectSource.organization_id == principal.organization_id)
        .order_by(ProjectSource.created_at.desc(), ProjectSource.id)
    )
    if workspace_id is not None:
        statement = statement.where(ProjectSource.workspace_id == workspace_id)
    if repository_id is not None:
        statement = statement.where(ProjectSource.repository_id == repository_id)
    rows = (await session.execute(statement)).all()
    views: list[ProjectSourceInventoryView] = []
    for source, version, connector, source_revocation, version_revocation in rows:
        if (
            source_revocation is not None or version_revocation is not None
        ) and not include_revoked:
            continue
        views.append(
            ProjectSourceInventoryView(
                id=source.id,
                workspace_id=source.workspace_id,
                repository_id=source.repository_id,
                connector_version_id=source.connector_version_id,
                connector_name=connector.name,
                connector_type=version.connector_type,
                environment=version.environment,
                provider_repository_id=source.provider_repository_id,
                created_by=source.created_by,
                created_at=source.created_at,
                source_revoked_at=(
                    source_revocation.revoked_at if source_revocation is not None else None
                ),
                source_revocation_reason=(
                    source_revocation.reason_code if source_revocation is not None else None
                ),
                version_revoked_at=(
                    version_revocation.revoked_at if version_revocation is not None else None
                ),
                version_revocation_reason=(
                    version_revocation.reason_code if version_revocation is not None else None
                ),
                active=source_revocation is None and version_revocation is None,
            )
        )
    return views


def get_project_source_service(
    settings: Settings = Depends(get_app_settings),
) -> ProjectSourceService:
    if settings.environment == Environment.PRODUCTION:
        raise AuthorizationError("project_source_denied", "当前环境不开放来源管理")
    return ProjectSourceService(environment=settings.environment)


def _committed_result(result: SourceManagementResult) -> SourceManagementView:
    # 只能在外层事务提交之后调用；错误响应不能回滚已记录的拒绝审计。
    details = {"policy_decision_id": str(result.policy_decision_id)}
    if result.outcome == "DENIED":
        raise AuthorizationError("project_source_denied", "来源管理操作不被允许", **details)
    if result.outcome == "INVALID":
        raise ValidationError("project_source_invalid", "来源管理配置或参数不合法", **details)
    if result.outcome == "CONFLICT":
        raise ConflictError("project_source_conflict", "固定来源绑定冲突", **details)
    return SourceManagementView.model_validate(result)


@router.post("/versions", response_model=SourceManagementView, status_code=201)
async def create_source_version(
    request: CreateSourceVersionRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ProjectSourceService = Depends(get_project_source_service),
) -> SourceManagementView:
    """冻结当前 Connector 配置声明；每次成功创建独立版本，不自动重试。"""
    async with session.begin():
        result = await service.create_connector_version(
            session, principal, connector_id=request.connector_id
        )
    return _committed_result(result)


@router.post("", response_model=SourceManagementView)
async def register_project_source(
    request: RegisterProjectSourceRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ProjectSourceService = Depends(get_project_source_service),
) -> SourceManagementView:
    """登记固定声明；不证明厂商身份、实际 scopes 或 Run 获取权限。"""
    async with session.begin():
        result = await service.register_source(
            session,
            principal,
            workspace_id=request.workspace_id,
            repository_id=request.repository_id,
            connector_version_id=request.connector_version_id,
            provider_repository_id=request.provider_repository_id,
        )
    return _committed_result(result)


@router.post("/versions/{connector_version_id}/revoke", response_model=SourceManagementView)
async def revoke_source_version(
    connector_version_id: UUID,
    request: RevokeProjectSourceRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ProjectSourceService = Depends(get_project_source_service),
) -> SourceManagementView:
    async with session.begin():
        result = await service.revoke_connector_version(
            session,
            principal,
            connector_version_id=connector_version_id,
            reason=request.reason,
        )
    return _committed_result(result)


@router.post("/{source_id}/revoke", response_model=SourceManagementView)
async def revoke_project_source(
    source_id: UUID,
    request: RevokeProjectSourceRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ProjectSourceService = Depends(get_project_source_service),
) -> SourceManagementView:
    async with session.begin():
        result = await service.revoke_source(
            session, principal, source_id=source_id, reason=request.reason
        )
    return _committed_result(result)
