"""项目来源内部事实表；数据库记录不等于来源权限或厂商 scopes 证明。"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from obsion.db.base import Base, IdMixin, OrganizationMixin


class ConnectorConfigurationVersion(Base, IdMixin, OrganizationMixin):
    __tablename__ = "connector_configuration_versions"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_connector_config_versions_org_id"),
        ForeignKeyConstraint(
            ["organization_id", "connector_id"],
            ["connectors.organization_id", "connectors.id"],
            name="fk_connector_config_versions_connector",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "created_by"],
            ["users.organization_id", "users.id"],
            name="fk_connector_config_versions_creator",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(trim(connector_type)) > 0 AND length(trim(environment)) > 0",
            name="version_identity_nonempty",
        ),
    )

    connector_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    connector_type: Mapped[str] = mapped_column(String(160), nullable=False)
    environment: Mapped[str] = mapped_column(String(80), nullable=False)
    endpoint: Mapped[str | None] = mapped_column(String(1024))
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    credential_ref: Mapped[str | None] = mapped_column(String(500))
    declared_grants: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    allowed_egress: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProjectSource(Base, IdMixin, OrganizationMixin):
    __tablename__ = "project_sources"
    __table_args__ = (
        UniqueConstraint("organization_id", "id", name="uq_project_sources_org_id"),
        UniqueConstraint(
            "organization_id",
            "workspace_id",
            "repository_id",
            "connector_version_id",
            name="uq_project_sources_fixed_binding",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            name="fk_project_sources_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "repository_id"],
            ["code_repositories.organization_id", "code_repositories.id"],
            name="fk_project_sources_repository",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "connector_version_id"],
            [
                "connector_configuration_versions.organization_id",
                "connector_configuration_versions.id",
            ],
            name="fk_project_sources_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "created_by"],
            ["users.organization_id", "users.id"],
            name="fk_project_sources_creator",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(trim(provider_repository_id)) > 0", name="provider_id_nonempty"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    repository_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    connector_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    provider_repository_id: Mapped[str] = mapped_column(String(500), nullable=False)
    created_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ConnectorVersionRevocation(Base, OrganizationMixin):
    __tablename__ = "connector_version_revocations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "connector_version_id"],
            [
                "connector_configuration_versions.organization_id",
                "connector_configuration_versions.id",
            ],
            name="fk_connector_version_revocations_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "revoked_by"],
            ["users.organization_id", "users.id"],
            name="fk_connector_version_revocations_actor",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "reason_code IN ('OPERATOR_REVOKED', 'CONFIGURATION_REPLACED', 'SECURITY_REVOKED')",
            name="revocation_reason",
        ),
    )

    connector_version_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    revoked_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ProjectSourceRevocation(Base, OrganizationMixin):
    __tablename__ = "project_source_revocations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "source_id"],
            ["project_sources.organization_id", "project_sources.id"],
            name="fk_project_source_revocations_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "revoked_by"],
            ["users.organization_id", "users.id"],
            name="fk_project_source_revocations_actor",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "reason_code IN ('OPERATOR_REVOKED', 'CONFIGURATION_REPLACED', 'SECURITY_REVOKED')",
            name="revocation_reason",
        ),
    )

    source_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    revoked_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RunSourcePin(Base, IdMixin, OrganizationMixin):
    """不可变的 Run 到授权项目版本绑定。

    ``ProjectSource`` 只描述允许使用哪一个供应商仓库；本表还固定本次
    Run 实际核验过的 commit/tree 和快照指纹。来源撤权仍通过每次读取时
    重查账本生效，不能把这个历史 pin 当成永久授权。
    """

    __tablename__ = "run_source_pins"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "run_id",
            "source_id",
            name="uq_run_source_pins_run_source",
        ),
        ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name="fk_run_source_pins_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["organization_id", "source_id"],
            ["project_sources.organization_id", "project_sources.id"],
            name="fk_run_source_pins_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "connector_version_id"],
            [
                "connector_configuration_versions.organization_id",
                "connector_configuration_versions.id",
            ],
            name="fk_run_source_pins_connector_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "workspace_id"],
            ["workspaces.organization_id", "workspaces.id"],
            name="fk_run_source_pins_workspace",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "repository_id"],
            ["code_repositories.organization_id", "code_repositories.id"],
            name="fk_run_source_pins_repository",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "pinned_by"],
            ["users.organization_id", "users.id"],
            name="fk_run_source_pins_pinner",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(commit_id) IN (40, 64) AND length(tree_id) = length(commit_id)",
            name="run_source_pin_hash_lengths",
        ),
        CheckConstraint(
            "length(snapshot_fingerprint) = 64",
            name="run_source_pin_fingerprint_length",
        ),
        CheckConstraint("file_count >= 0", name="nonnegative_run_source_pin_file_count"),
        CheckConstraint("snapshot_bytes >= 0", name="nonnegative_run_source_pin_bytes"),
    )

    run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    source_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    connector_version_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    workspace_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    repository_id: Mapped[UUID] = mapped_column(Uuid, nullable=False, index=True)
    commit_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tree_id: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    pinned_by: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
