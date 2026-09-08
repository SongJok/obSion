"""Bind a verified project snapshot to one Harness Run.

The service is intentionally internal. A source adapter must obtain the
snapshot through the Capability Gateway first, then hand the raw commit and
validated ``ProjectSnapshot`` to this boundary. Callers cannot pin a caller
supplied hash, mutable branch, or unverified Code Graph record.
"""

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.code_intelligence.service import repository_access_clause
from obsion.common.errors import AuthorizationError, ConflictError, ValidationError
from obsion.common.ids import new_id
from obsion.db.models import CodeRepository, Connector, Thread, Turn
from obsion.db.project_source_models import (
    ConnectorConfigurationVersion,
    ProjectSource,
    RunSourcePin,
)
from obsion.domain.enums import ActorType, DecisionEffect, RiskLevel, RunStatus
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.persistence.events import EventDraft, EventStore
from obsion.sandbox.git_integrity import verify_git_snapshot
from obsion.sandbox.project import ProjectRejected, ProjectSnapshot
from obsion.sandbox.source_state import check_project_source_state
from obsion.security.auth import load_principal_by_id
from obsion.security.identity import Principal
from obsion.security.policy import PolicyEngine, ResourcePolicyInput
from obsion.security.workspace_access import require_run_access

PinOutcome = Literal["CREATED", "UNCHANGED"]


@dataclass(frozen=True, slots=True)
class RunSourcePinResult:
    outcome: PinOutcome
    pin: RunSourcePin


