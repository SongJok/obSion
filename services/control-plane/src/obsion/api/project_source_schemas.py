"""来源管理仅接收固定标识；不是源码获取或 scopes 证明契约。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from obsion.application.project_sources import RevocationReason


class SourceManagementModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class CreateSourceVersionRequest(SourceManagementModel):
    connector_id: UUID


class RegisterProjectSourceRequest(SourceManagementModel):
    workspace_id: UUID
    repository_id: UUID
    connector_version_id: UUID
    provider_repository_id: str = Field(
        min_length=1,
        max_length=500,
        pattern=r"^[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*$",
    )


class RevokeProjectSourceRequest(SourceManagementModel):
    reason: RevocationReason


class SourceManagementView(SourceManagementModel):
    outcome: Literal["CREATED", "UNCHANGED"]
    record_id: UUID
    policy_decision_id: UUID
    reason: Literal["recorded", "already_recorded"]


class SourceVersionInventoryView(SourceManagementModel):
    """脱敏的冻结 Connector 版本清单，供管理员对账绑定前后的状态。"""

    id: UUID
    connector_id: UUID
    connector_name: str
    connector_type: str
    environment: str
    created_by: UUID
    created_at: datetime
    revoked_at: datetime | None
    revocation_reason: str | None
    active: bool


class ProjectSourceInventoryView(SourceManagementModel):
    """脱敏的项目来源绑定清单，不暴露连接端点、配置或凭据引用。"""

    id: UUID
    workspace_id: UUID
    repository_id: UUID
    connector_version_id: UUID
    connector_name: str
    connector_type: str
    environment: str
    provider_repository_id: str
    created_by: UUID
    created_at: datetime
    source_revoked_at: datetime | None
    source_revocation_reason: str | None
    version_revoked_at: datetime | None
    version_revocation_reason: str | None
    active: bool
