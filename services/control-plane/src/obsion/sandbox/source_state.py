"""可信控制面内部的来源状态前置检查；不授予权限、不获取或返回源码。"""

import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.db.models import CodeRepository, Connector, Organization, Workspace
from obsion.db.project_source_models import (
    ConnectorConfigurationVersion,
    ConnectorVersionRevocation,
    ProjectSource,
    ProjectSourceRevocation,
)
from obsion.domain.enums import ConnectorStatus
from obsion.sandbox.project import ProjectRejected, ProjectRevision

_CONFIG_FIELDS = (
    "connector_type",
    "environment",
    "endpoint",
    "configuration",
    "credential_ref",
    "declared_grants",
    "allowed_egress",
)


async def check_project_source_state(
    session: AsyncSession, revision: ProjectRevision, *, environment: str
) -> None:
    """每次使用前重读数据库；成功仅表示当前事务快照的来源状态可继续检查。

    不检查用户/仓库 ACL、Policy、应用实际 scopes、任务授权或 Git 对象。
    调用者仍须完成全部授权交集；本函数不是外部调用的锁、租约或许可票据。
    """
    if type(revision) is not ProjectRevision or not environment:
        raise ProjectRejected("project_source_unavailable")
    # Core 列查询不触发 ORM autoflush，先纳入当前事务尚未刷新的撤销/禁用。
    await session.flush()
    source = ProjectSource.__table__
    version = ConnectorConfigurationVersion.__table__
    connector = Connector.__table__
    workspace = Workspace.__table__
    repository = CodeRepository.__table__
    organization = Organization.__table__
    version_revocation = ConnectorVersionRevocation.__table__
    source_revocation = ProjectSourceRevocation.__table__
    row = (
        (
            await session.execute(
                select(
                    *[version.c[key].label(f"pinned_{key}") for key in _CONFIG_FIELDS],
                    *[connector.c[key].label(f"current_{key}") for key in _CONFIG_FIELDS],
                )
                .select_from(source)
                .join(version, source.c.connector_version_id == version.c.id)
                .join(connector, version.c.connector_id == connector.c.id)
                .join(workspace, source.c.workspace_id == workspace.c.id)
                .join(repository, source.c.repository_id == repository.c.id)
                .join(organization, source.c.organization_id == organization.c.id)
                .where(
                    source.c.organization_id == revision.organization_id,
                    version.c.organization_id == revision.organization_id,
                    connector.c.organization_id == revision.organization_id,
                    workspace.c.organization_id == revision.organization_id,
                    repository.c.organization_id == revision.organization_id,
                    source.c.workspace_id == revision.workspace_id,
                    source.c.repository_id == revision.repository_id,
                    source.c.connector_version_id == revision.connector_version_id,
                    organization.c.active.is_(True),
                    workspace.c.archived_at.is_(None),
                    repository.c.deleted_at.is_(None),
                    connector.c.status == ConnectorStatus.ACTIVE,
                    version.c.environment == environment,
                    ~select(version_revocation.c.connector_version_id)
                    .where(version_revocation.c.connector_version_id == version.c.id)
                    .exists(),
                    ~select(source_revocation.c.source_id)
                    .where(source_revocation.c.source_id == source.c.id)
                    .exists(),
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise ProjectRejected("project_source_unavailable")
    # 使用数据库列而非缓存 ORM 对象；严格 JSON 比较也区分 true 与 1。
    try:
        pinned = json.dumps(
            [row[f"pinned_{key}"] for key in _CONFIG_FIELDS], sort_keys=True, allow_nan=False
        )
        current = json.dumps(
            [row[f"current_{key}"] for key in _CONFIG_FIELDS], sort_keys=True, allow_nan=False
        )
    except (TypeError, ValueError):
        raise ProjectRejected("project_source_unavailable") from None
    if pinned != current:
        raise ProjectRejected("project_source_unavailable")
