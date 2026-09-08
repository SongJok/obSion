"""内部管理登记：调用者拥有事务，无网络、凭据解析或源码读取。

可预期拒绝以结果返回，使调用者能提交拒绝审计；异常会回滚本次 SAVEPOINT。
成功结果仍须等待外层事务提交，不是来源授权、实际 scopes 或获取许可。
"""

import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import desc, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.code_intelligence.service import repository_access_clause
from obsion.common.errors import AuthorizationError
from obsion.common.ids import new_id
from obsion.db.models import CodeRepository, Connector, Organization, SecretReference, Workspace
from obsion.db.project_source_models import (
    ConnectorConfigurationVersion,
    ConnectorVersionRevocation,
    ProjectSource,
    ProjectSourceRevocation,
)
from obsion.domain.enums import ActorType, ConnectorStatus, DecisionEffect, RiskLevel
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.sandbox.source_configuration import configuration_snapshot, valid_repository_id
from obsion.security.auth import load_principal_by_id
from obsion.security.identity import Principal
from obsion.security.policy import PolicyEngine, ResourcePolicyInput
from obsion.security.workspace_access import workspace_access_clause

Outcome = Literal["CREATED", "UNCHANGED", "DENIED", "INVALID", "CONFLICT"]
RevocationReason = Literal["OPERATOR_REVOKED", "CONFIGURATION_REPLACED", "SECURITY_REVOKED"]
_REASONS = frozenset({"OPERATOR_REVOKED", "CONFIGURATION_REPLACED", "SECURITY_REVOKED"})
_CODEUP_REMOTE_ID = re.compile(r"[1-9][0-9]{0,19}")
_CODEUP_NAME = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+")


@dataclass(frozen=True, slots=True)
class SourceManagementResult:
    outcome: Outcome
    record_id: UUID | None
    policy_decision_id: UUID | None
    reason: str


class _Rejected(Exception):
    def __init__(self, reason: str, outcome: Outcome = "DENIED") -> None:
        self.reason = reason
        self.outcome = outcome