class RunSourcePinService:
    """Persist one source revision after all local authorization checks pass."""

    def __init__(self, *, events: EventStore | None = None, audit: AuditWriter | None = None):
        self.events = events or EventStore()
        self.audit = audit or AuditWriter()
        self.policy = PolicyEngine()

    async def pin_verified_snapshot(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        run_id: UUID,
        source_id: UUID,
        snapshot: ProjectSnapshot,
        raw_commit: bytes,
        environment: str,
    ) -> RunSourcePinResult:
        """Pin a Gateway-fetched and Git-verified snapshot to ``run_id``.

        The method owns no external credentials and performs no vendor I/O. It
        rechecks organization, Run/workspace access, repository ACL, source
        revocation, connector configuration drift, and the exact Git object
        bytes in one caller-owned transaction.
        """

        if not session.in_transaction():
            raise ValidationError("project_source_invalid", "项目来源绑定需要显式事务")
        # A caller may catch errors and commit other work. Never leave a pin
        # without its event, policy decision and audit in that transaction.
        async with session.begin_nested():
            return await self._pin(
                session,
                principal,
                run_id=run_id,
                source_id=source_id,
                snapshot=snapshot,
                raw_commit=raw_commit,
                environment=environment,
            )

    async def _pin(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        run_id: UUID,
        source_id: UUID,
        snapshot: ProjectSnapshot,
        raw_commit: bytes,
        environment: str,
    ) -> RunSourcePinResult:
        if not isinstance(snapshot, ProjectSnapshot) or not isinstance(raw_commit, bytes):
            raise ValidationError("project_source_invalid", "项目来源快照格式无效")
        if not isinstance(environment, str) or environment not in {
            "test",
            "development",
            "staging",
        }:
            raise ValidationError("project_source_invalid", "项目来源运行环境无效")
        try:
            verify_git_snapshot(snapshot, raw_commit=raw_commit)
        except ProjectRejected:
            # Do not expose file paths, commit messages, or vendor response data.
            raise ValidationError("project_source_invalid", "项目来源版本完整性校验失败") from None

        # Match source management's Connector -> Workspace -> Repository order.
        # Revocations/configuration updates serialize on this Connector row.
        connector_id = await session.scalar(
            select(Connector.id)
            .join(
                ConnectorConfigurationVersion,
                ConnectorConfigurationVersion.connector_id == Connector.id,
            )
            .join(
                ProjectSource,
                ProjectSource.connector_version_id == ConnectorConfigurationVersion.id,
            )
            .where(
                ProjectSource.id == source_id,
                ProjectSource.organization_id == principal.organization_id,
                ConnectorConfigurationVersion.organization_id == principal.organization_id,
                Connector.organization_id == principal.organization_id,
            )
            .with_for_update(of=Connector)
        )
        if connector_id is None:
            raise AuthorizationError("project_source_denied", "项目来源不可用")
        # Ignore caller-supplied permissions, including stale administrator roles.
        try:
            principal = await load_principal_by_id(session, principal.organization_id, principal.id)
        except AuthorizationError:
            raise AuthorizationError("project_source_denied", "项目来源访问主体不可用") from None
        run = await require_run_access(
            session,
            principal,
            run_id,
            write=True,
            for_update=True,
        )
        await session.refresh(run, attribute_names=["status"])
        if run.status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            raise ConflictError(
                "project_source_conflict",
                "终态 Run 不能新增项目来源版本绑定",
            )
        turn = await session.scalar(
            select(Turn).where(
                Turn.id == run.turn_id,
                Turn.organization_id == principal.organization_id,
            )
        )
        if turn is None:
            raise AuthorizationError("project_source_denied", "项目来源不可用")
        thread = await session.scalar(
            select(Thread).where(
                Thread.id == turn.thread_id,
                Thread.organization_id == principal.organization_id,
            )
        )
        if thread is None:
            raise AuthorizationError("project_source_denied", "项目来源不可用")

        revision = snapshot.revision
        if (
            revision.organization_id != principal.organization_id
            or revision.workspace_id != thread.workspace_id
        ):
            raise AuthorizationError("project_source_denied", "项目来源不属于当前工作区")

        source = await session.scalar(
            select(ProjectSource)
            .where(
                ProjectSource.id == source_id,
                ProjectSource.organization_id == principal.organization_id,
            )
            .with_for_update()
        )
        if (
            source is None
            or source.workspace_id != revision.workspace_id
            or source.repository_id != revision.repository_id
            or source.connector_version_id != revision.connector_version_id
        ):
            raise AuthorizationError("project_source_denied", "项目来源不可用")

        repository = await session.scalar(
            select(CodeRepository.id)
            .where(
                CodeRepository.id == revision.repository_id,
                CodeRepository.organization_id == principal.organization_id,
                repository_access_clause(principal),
            )
            .with_for_update()
        )
        if repository is None:
            raise AuthorizationError("project_source_denied", "项目仓库访问未授权")

        try:
            await check_project_source_state(session, revision, environment=environment)
        except ProjectRejected:
            raise AuthorizationError(
                "project_source_denied", "项目来源已撤销或配置已变化"
            ) from None

        decision = await self.policy.evaluate_resource(
            session,
            ResourcePolicyInput(
                principal=principal,
                action="run.source.pin",
                resource_type="run_source_pin",
                resource={
                    "run_id": str(run.id),
                    "source_id": str(source.id),
                    "workspace_id": str(revision.workspace_id),
                    "repository_id": str(revision.repository_id),
                    "connector_version_id": str(revision.connector_version_id),
                },
                context={"entrypoint": "run-source-pin", "environment": environment},
                risk_level=RiskLevel.L2,
                run_id=run.id,
            ),
        )
        if decision.effect != DecisionEffect.ALLOW or decision.obligations:
            raise AuthorizationError("project_source_denied", "项目来源版本绑定未获策略允许")

        existing = await session.scalar(
            select(RunSourcePin)
            .where(
                RunSourcePin.organization_id == principal.organization_id,
                RunSourcePin.run_id == run.id,
                RunSourcePin.source_id == source.id,
            )
            .with_for_update()
        )
        values = {
            "connector_version_id": revision.connector_version_id,
            "workspace_id": revision.workspace_id,
            "repository_id": revision.repository_id,
            "commit_id": revision.commit_id,
            "tree_id": revision.tree_id,
            "snapshot_fingerprint": snapshot.fingerprint,
            "file_count": len(snapshot.files),
            "snapshot_bytes": sum(len(item.content) for item in snapshot.files),
        }
        if existing is not None:
            if all(getattr(existing, key) == value for key, value in values.items()):
                return RunSourcePinResult("UNCHANGED", existing)
            raise ConflictError(
                "project_source_conflict",
                "Run 已绑定到不同的项目来源版本",
            )

        pin = RunSourcePin(
            organization_id=principal.organization_id,
            run_id=run.id,
            source_id=source.id,
            pinned_by=principal.id,
            **values,
        )
        session.add(pin)
        await session.flush()
        correlation_id = new_id()
        await self.events.append(
            session,
            EventDraft(
                name="run.source_pinned",
                aggregate_type="run",
                aggregate_id=run.id,
                organization_id=principal.organization_id,
                correlation_id=correlation_id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                run_id=run.id,
                payload={
                    "source_id": str(source.id),
                    "connector_version_id": str(revision.connector_version_id),
                    "repository_id": str(revision.repository_id),
                    "commit_id": revision.commit_id,
                    "tree_id": revision.tree_id,
                    "snapshot_fingerprint": snapshot.fingerprint,
                    "file_count": len(snapshot.files),
                    "snapshot_bytes": values["snapshot_bytes"],
                },
            ),
        )
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=correlation_id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action="run.source.pin",
                resource_type="run_source_pin",
                resource_id=str(pin.id),
                outcome="SUCCESS",
                risk_level=RiskLevel.L2,
                policy_decision_id=decision.id,
                metadata={
                    "run_id": str(run.id),
                    "source_id": str(source.id),
                    "connector_version_id": str(revision.connector_version_id),
                    "commit_id": revision.commit_id,
                    "tree_id": revision.tree_id,
                    "snapshot_fingerprint": snapshot.fingerprint,
                },
            ),
        )
        return RunSourcePinResult("CREATED", pin)
