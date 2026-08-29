import hashlib
import json
from datetime import datetime
from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.collaboration.schemas import (
    CreateWorkspaceDecisionRequest,
    CreateWorkspaceTaskRequest,
    DecisionContentRequest,
    ReviseWorkspaceDecisionRequest,
    UpdateWorkspaceTaskRequest,
)
from obsion.common.errors import ConflictError, NotFoundError, ValidationError
from obsion.common.ids import new_id
from obsion.common.time import ensure_utc, utc_now
from obsion.db.models import (
    Event,
    Run,
    Thread,
    Turn,
    User,
    Workspace,
    WorkspaceDecision,
    WorkspaceDecisionVersion,
    WorkspaceMember,
    WorkspaceTask,
)
from obsion.domain.enums import (
    ActorType,
    WorkspaceDecisionStatus,
    WorkspaceTaskStatus,
)
from obsion.domain.workspace_collaboration import (
    validate_decision_transition,
    validate_task_transition,
)
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.persistence.events import EventDraft, EventStore
from obsion.security.identity import Principal
from obsion.security.redaction import redact_text
from obsion.security.workspace_access import require_run_access, require_workspace_access


class WorkspaceCollaborationService:
    def __init__(
        self,
        event_store: EventStore | None = None,
        audit: AuditWriter | None = None,
    ) -> None:
        self.events = event_store or EventStore()
        self.audit = audit or AuditWriter()

    async def create_task(
        self,
        session: AsyncSession,
        principal: Principal,
        workspace_id: UUID,
        request: CreateWorkspaceTaskRequest,
    ) -> WorkspaceTask:
        workspace = await require_workspace_access(session, principal, workspace_id, write=True)
        if request.assignee_id is not None:
            await self._require_assignee(session, principal, workspace, request.assignee_id)
        if request.source_run_id is not None:
            await self._require_source_run(session, principal, workspace.id, request.source_run_id)
        task = WorkspaceTask(
            organization_id=principal.organization_id,
            workspace_id=workspace.id,
            title=redact_text(request.title.strip()),
            description=redact_text(request.description.strip()),
            priority=request.priority,
            assignee_id=request.assignee_id,
            created_by=principal.id,
            source_run_id=request.source_run_id,
            due_at=ensure_utc(request.due_at) if request.due_at else None,
            status=WorkspaceTaskStatus.OPEN,
            version=1,
        )
        session.add(task)
        await session.flush()
        await self._record(
            session,
            principal,
            workspace,
            "workspace_task.created",
            "workspace_task.create",
            "workspace_task",
            task.id,
            task.source_run_id,
            {
                "workspace_id": str(workspace.id),
                "status": task.status,
                "priority": task.priority,
                "version": task.version,
            },
        )
        return task

    async def list_tasks(
        self,
        session: AsyncSession,
        principal: Principal,
        workspace_id: UUID,
        *,
        status: WorkspaceTaskStatus | None = None,
        assignee_id: UUID | None = None,
        limit: int = 200,
    ) -> list[WorkspaceTask]:
        await require_workspace_access(session, principal, workspace_id)
        statement = select(WorkspaceTask).where(
            WorkspaceTask.organization_id == principal.organization_id,
            WorkspaceTask.workspace_id == workspace_id,
        )
        if status is not None:
            statement = statement.where(WorkspaceTask.status == status)
        if assignee_id is not None:
            statement = statement.where(WorkspaceTask.assignee_id == assignee_id)
        rows = await session.scalars(
            statement.order_by(WorkspaceTask.updated_at.desc(), WorkspaceTask.id).limit(limit)
        )
        return list(rows)

    async def update_task(
        self,
        session: AsyncSession,
        principal: Principal,
        task_id: UUID,
        request: UpdateWorkspaceTaskRequest,
    ) -> WorkspaceTask:
        task, workspace = await self._task(session, principal, task_id, write=True, for_update=True)
        self._check_version("workspace_task", task.id, task.version, request.expected_version)
        changed: list[str] = []

        if "title" in request.model_fields_set:
            if request.title is None:
                raise ValidationError("workspace_task_title_required", "Task title cannot be null")
            title = redact_text(request.title.strip())
            if title != task.title:
                task.title = title
                changed.append("title")
        if "description" in request.model_fields_set:
            if request.description is None:
                raise ValidationError(
                    "workspace_task_description_required", "Task description cannot be null"
                )
            description = redact_text(request.description.strip())
            if description != task.description:
                task.description = description
                changed.append("description")
        if "priority" in request.model_fields_set:
            if request.priority is None:
                raise ValidationError(
                    "workspace_task_priority_required", "Task priority cannot be null"
                )
            if request.priority != task.priority:
                task.priority = request.priority
                changed.append("priority")
        if "assignee_id" in request.model_fields_set:
            if request.assignee_id is not None:
                await self._require_assignee(session, principal, workspace, request.assignee_id)
            if request.assignee_id != task.assignee_id:
                task.assignee_id = request.assignee_id
                changed.append("assignee_id")
        if "due_at" in request.model_fields_set:
            due_at = ensure_utc(request.due_at) if request.due_at else None
            if due_at != task.due_at:
                task.due_at = due_at
                changed.append("due_at")
        if "status" in request.model_fields_set:
            if request.status is None:
                raise ValidationError(
                    "workspace_task_status_required", "Task status cannot be null"
                )
            if request.status != task.status:
                validate_task_transition(task.status, request.status)
                previous = task.status
                task.status = request.status
                task.completed_at = (
                    utc_now() if request.status == WorkspaceTaskStatus.COMPLETED else None
                )
                changed.extend(["status", "completed_at"])
                status_change = {"previous_status": previous, "status": request.status}
            else:
                status_change = {"status": task.status}
        else:
            status_change = {"status": task.status}

        if not changed:
            raise ConflictError(
                "workspace_task_no_changes", "The task update does not change the record"
            )
        task.version += 1
        await session.flush()
        await self._record(
            session,
            principal,
            workspace,
            "workspace_task.updated",
            "workspace_task.update",
            "workspace_task",
            task.id,
            task.source_run_id,
            {
                "workspace_id": str(workspace.id),
                "changed_fields": sorted(set(changed)),
                "version": task.version,
                **status_change,
            },
        )
        return task

    async def create_decision(
        self,
        session: AsyncSession,
        principal: Principal,
        workspace_id: UUID,
        request: CreateWorkspaceDecisionRequest,
    ) -> tuple[WorkspaceDecision, WorkspaceDecisionVersion]:
        workspace = await require_workspace_access(session, principal, workspace_id, write=True)
        if request.source_run_id is not None:
            await self._require_source_run(session, principal, workspace.id, request.source_run_id)
        if request.supersedes_decision_id is not None:
            superseded, _ = await self._decision(
                session,
                principal,
                request.supersedes_decision_id,
                write=True,
                for_update=True,
            )
            if superseded.workspace_id != workspace.id:
                raise ValidationError(
                    "workspace_decision_supersedes_workspace_mismatch",
                    "A decision can only supersede a decision in the same workspace",
                )
            if superseded.status != WorkspaceDecisionStatus.ACCEPTED:
                raise ConflictError(
                    "workspace_decision_supersedes_not_accepted",
                    "Only an accepted decision can be superseded",
                    current_status=superseded.status,
                )

        decision = WorkspaceDecision(
            organization_id=principal.organization_id,
            workspace_id=workspace.id,
            status=WorkspaceDecisionStatus.PROPOSED,
            current_version=1,
            created_by=principal.id,
            source_run_id=request.source_run_id,
            supersedes_decision_id=request.supersedes_decision_id,
        )
        session.add(decision)
        await session.flush()
        version = self._new_decision_version(
            principal, decision.id, 1, request, created_at=utc_now()
        )
        session.add(version)
        await session.flush()
        await self._record(
            session,
            principal,
            workspace,
            "workspace_decision.proposed",
            "workspace_decision.create",
            "workspace_decision",
            decision.id,
            decision.source_run_id,
            {
                "workspace_id": str(workspace.id),
                "version": 1,
                "checksum_sha256": version.checksum_sha256,
                "supersedes_decision_id": (
                    str(decision.supersedes_decision_id)
                    if decision.supersedes_decision_id
                    else None
                ),
            },
        )
        return decision, version

    async def list_decisions(
        self,
        session: AsyncSession,
        principal: Principal,
        workspace_id: UUID,
        *,
        status: WorkspaceDecisionStatus | None = None,
        limit: int = 200,
    ) -> list[tuple[WorkspaceDecision, WorkspaceDecisionVersion]]:
        await require_workspace_access(session, principal, workspace_id)
        statement = (
            select(WorkspaceDecision, WorkspaceDecisionVersion)
            .join(
                WorkspaceDecisionVersion,
                (WorkspaceDecisionVersion.decision_id == WorkspaceDecision.id)
                & (WorkspaceDecisionVersion.version == WorkspaceDecision.current_version),
            )
            .where(
                WorkspaceDecision.organization_id == principal.organization_id,
                WorkspaceDecision.workspace_id == workspace_id,
                WorkspaceDecisionVersion.organization_id == principal.organization_id,
            )
        )
        if status is not None:
            statement = statement.where(WorkspaceDecision.status == status)
        rows = await session.execute(
            statement.order_by(WorkspaceDecision.updated_at.desc(), WorkspaceDecision.id).limit(
                limit
            )
        )
        return [(decision, version) for decision, version in rows.all()]

    async def list_decision_versions(
        self,
        session: AsyncSession,
        principal: Principal,
        decision_id: UUID,
    ) -> list[WorkspaceDecisionVersion]:
        await self._decision(session, principal, decision_id)
        rows = await session.scalars(
            select(WorkspaceDecisionVersion)
            .where(
                WorkspaceDecisionVersion.organization_id == principal.organization_id,
                WorkspaceDecisionVersion.decision_id == decision_id,
            )
            .order_by(WorkspaceDecisionVersion.version.desc())
        )
        return list(rows)

    async def revise_decision(
        self,
        session: AsyncSession,
        principal: Principal,
        decision_id: UUID,
        request: ReviseWorkspaceDecisionRequest,
    ) -> tuple[WorkspaceDecision, WorkspaceDecisionVersion]:
        decision, workspace = await self._decision(
            session, principal, decision_id, write=True, for_update=True
        )
        self._check_version(
            "workspace_decision",
            decision.id,
            decision.current_version,
            request.expected_version,
        )
        if decision.status != WorkspaceDecisionStatus.PROPOSED:
            raise ConflictError(
                "workspace_decision_revision_closed",
                "Only a proposed decision can be revised",
                current_status=decision.status,
            )
        current = await session.scalar(
            select(WorkspaceDecisionVersion).where(
                WorkspaceDecisionVersion.decision_id == decision.id,
                WorkspaceDecisionVersion.version == decision.current_version,
            )
        )
        if current is None:
            raise ConflictError(
                "workspace_decision_version_missing",
                "The current decision version is unavailable",
            )
        next_version = decision.current_version + 1
        version = self._new_decision_version(
            principal, decision.id, next_version, request, created_at=utc_now()
        )
        if version.checksum_sha256 == current.checksum_sha256:
            raise ConflictError(
                "workspace_decision_no_changes",
                "The decision revision does not change the governed content",
            )
        decision.current_version = next_version
        session.add(version)
        await session.flush()
        await self._record(
            session,
            principal,
            workspace,
            "workspace_decision.revised",
            "workspace_decision.revise",
            "workspace_decision",
            decision.id,
            decision.source_run_id,
            {
                "workspace_id": str(workspace.id),
                "version": next_version,
                "previous_version": next_version - 1,
                "checksum_sha256": version.checksum_sha256,
            },
        )
        return decision, version

    async def decide(
        self,
        session: AsyncSession,
        principal: Principal,
        decision_id: UUID,
        expected_version: int,
        target: WorkspaceDecisionStatus,
    ) -> tuple[WorkspaceDecision, WorkspaceDecisionVersion]:
        if target not in {
            WorkspaceDecisionStatus.ACCEPTED,
            WorkspaceDecisionStatus.REJECTED,
        }:
            raise ValidationError(
                "workspace_decision_target_invalid",
                "A proposed decision can only be accepted or rejected",
            )
        decision, workspace = await self._decision(
            session, principal, decision_id, write=True, for_update=True
        )
        self._check_version(
            "workspace_decision", decision.id, decision.current_version, expected_version
        )
        validate_decision_transition(decision.status, target)
        correlation_id = new_id()
        if target == WorkspaceDecisionStatus.ACCEPTED and decision.supersedes_decision_id:
            superseded, superseded_workspace = await self._decision(
                session,
                principal,
                decision.supersedes_decision_id,
                write=True,
                for_update=True,
            )
            if superseded.workspace_id != decision.workspace_id:
                raise ValidationError(
                    "workspace_decision_supersedes_workspace_mismatch",
                    "The superseded decision no longer belongs to this workspace",
                )
            validate_decision_transition(superseded.status, WorkspaceDecisionStatus.SUPERSEDED)
            superseded.status = WorkspaceDecisionStatus.SUPERSEDED
            await session.flush()
            await self._record(
                session,
                principal,
                superseded_workspace,
                "workspace_decision.superseded",
                "workspace_decision.supersede",
                "workspace_decision",
                superseded.id,
                superseded.source_run_id,
                {
                    "workspace_id": str(workspace.id),
                    "superseded_by_decision_id": str(decision.id),
                    "version": superseded.current_version,
                },
                correlation_id=correlation_id,
            )

        decision.status = target
        decision.decided_by = principal.id
        decision.decided_at = utc_now()
        await session.flush()
        version = await session.scalar(
            select(WorkspaceDecisionVersion).where(
                WorkspaceDecisionVersion.decision_id == decision.id,
                WorkspaceDecisionVersion.version == decision.current_version,
            )
        )
        if version is None:
            raise ConflictError(
                "workspace_decision_version_missing",
                "The current decision version is unavailable",
            )
        await self._record(
            session,
            principal,
            workspace,
            (
                "workspace_decision.accepted"
                if target == WorkspaceDecisionStatus.ACCEPTED
                else "workspace_decision.rejected"
            ),
            (
                "workspace_decision.accept"
                if target == WorkspaceDecisionStatus.ACCEPTED
                else "workspace_decision.reject"
            ),
            "workspace_decision",
            decision.id,
            decision.source_run_id,
            {
                "workspace_id": str(workspace.id),
                "status": target,
                "version": decision.current_version,
                "checksum_sha256": version.checksum_sha256,
            },
            correlation_id=correlation_id,
        )
        return decision, version

    async def list_task_events(
        self,
        session: AsyncSession,
        principal: Principal,
        task_id: UUID,
        *,
        after_sequence: int,
        limit: int,
    ) -> list[Event]:
        await self._task(session, principal, task_id)
        return await self.events.list_aggregate(
            session,
            principal.organization_id,
            "workspace_task",
            task_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    async def list_decision_events(
        self,
        session: AsyncSession,
        principal: Principal,
        decision_id: UUID,
        *,
        after_sequence: int,
        limit: int,
    ) -> list[Event]:
        await self._decision(session, principal, decision_id)
        return await self.events.list_aggregate(
            session,
            principal.organization_id,
            "workspace_decision",
            decision_id,
            after_sequence=after_sequence,
            limit=limit,
        )

    async def _task(
        self,
        session: AsyncSession,
        principal: Principal,
        task_id: UUID,
        *,
        write: bool = False,
        for_update: bool = False,
    ) -> tuple[WorkspaceTask, Workspace]:
        statement = select(WorkspaceTask).where(
            WorkspaceTask.id == task_id,
            WorkspaceTask.organization_id == principal.organization_id,
        )
        if for_update:
            statement = statement.with_for_update()
        task = await session.scalar(statement)
        if task is None:
            raise NotFoundError("WorkspaceTask", task_id)
        workspace = await require_workspace_access(
            session, principal, task.workspace_id, write=write
        )
        return task, workspace

    async def _decision(
        self,
        session: AsyncSession,
        principal: Principal,
        decision_id: UUID,
        *,
        write: bool = False,
        for_update: bool = False,
    ) -> tuple[WorkspaceDecision, Workspace]:
        statement = select(WorkspaceDecision).where(
            WorkspaceDecision.id == decision_id,
            WorkspaceDecision.organization_id == principal.organization_id,
        )
        if for_update:
            statement = statement.with_for_update()
        decision = await session.scalar(statement)
        if decision is None:
            raise NotFoundError("WorkspaceDecision", decision_id)
        workspace = await require_workspace_access(
            session, principal, decision.workspace_id, write=write
        )
        return decision, workspace

    async def _require_source_run(
        self,
        session: AsyncSession,
        principal: Principal,
        workspace_id: UUID,
        run_id: UUID,
    ) -> None:
        await require_run_access(session, principal, run_id)
        source_workspace_id = await session.scalar(
            select(Thread.workspace_id)
            .select_from(Run)
            .join(Turn, Turn.id == Run.turn_id)
            .join(Thread, Thread.id == Turn.thread_id)
            .where(
                Run.id == run_id,
                Run.organization_id == principal.organization_id,
            )
        )
        if source_workspace_id != workspace_id:
            raise ValidationError(
                "workspace_source_run_mismatch",
                "The source run must belong to the same workspace",
            )

    async def _require_assignee(
        self,
        session: AsyncSession,
        principal: Principal,
        workspace: Workspace,
        assignee_id: UUID,
    ) -> None:
        member = exists(
            select(WorkspaceMember.user_id).where(
                WorkspaceMember.organization_id == principal.organization_id,
                WorkspaceMember.workspace_id == workspace.id,
                WorkspaceMember.user_id == User.id,
            )
        )
        allowed = await session.scalar(
            select(User.id).where(
                User.id == assignee_id,
                User.organization_id == principal.organization_id,
                User.active.is_(True),
                or_(User.id == workspace.owner_id, member),
            )
        )
        if allowed is None:
            raise ValidationError(
                "workspace_task_assignee_invalid",
                "The assignee must be an active workspace member",
            )

    def _new_decision_version(
        self,
        principal: Principal,
        decision_id: UUID,
        version: int,
        content: DecisionContentRequest,
        *,
        created_at: datetime,
    ) -> WorkspaceDecisionVersion:
        alternatives: list[str] = []
        for raw in content.alternatives:
            value = redact_text(raw.strip())
            if value not in alternatives:
                alternatives.append(value)
        normalized = {
            "title": redact_text(content.title.strip()),
            "summary": redact_text(content.summary.strip()),
            "rationale": redact_text(content.rationale.strip()),
            "alternatives": alternatives,
        }
        canonical = json.dumps(
            normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return WorkspaceDecisionVersion(
            organization_id=principal.organization_id,
            decision_id=decision_id,
            version=version,
            title=normalized["title"],
            summary=normalized["summary"],
            rationale=normalized["rationale"],
            alternatives=normalized["alternatives"],
            created_by=principal.id,
            checksum_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
            created_at=created_at,
        )

    @staticmethod
    def _check_version(aggregate: str, aggregate_id: UUID, current: int, expected: int) -> None:
        if current != expected:
            raise ConflictError(
                f"{aggregate}_version_conflict",
                "The record changed after it was loaded; refresh and retry",
                id=str(aggregate_id),
                expected_version=expected,
                current_version=current,
            )

    async def _record(
        self,
        session: AsyncSession,
        principal: Principal,
        workspace: Workspace,
        event_name: str,
        audit_action: str,
        aggregate_type: str,
        aggregate_id: UUID,
        run_id: UUID | None,
        payload: dict[str, object],
        *,
        correlation_id: UUID | None = None,
    ) -> None:
        correlation = correlation_id or new_id()
        await self.events.append(
            session,
            EventDraft(
                name=event_name,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                organization_id=principal.organization_id,
                correlation_id=correlation,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                run_id=run_id,
                classification=workspace.classification,
                payload=payload,
            ),
        )
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=correlation,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action=audit_action,
                resource_type=aggregate_type,
                resource_id=str(aggregate_id),
                outcome="SUCCESS",
                metadata=payload,
            ),
        )