class ProjectSourceService:
    def __init__(self, *, environment: str) -> None:
        # 本切片没有生产入口；管理登记不隐式启用沙箱或外部执行。
        if environment not in {"test", "development", "staging"}:
            raise ValueError("project_source_management_environment_denied")
        self.environment = environment
        self.policy = PolicyEngine()
        self.audit = AuditWriter()

    async def _run(
        self,
        session: AsyncSession,
        actor: Principal,
        *,
        action: str,
        resource: dict[str, str],
        operation: Callable[[Principal], Awaitable[tuple[UUID, Outcome]]],
    ) -> SourceManagementResult:
        if not session.in_transaction():
            raise ValueError("project_source_management_requires_transaction")
        async with session.begin_nested():
            # 先等待来源管理锁，再重载权限/Policy；不能用等待锁之前的允许决策登记。
            await self._lock_target(session, actor.organization_id, resource)
            active = bool(
                await session.scalar(
                    select(Organization.active).where(Organization.id == actor.organization_id)
                )
            )
            # 不接受调用者 roles/permissions，也不信任 identity map 中的旧 Role/Policy。
            try:
                current = await load_principal_by_id(session, actor.organization_id, actor.id)
            except AuthorizationError:
                active = False
                current = Principal(actor.id, actor.organization_id, "", "")
            if not active:
                current = Principal(actor.id, actor.organization_id, "", "")
            exists = await session.scalar(
                select(Organization.id).where(Organization.id == actor.organization_id)
            )
            if exists is None:
                # 未知组织没有合法审计 FK；外层认证入口负责记录此类请求。
                return SourceManagementResult("DENIED", None, None, "principal_unavailable")
            decision = await self.policy.evaluate_resource(
                session,
                ResourcePolicyInput(
                    principal=current,
                    action=action,
                    resource_type="project_source_management",
                    resource=resource,
                    context={
                        "entrypoint": "project-source-management",
                        "environment": self.environment,
                    },
                    risk_level=RiskLevel.L2,
                ),
            )
            record_id = None
            reason = "policy_denied"
            outcome: Outcome = "DENIED"
            if active and decision.effect == DecisionEffect.ALLOW and not decision.obligations:
                try:
                    record_id, outcome = await operation(current)
                    reason = "recorded" if outcome == "CREATED" else "already_recorded"
                except _Rejected as rejected:
                    outcome, reason = rejected.outcome, rejected.reason
            await self.audit.write(
                session,
                AuditDraft(
                    organization_id=current.organization_id,
                    correlation_id=new_id(),
                    actor_type=ActorType.USER,
                    actor_id=current.id,
                    action=action,
                    resource_type="project_source_management",
                    resource_id=str(record_id) if record_id else None,
                    outcome=outcome,
                    risk_level=RiskLevel.L2,
                    policy_decision_id=decision.id,
                    resource=resource,
                    metadata={"reason": reason},
                ),
            )
            return SourceManagementResult(outcome, record_id, decision.id, reason)

    async def _lock_target(
        self, session: AsyncSession, organization_id: UUID, resource: dict[str, str]
    ) -> None:
        connector_id = UUID(resource["connector_id"]) if "connector_id" in resource else None
        version_id = (
            UUID(resource["connector_version_id"]) if "connector_version_id" in resource else None
        )
        workspace_id = UUID(resource["workspace_id"]) if "workspace_id" in resource else None
        repository_id = UUID(resource["repository_id"]) if "repository_id" in resource else None
        if "source_id" in resource:
            source = (
                await session.execute(
                    select(
                        ProjectSource.connector_version_id,
                        ProjectSource.workspace_id,
                        ProjectSource.repository_id,
                    ).where(
                        ProjectSource.id == UUID(resource["source_id"]),
                        ProjectSource.organization_id == organization_id,
                    )
                )
            ).one_or_none()
            if source is not None:
                version_id, workspace_id, repository_id = source
        if version_id is not None:
            connector_id = await session.scalar(
                select(ConnectorConfigurationVersion.connector_id).where(
                    ConnectorConfigurationVersion.id == version_id,
                    ConnectorConfigurationVersion.organization_id == organization_id,
                )
            )
        # 固定顺序；只锁租户内目标，不读取配置，不做写入或给出存在性响应。
        for model, identifier in (
            (Connector, connector_id),
            (Workspace, workspace_id),
            (CodeRepository, repository_id),
        ):
            if identifier is not None:
                await session.scalar(
                    select(model.id)
                    .where(model.id == identifier, model.organization_id == organization_id)
                    .with_for_update()
                )

    async def _connector(
        self, session: AsyncSession, actor: Principal, connector_id: UUID
    ) -> dict[str, Any]:
        # PostgreSQL 行锁与旧管理面的 UPDATE 互斥；列查询不会返回 ORM 旧配置。
        row = (
            (
                await session.execute(
                    select(Connector.__table__)
                    .where(
                        Connector.id == connector_id,
                        Connector.organization_id == actor.organization_id,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise _Rejected("source_unavailable")
        return dict(row)

    async def _snapshot(
        self, session: AsyncSession, actor: Principal, connector: dict[str, Any]
    ) -> dict[str, Any]:
        if connector["status"] != ConnectorStatus.ACTIVE:
            raise _Rejected("source_unavailable")
        try:
            snapshot = configuration_snapshot(connector, environment=self.environment)
        except ValueError:
            raise _Rejected("configuration_invalid", "INVALID") from None
        reference = snapshot["credential_ref"]
        if reference is not None:
            available = await session.scalar(
                select(SecretReference.id).where(
                    SecretReference.organization_id == actor.organization_id,
                    SecretReference.name == reference.removeprefix("secret://"),
                )
            )
            if available is None:
                raise _Rejected("configuration_invalid", "INVALID")
        return snapshot

    async def _version(
        self, session: AsyncSession, actor: Principal, version_id: UUID, *, usable: bool
    ) -> dict[str, Any]:
        row = (
            (
                await session.execute(
                    select(ConnectorConfigurationVersion.__table__).where(
                        ConnectorConfigurationVersion.id == version_id,
                        ConnectorConfigurationVersion.organization_id == actor.organization_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise _Rejected("source_unavailable")
        version = dict(row)
        if version["environment"] != self.environment:
            raise _Rejected("source_unavailable")
        connector = await self._connector(session, actor, version["connector_id"])
        if usable:
            revoked = await session.scalar(
                select(ConnectorVersionRevocation.connector_version_id).where(
                    ConnectorVersionRevocation.connector_version_id == version_id,
                    ConnectorVersionRevocation.organization_id == actor.organization_id,
                )
            )
            if revoked is not None:
                raise _Rejected("source_unavailable")
            current = await self._snapshot(session, actor, connector)
            pinned = {key: version[key] for key in current}
            if json.dumps(current, sort_keys=True, allow_nan=False) != json.dumps(
                pinned, sort_keys=True, allow_nan=False
            ):
                raise _Rejected("source_unavailable")
        return version

    async def _project_access(
        self,
        session: AsyncSession,
        actor: Principal,
        workspace_id: UUID,
        repository_id: UUID,
        *,
        usable: bool,
    ) -> None:
        workspace = await session.scalar(
            select(Workspace.id)
            .where(
                Workspace.id == workspace_id,
                Workspace.organization_id == actor.organization_id,
                workspace_access_clause(actor, write=True),
                *((Workspace.archived_at.is_(None),) if usable else ()),
            )
            .with_for_update()
        )
        repository = await session.scalar(
            select(CodeRepository.id)
            .where(
                CodeRepository.id == repository_id,
                CodeRepository.organization_id == actor.organization_id,
                repository_access_clause(actor),
            )
            .with_for_update()
        )
        if workspace is None or repository is None:
            raise _Rejected("source_unavailable")

    async def create_connector_version(
        self, session: AsyncSession, actor: Principal, *, connector_id: UUID
    ) -> SourceManagementResult:
        async def operation(current: Principal) -> tuple[UUID, Outcome]:
            connector = await self._connector(session, current, connector_id)
            snapshot = await self._snapshot(session, current, connector)
            version = ConnectorConfigurationVersion(
                organization_id=current.organization_id,
                connector_id=connector_id,
                created_by=current.id,
                **snapshot,
            )
            session.add(version)
            await session.flush()
            return version.id, "CREATED"

        return await self._run(
            session,
            actor,
            action="project_source.version.create",
            resource={"connector_id": str(connector_id)},
            operation=operation,
        )

    async def map_codeup_repository(
        self,
        session: AsyncSession,
        actor: Principal,
        *,
        connector_id: UUID,
        repository_id: UUID,
        provider_repository_id: str,
    ) -> SourceManagementResult:
        """Bind a verified Codeup repository and freeze the resulting config.

        The provider lookup is performed by the Codeup catalog Gateway before
        this method is called. This transaction only changes the local mapping
        and creates an immutable source version; it never resolves credentials
        or performs vendor I/O.
        """

        async def operation(current: Principal) -> tuple[UUID, Outcome]:
            if not current.can("connectors.write"):
                raise _Rejected("mapping_denied")
            if not _CODEUP_REMOTE_ID.fullmatch(provider_repository_id):
                raise _Rejected("binding_invalid", "INVALID")
            repository = await session.scalar(
                select(CodeRepository)
                .where(
                    CodeRepository.id == repository_id,
                    CodeRepository.organization_id == current.organization_id,
                    CodeRepository.deleted_at.is_(None),
                    repository_access_clause(current),
                )
                .with_for_update()
            )
            if repository is None or _CODEUP_NAME.fullmatch(repository.name) is None:
                raise _Rejected("source_unavailable")
            connector = await self._connector(session, current, connector_id)
            if connector["connector_type"] != "codeup":
                raise _Rejected("source_unavailable")
            configuration = connector.get("configuration")
            if not isinstance(configuration, dict):
                raise _Rejected("configuration_invalid", "INVALID")
            mappings = configuration.get("repositories")
            allowed = configuration.get("allowed_repositories")
            if not isinstance(mappings, dict) or not isinstance(allowed, list):
                raise _Rejected("configuration_invalid", "INVALID")
            current_mapping = mappings.get(repository.name)
            if current_mapping is not None:
                if (
                    isinstance(current_mapping, dict)
                    and current_mapping.get("id") == provider_repository_id
                    and current_mapping.get("repository_id") == str(repository.id)
                ):
                    next_configuration = json.loads(json.dumps(configuration, allow_nan=False))
                    outcome: Outcome = "UNCHANGED"
                else:
                    raise _Rejected("binding_conflict", "CONFLICT")
            else:
                if any(
                    isinstance(value, dict) and value.get("id") == provider_repository_id
                    for value in mappings.values()
                ):
                    raise _Rejected("binding_conflict", "CONFLICT")
                if len(mappings) >= 100:
                    raise _Rejected("configuration_invalid", "INVALID")
                next_configuration = json.loads(json.dumps(configuration, allow_nan=False))
                next_configuration.setdefault("repositories", {})[repository.name] = {
                    "id": provider_repository_id,
                    "repository_id": str(repository.id),
                }
                next_configuration.setdefault("allowed_repositories", []).append(repository.name)
                next_configuration["allowed_repositories"] = sorted(
                    set(next_configuration["allowed_repositories"])
                )
                outcome = "CREATED"

            snapshot_values = dict(connector)
            snapshot_values["configuration"] = next_configuration
            snapshot = await self._snapshot(session, current, snapshot_values)
            if outcome == "UNCHANGED":
                versions = list(
                    await session.scalars(
                        select(ConnectorConfigurationVersion)
                        .where(
                            ConnectorConfigurationVersion.organization_id
                            == current.organization_id,
                            ConnectorConfigurationVersion.connector_id == connector_id,
                        )
                        .order_by(desc(ConnectorConfigurationVersion.created_at))
                    )
                )
                for version in versions:
                    revoked = await session.scalar(
                        select(ConnectorVersionRevocation.connector_version_id).where(
                            ConnectorVersionRevocation.organization_id == current.organization_id,
                            ConnectorVersionRevocation.connector_version_id == version.id,
                        )
                    )
                    if revoked is None and json.dumps(
                        {
                            key: getattr(version, key)
                            for key in (
                                "connector_type",
                                "environment",
                                "endpoint",
                                "configuration",
                                "credential_ref",
                                "declared_grants",
                                "allowed_egress",
                            )
                        },
                        sort_keys=True,
                        allow_nan=False,
                    ) == json.dumps(snapshot, sort_keys=True, allow_nan=False):
                        return version.id, "UNCHANGED"
            if outcome == "CREATED":
                await session.execute(
                    update(Connector)
                    .where(
                        Connector.id == connector_id,
                        Connector.organization_id == current.organization_id,
                    )
                    .values(configuration=next_configuration)
                    .execution_options(synchronize_session=False)
                )
            version = ConnectorConfigurationVersion(
                organization_id=current.organization_id,
                connector_id=connector_id,
                created_by=current.id,
                **snapshot,
            )
            session.add(version)
            await session.flush()
            return version.id, outcome

        return await self._run(
            session,
            actor,
            action="project_source.codeup.map",
            resource={
                "connector_id": str(connector_id),
                "repository_id": str(repository_id),
            },
            operation=operation,
        )

    async def register_source(
        self,
        session: AsyncSession,
        actor: Principal,
        *,
        workspace_id: UUID,
        repository_id: UUID,
        connector_version_id: UUID,
        provider_repository_id: str,
    ) -> SourceManagementResult:
        async def operation(current: Principal) -> tuple[UUID, Outcome]:
            if not valid_repository_id(provider_repository_id):
                raise _Rejected("binding_invalid", "INVALID")
            version = await self._version(session, current, connector_version_id, usable=True)
            await self._project_access(session, current, workspace_id, repository_id, usable=True)
            configuration = version["configuration"]
            if version["connector_type"] == "codeup":
                repositories = configuration.get("repositories")
                matches = isinstance(repositories, dict) and any(
                    isinstance(mapping, dict)
                    and mapping.get("id") == provider_repository_id
                    and mapping.get("repository_id") == str(repository_id)
                    for mapping in repositories.values()
                )
            else:
                matches = provider_repository_id in configuration["allowed_repositories"]
            if not matches:
                raise _Rejected("source_unavailable")
            source = (
                (
                    await session.execute(
                        select(ProjectSource.__table__).where(
                            ProjectSource.organization_id == current.organization_id,
                            ProjectSource.workspace_id == workspace_id,
                            ProjectSource.repository_id == repository_id,
                            ProjectSource.connector_version_id == connector_version_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if source is not None:
                revoked = await session.scalar(
                    select(ProjectSourceRevocation.source_id).where(
                        ProjectSourceRevocation.source_id == source["id"],
                        ProjectSourceRevocation.organization_id == current.organization_id,
                    )
                )
                if revoked is not None:
                    raise _Rejected("source_unavailable")
                if source["provider_repository_id"] != provider_repository_id:
                    raise _Rejected("binding_conflict", "CONFLICT")
                return source["id"], "UNCHANGED"
            record = ProjectSource(
                organization_id=current.organization_id,
                workspace_id=workspace_id,
                repository_id=repository_id,
                connector_version_id=connector_version_id,
                provider_repository_id=provider_repository_id,
                created_by=current.id,
            )
            session.add(record)
            await session.flush()
            return record.id, "CREATED"

        return await self._run(
            session,
            actor,
            action="project_source.register",
            resource={
                "workspace_id": str(workspace_id),
                "repository_id": str(repository_id),
                "connector_version_id": str(connector_version_id),
            },
            operation=operation,
        )

    async def revoke_connector_version(
        self,
        session: AsyncSession,
        actor: Principal,
        *,
        connector_version_id: UUID,
        reason: RevocationReason,
    ) -> SourceManagementResult:
        async def operation(current: Principal) -> tuple[UUID, Outcome]:
            if reason not in _REASONS:
                raise _Rejected("revocation_invalid", "INVALID")
            await self._version(session, current, connector_version_id, usable=False)
            existing = await session.scalar(
                select(ConnectorVersionRevocation.connector_version_id).where(
                    ConnectorVersionRevocation.connector_version_id == connector_version_id,
                    ConnectorVersionRevocation.organization_id == current.organization_id,
                )
            )
            if existing is not None:
                return connector_version_id, "UNCHANGED"
            session.add(
                ConnectorVersionRevocation(
                    organization_id=current.organization_id,
                    connector_version_id=connector_version_id,
                    revoked_by=current.id,
                    reason_code=reason,
                )
            )
            await session.flush()
            return connector_version_id, "CREATED"

        return await self._run(
            session,
            actor,
            action="project_source.version.revoke",
            resource={"connector_version_id": str(connector_version_id)},
            operation=operation,
        )

    async def revoke_source(
        self,
        session: AsyncSession,
        actor: Principal,
        *,
        source_id: UUID,
        reason: RevocationReason,
    ) -> SourceManagementResult:
        async def operation(current: Principal) -> tuple[UUID, Outcome]:
            if reason not in _REASONS:
                raise _Rejected("revocation_invalid", "INVALID")
            source = (
                (
                    await session.execute(
                        select(ProjectSource.__table__).where(
                            ProjectSource.id == source_id,
                            ProjectSource.organization_id == current.organization_id,
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if source is None:
                raise _Rejected("source_unavailable")
            await self._version(session, current, source["connector_version_id"], usable=False)
            await self._project_access(
                session, current, source["workspace_id"], source["repository_id"], usable=False
            )
            existing = await session.scalar(
                select(ProjectSourceRevocation.source_id).where(
                    ProjectSourceRevocation.source_id == source_id,
                    ProjectSourceRevocation.organization_id == current.organization_id,
                )
            )
            if existing is not None:
                return source_id, "UNCHANGED"
            session.add(
                ProjectSourceRevocation(
                    organization_id=current.organization_id,
                    source_id=source_id,
                    revoked_by=current.id,
                    reason_code=reason,
                )
            )
            await session.flush()
            return source_id, "CREATED"

        return await self._run(
            session,
            actor,
            action="project_source.revoke",
            resource={"source_id": str(source_id)},
            operation=operation,
        )
