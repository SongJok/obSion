import hashlib
import json
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.automation.schemas import (
    CreateScheduleRequest,
    CreateWorkflowRequest,
    ReviewAutomationStepRequest,
    WorkflowSpec,
)
from obsion.common.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from obsion.common.ids import new_id
from obsion.common.time import ensure_utc, utc_now
from obsion.db.models import (
    Artifact,
    AutomationExecution,
    AutomationStepExecution,
    NotificationDelivery,
    Run,
    User,
    WorkflowDefinition,
    WorkflowSchedule,
    WorkflowVersion,
)
from obsion.domain.enums import (
    ActorType,
    AutomationStatus,
    AutomationStepStatus,
    AutomationTrigger,
    NotificationStatus,
    ReviewDecision,
    RunStatus,
    WorkflowConcurrencyPolicy,
    WorkflowStatus,
)
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.persistence.events import EventDraft, EventStore
from obsion.security.auth import load_principal_by_id
from obsion.security.identity import Principal
from obsion.security.workspace_access import require_workspace_access

_ACTIVE_EXECUTION_STATUSES = {
    AutomationStatus.PENDING,
    AutomationStatus.RUNNING,
    AutomationStatus.WAITING_REVIEW,
}
_TERMINAL_EXECUTION_STATUSES = {
    AutomationStatus.COMPLETED,
    AutomationStatus.FAILED,
    AutomationStatus.CANCELLED,
    AutomationStatus.SKIPPED,
}
_TERMINAL_RUN_STATUSES = {
    RunStatus.COMPLETED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
}


def workflow_checksum(spec: dict[str, Any]) -> str:
    payload = json.dumps(spec, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def next_cron_occurrence(expression: str, timezone: str, after: datetime) -> datetime:
    if len(expression.split()) != 5 or not croniter.is_valid(expression):
        raise ValidationError(
            "schedule_cron_invalid", "Schedules require a valid five-field cron expression"
        )
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValidationError(
            "schedule_timezone_invalid", "Schedule timezone must be a valid IANA timezone"
        ) from exc
    local_after = ensure_utc(after).astimezone(zone)
    occurrence = cast(datetime, croniter(expression, local_after).get_next(datetime))
    return occurrence.astimezone(ZoneInfo("UTC"))


class AutomationService:
    def __init__(self) -> None:
        self.events = EventStore()
        self.audit = AuditWriter()

    async def create_workflow(
        self,
        session: AsyncSession,
        principal: Principal,
        workspace_id: UUID,
        request: CreateWorkflowRequest,
    ) -> tuple[WorkflowDefinition, WorkflowVersion]:
        self._require(principal, "automation.manage")
        await require_workspace_access(session, principal, workspace_id, write=True)
        owner_id = request.owner_id or principal.id
        await self._require_owner(session, principal, owner_id, workspace_id)
        if (
            request.concurrency_policy == WorkflowConcurrencyPolicy.FORBID
            and request.max_concurrency != 1
        ):
            raise ValidationError(
                "workflow_concurrency_invalid",
                "FORBID workflows must have max_concurrency set to one",
            )
        existing = await session.scalar(
            select(WorkflowDefinition.id).where(
                WorkflowDefinition.organization_id == principal.organization_id,
                WorkflowDefinition.workspace_id == workspace_id,
                WorkflowDefinition.name == request.name,
            )
        )
        if existing is not None:
            raise ConflictError("workflow_name_exists", "Workflow name already exists")
        now = utc_now()
        workflow = WorkflowDefinition(
            organization_id=principal.organization_id,
            workspace_id=workspace_id,
            name=request.name,
            display_name=request.display_name.strip(),
            description=request.description.strip(),
            status=WorkflowStatus.DRAFT,
            owner_id=owner_id,
            concurrency_policy=request.concurrency_policy,
            max_concurrency=request.max_concurrency,
            timeout_seconds=request.timeout_seconds,
            notify_on_success=request.notify_on_success,
            notify_on_failure=request.notify_on_failure,
            classification=request.classification,
        )
        session.add(workflow)
        await session.flush()
        spec = request.spec.model_dump(mode="json")
        version = WorkflowVersion(
            organization_id=principal.organization_id,
            workflow_id=workflow.id,
            version=1,
            spec=spec,
            checksum_sha256=workflow_checksum(spec),
            created_by=principal.id,
            created_at=now,
        )
        session.add(version)
        await session.flush()
        await self._event(
            session,
            principal.organization_id,
            workflow.id,
            "workflow.created",
            principal,
            {"version": 1, "owner_id": str(owner_id)},
        )
        await self._audit(
            session,
            principal,
            workflow.id,
            "workflow.create",
            {"version": 1, "workspace_id": str(workspace_id)},
        )
        return workflow, version

    async def list_workflows(
        self, session: AsyncSession, principal: Principal, workspace_id: UUID
    ) -> list[WorkflowDefinition]:
        await require_workspace_access(session, principal, workspace_id)
        return list(
            await session.scalars(
                select(WorkflowDefinition)
                .where(
                    WorkflowDefinition.organization_id == principal.organization_id,
                    WorkflowDefinition.workspace_id == workspace_id,
                )
                .order_by(WorkflowDefinition.updated_at.desc())
            )
        )

    async def get_workflow(
        self,
        session: AsyncSession,
        principal: Principal,
        workflow_id: UUID,
        *,
        write: bool = False,
        for_update: bool = False,
    ) -> WorkflowDefinition:
        statement = select(WorkflowDefinition).where(
            WorkflowDefinition.id == workflow_id,
            WorkflowDefinition.organization_id == principal.organization_id,
        )
        if for_update:
            statement = statement.with_for_update()
        workflow = await session.scalar(statement)
        if workflow is None:
            raise NotFoundError("Workflow", workflow_id)
        await require_workspace_access(session, principal, workflow.workspace_id, write=write)
        return workflow

    async def list_versions(
        self, session: AsyncSession, principal: Principal, workflow_id: UUID
    ) -> list[WorkflowVersion]:
        await self.get_workflow(session, principal, workflow_id)
        return list(
            await session.scalars(
                select(WorkflowVersion)
                .where(
                    WorkflowVersion.organization_id == principal.organization_id,
                    WorkflowVersion.workflow_id == workflow_id,
                )
                .order_by(WorkflowVersion.version.desc())
            )
        )

    async def create_version(
        self,
        session: AsyncSession,
        principal: Principal,
        workflow_id: UUID,
        spec: WorkflowSpec,
    ) -> WorkflowVersion:
        self._require(principal, "automation.manage")
        workflow = await self.get_workflow(
            session, principal, workflow_id, write=True, for_update=True
        )
        if workflow.status == WorkflowStatus.RETIRED:
            raise ConflictError("workflow_retired", "A retired workflow cannot be revised")
        latest = await session.scalar(
            select(func.max(WorkflowVersion.version)).where(
                WorkflowVersion.workflow_id == workflow.id
            )
        )
        version_number = (latest or 0) + 1
        serialized = spec.model_dump(mode="json")
        version = WorkflowVersion(
            organization_id=principal.organization_id,
            workflow_id=workflow.id,
            version=version_number,
            spec=serialized,
            checksum_sha256=workflow_checksum(serialized),
            created_by=principal.id,
            created_at=utc_now(),
        )
        session.add(version)
        await session.flush()
        await self._event(
            session,
            principal.organization_id,
            workflow.id,
            "workflow.version_created",
            principal,
            {"version": version_number},
        )
        await self._audit(
            session,
            principal,
            workflow.id,
            "workflow.version.create",
            {"version": version_number},
        )
        return version

    async def publish_version(
        self,
        session: AsyncSession,
        principal: Principal,
        workflow_id: UUID,
        version_number: int,
    ) -> tuple[WorkflowDefinition, WorkflowVersion]:
        self._require(principal, "automation.manage")
        workflow = await self.get_workflow(
            session, principal, workflow_id, write=True, for_update=True
        )
        if workflow.status == WorkflowStatus.RETIRED:
            raise ConflictError("workflow_retired", "A retired workflow cannot be published")
        version = await session.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.organization_id == principal.organization_id,
                WorkflowVersion.workflow_id == workflow.id,
                WorkflowVersion.version == version_number,
            )
        )
        if version is None:
            raise NotFoundError("Workflow version", version_number)
        if version.published_at is None:
            version.published_at = utc_now()
        workflow.active_version = version.version
        workflow.status = WorkflowStatus.ACTIVE
        await self._event(
            session,
            principal.organization_id,
            workflow.id,
            "workflow.version_published",
            principal,
            {"version": version.version},
        )
        await self._audit(
            session,
            principal,
            workflow.id,
            "workflow.publish",
            {"version": version.version},
        )
        return workflow, version

    async def set_workflow_status(
        self,
        session: AsyncSession,
        principal: Principal,
        workflow_id: UUID,
        target: WorkflowStatus,
    ) -> WorkflowDefinition:
        self._require(principal, "automation.manage")
        workflow = await self.get_workflow(
            session, principal, workflow_id, write=True, for_update=True
        )
        if target not in {WorkflowStatus.ACTIVE, WorkflowStatus.PAUSED, WorkflowStatus.RETIRED}:
            raise ValidationError("workflow_status_invalid", "Unsupported workflow status target")
        if target == WorkflowStatus.ACTIVE and workflow.active_version is None:
            raise ConflictError("workflow_unpublished", "Publish a workflow version first")
        if workflow.status == WorkflowStatus.RETIRED and target != WorkflowStatus.RETIRED:
            raise ConflictError("workflow_retired", "A retired workflow cannot be resumed")
        workflow.status = target
        if target != WorkflowStatus.ACTIVE:
            schedules = await session.scalars(
                select(WorkflowSchedule).where(
                    WorkflowSchedule.organization_id == principal.organization_id,
                    WorkflowSchedule.workflow_id == workflow.id,
                    WorkflowSchedule.enabled.is_(True),
                )
            )
            for schedule in schedules:
                schedule.enabled = False
        await self._event(
            session,
            principal.organization_id,
            workflow.id,
            f"workflow.{target.value.lower()}",
            principal,
            {},
        )
        await self._audit(
            session,
            principal,
            workflow.id,
            "workflow.status.change",
            {"status": target.value},
        )
        return workflow

    async def create_schedule(
        self,
        session: AsyncSession,
        principal: Principal,
        workflow_id: UUID,
        request: CreateScheduleRequest,
    ) -> WorkflowSchedule:
        self._require(principal, "automation.manage")
        workflow = await self.get_workflow(
            session, principal, workflow_id, write=True, for_update=True
        )
        if workflow.status != WorkflowStatus.ACTIVE or workflow.active_version is None:
            raise ConflictError("workflow_not_active", "Only active workflows can be scheduled")
        version_number = request.workflow_version or workflow.active_version
        version = await self._published_version(
            session, principal.organization_id, workflow.id, version_number
        )
        existing = await session.scalar(
            select(WorkflowSchedule.id).where(
                WorkflowSchedule.workflow_id == workflow.id,
                WorkflowSchedule.name == request.name,
            )
        )
        if existing is not None:
            raise ConflictError("schedule_name_exists", "Schedule name already exists")
        now = utc_now()
        schedule = WorkflowSchedule(
            organization_id=principal.organization_id,
            workspace_id=workflow.workspace_id,
            workflow_id=workflow.id,
            workflow_version_id=version.id,
            name=request.name.strip(),
            cron_expression=request.cron_expression.strip(),
            timezone=request.timezone.strip(),
            misfire_policy=request.misfire_policy,
            misfire_grace_seconds=request.misfire_grace_seconds,
            input_payload=request.input_payload,
            owner_id=workflow.owner_id,
            enabled=request.enabled,
            next_fire_at=next_cron_occurrence(
                request.cron_expression.strip(), request.timezone.strip(), now
            ),
            created_by=principal.id,
        )
        session.add(schedule)
        await session.flush()
        await self._event(
            session,
            principal.organization_id,
            workflow.id,
            "schedule.created",
            principal,
            {
                "schedule_id": str(schedule.id),
                "next_fire_at": schedule.next_fire_at.isoformat(),
            },
        )
        await self._audit(
            session,
            principal,
            schedule.id,
            "schedule.create",
            {"workflow_id": str(workflow.id)},
            resource_type="schedule",
        )
        return schedule

    async def list_schedules(
        self, session: AsyncSession, principal: Principal, workflow_id: UUID
    ) -> list[WorkflowSchedule]:
        await self.get_workflow(session, principal, workflow_id)
        return list(
            await session.scalars(
                select(WorkflowSchedule)
                .where(
                    WorkflowSchedule.organization_id == principal.organization_id,
                    WorkflowSchedule.workflow_id == workflow_id,
                )
                .order_by(WorkflowSchedule.created_at.desc())
            )
        )

    async def set_schedule_enabled(
        self,
        session: AsyncSession,
        principal: Principal,
        schedule_id: UUID,
        enabled: bool,
    ) -> WorkflowSchedule:
        self._require(principal, "automation.manage")
        schedule = await session.scalar(
            select(WorkflowSchedule)
            .where(
                WorkflowSchedule.id == schedule_id,
                WorkflowSchedule.organization_id == principal.organization_id,
            )
            .with_for_update()
        )
        if schedule is None:
            raise NotFoundError("Schedule", schedule_id)
        workflow = await self.get_workflow(session, principal, schedule.workflow_id, write=True)
        if enabled and workflow.status != WorkflowStatus.ACTIVE:
            raise ConflictError("workflow_not_active", "Paused workflows cannot enable schedules")
        schedule.enabled = enabled
        schedule.last_error_code = None
        if enabled:
            schedule.next_fire_at = next_cron_occurrence(
                schedule.cron_expression, schedule.timezone, utc_now()
            )
        await self._event(
            session,
            principal.organization_id,
            workflow.id,
            "schedule.enabled" if enabled else "schedule.disabled",
            principal,
            {"schedule_id": str(schedule.id)},
        )
        return schedule

    async def trigger_workflow(
        self,
        session: AsyncSession,
        principal: Principal,
        workflow_id: UUID,
        *,
        input_payload: dict[str, Any],
        idempotency_key: str | None,
        trigger: AutomationTrigger = AutomationTrigger.MANUAL,
        schedule: WorkflowSchedule | None = None,
        scheduled_for: datetime | None = None,
        pinned_version: WorkflowVersion | None = None,
    ) -> AutomationExecution:
        self._require(principal, "automation.trigger")
        workflow = await self.get_workflow(
            session, principal, workflow_id, write=True, for_update=True
        )
        if workflow.status != WorkflowStatus.ACTIVE or workflow.active_version is None:
            raise ConflictError("workflow_not_active", "Only active workflows can be triggered")
        if principal.id != workflow.owner_id and not principal.can("automation.trigger.all"):
            raise AuthorizationError(
                "workflow_owner_required", "Only the accountable owner can trigger this workflow"
            )
        version = pinned_version or await self._published_version(
            session,
            principal.organization_id,
            workflow.id,
            workflow.active_version,
        )
        key = idempotency_key or f"manual:{workflow.id}:{new_id()}"
        existing = await session.scalar(
            select(AutomationExecution).where(
                AutomationExecution.organization_id == principal.organization_id,
                AutomationExecution.idempotency_key == key,
            )
        )
        if existing is not None:
            if existing.workflow_id != workflow.id:
                raise ConflictError(
                    "automation_idempotency_conflict",
                    "The idempotency key belongs to another workflow",
                )
            return existing

        active = list(
            await session.scalars(
                select(AutomationExecution)
                .where(
                    AutomationExecution.organization_id == principal.organization_id,
                    AutomationExecution.workflow_id == workflow.id,
                    AutomationExecution.status.in_(_ACTIVE_EXECUTION_STATUSES),
                )
                .order_by(AutomationExecution.created_at)
                .with_for_update()
            )
        )
        admitted = True
        skip_code: str | None = None
        if workflow.concurrency_policy == WorkflowConcurrencyPolicy.FORBID and active:
            admitted = False
            skip_code = "workflow_concurrency_forbidden"
        elif (
            workflow.concurrency_policy == WorkflowConcurrencyPolicy.ALLOW
            and len(active) >= workflow.max_concurrency
        ):
            admitted = False
            skip_code = "workflow_concurrency_limit"
        elif workflow.concurrency_policy == WorkflowConcurrencyPolicy.REPLACE:
            for current in active:
                await self._cancel_execution_rows(session, current, principal.id)

        now = utc_now()
        execution = AutomationExecution(
            organization_id=principal.organization_id,
            workspace_id=workflow.workspace_id,
            workflow_id=workflow.id,
            workflow_version_id=version.id,
            schedule_id=schedule.id if schedule else None,
            trigger=trigger,
            scheduled_for=scheduled_for,
            idempotency_key=key,
            status=AutomationStatus.PENDING if admitted else AutomationStatus.SKIPPED,
            owner_id=workflow.owner_id,
            input_payload=input_payload,
            max_duration_seconds=workflow.timeout_seconds,
            deadline_at=now + timedelta(seconds=workflow.timeout_seconds),
            completed_at=None if admitted else now,
            error_code=skip_code,
            error_message=None if admitted else "Workflow concurrency policy skipped this trigger",
            summary={} if admitted else {"reason": skip_code},
        )
        session.add(execution)
        await session.flush()
        parsed_spec = WorkflowSpec.model_validate(version.spec)
        for ordinal, step_spec in enumerate(parsed_spec.steps, start=1):
            session.add(
                AutomationStepExecution(
                    organization_id=principal.organization_id,
                    execution_id=execution.id,
                    step_key=step_spec.id,
                    ordinal=ordinal,
                    name=step_spec.name,
                    step_type=step_spec.type,
                    depends_on=step_spec.depends_on,
                    spec=step_spec.model_dump(mode="json"),
                    status=(
                        AutomationStepStatus.PENDING if admitted else AutomationStepStatus.SKIPPED
                    ),
                    completed_at=None if admitted else now,
                    error_code=skip_code,
                )
            )
        await session.flush()
        event_name = "automation.execution_created" if admitted else "automation.execution_skipped"
        await self._event(
            session,
            principal.organization_id,
            execution.id,
            event_name,
            principal,
            {
                "workflow_id": str(workflow.id),
                "workflow_version": version.version,
                "trigger": trigger.value,
                "error_code": skip_code,
            },
            aggregate_type="automation_execution",
        )
        await self._audit(
            session,
            principal,
            execution.id,
            "automation.trigger",
            {"workflow_id": str(workflow.id), "trigger": trigger.value},
            outcome="SUCCESS" if admitted else "SKIPPED",
            resource_type="automation_execution",
        )
        return execution

    async def list_executions(
        self,
        session: AsyncSession,
        principal: Principal,
        workflow_id: UUID,
        *,
        limit: int = 100,
    ) -> list[AutomationExecution]:
        await self.get_workflow(session, principal, workflow_id)
        return list(
            await session.scalars(
                select(AutomationExecution)
                .where(
                    AutomationExecution.organization_id == principal.organization_id,
                    AutomationExecution.workflow_id == workflow_id,
                )
                .order_by(AutomationExecution.created_at.desc())
                .limit(limit)
            )
        )

    async def get_execution(
        self,
        session: AsyncSession,
        principal: Principal,
        execution_id: UUID,
        *,
        write: bool = False,
        for_update: bool = False,
    ) -> AutomationExecution:
        statement = select(AutomationExecution).where(
            AutomationExecution.id == execution_id,
            AutomationExecution.organization_id == principal.organization_id,
        )
        if for_update:
            statement = statement.with_for_update()
        execution = await session.scalar(statement)
        if execution is None:
            raise NotFoundError("Automation execution", execution_id)
        await require_workspace_access(session, principal, execution.workspace_id, write=write)
        return execution

    async def list_steps(
        self, session: AsyncSession, principal: Principal, execution_id: UUID
    ) -> list[AutomationStepExecution]:
        await self.get_execution(session, principal, execution_id)
        return list(
            await session.scalars(
                select(AutomationStepExecution)
                .where(
                    AutomationStepExecution.organization_id == principal.organization_id,
                    AutomationStepExecution.execution_id == execution_id,
                )
                .order_by(AutomationStepExecution.ordinal)
            )
        )

    async def cancel_execution(
        self, session: AsyncSession, principal: Principal, execution_id: UUID
    ) -> AutomationExecution:
        self._require(principal, "automation.trigger")
        execution = await self.get_execution(
            session, principal, execution_id, write=True, for_update=True
        )
        if principal.id != execution.owner_id and not principal.can("automation.trigger.all"):
            raise AuthorizationError(
                "workflow_owner_required",
                "Only the accountable owner can cancel this workflow execution",
            )
        if execution.status in _TERMINAL_EXECUTION_STATUSES:
            return execution
        await self._cancel_execution_rows(session, execution, principal.id)
        await self._event(
            session,
            principal.organization_id,
            execution.id,
            "automation.cancellation_requested",
            principal,
            {},
            aggregate_type="automation_execution",
        )
        await self._audit(
            session,
            principal,
            execution.id,
            "automation.cancel",
            {},
            resource_type="automation_execution",
        )
        return execution

    async def review_step(
        self,
        session: AsyncSession,
        principal: Principal,
        step_id: UUID,
        request: ReviewAutomationStepRequest,
    ) -> AutomationStepExecution:
        self._require(principal, "automation.review")
        step = await session.scalar(
            select(AutomationStepExecution)
            .where(
                AutomationStepExecution.id == step_id,
                AutomationStepExecution.organization_id == principal.organization_id,
            )
            .with_for_update()
        )
        if step is None:
            raise NotFoundError("Automation step", step_id)
        execution = await self.get_execution(
            session, principal, step.execution_id, write=True, for_update=True
        )
        if step.status != AutomationStepStatus.WAITING_REVIEW:
            raise ConflictError(
                "automation_step_not_waiting_review", "The workflow step is not awaiting review"
            )
        spec = step.spec
        if spec.get("disallow_self_review") is True and principal.id == execution.owner_id:
            raise AuthorizationError(
                "automation_self_review_denied", "The workflow owner cannot review this gate"
            )
        now = utc_now()
        step.review_decision = request.decision
        step.reviewed_by = principal.id
        step.review_reason = request.reason.strip()
        step.reviewed_at = now
        step.completed_at = now
        if request.decision == ReviewDecision.APPROVE:
            step.status = AutomationStepStatus.COMPLETED
            execution.status = AutomationStatus.RUNNING
        else:
            step.status = AutomationStepStatus.FAILED
            step.error_code = "human_review_rejected"
            execution.status = AutomationStatus.FAILED
            execution.error_code = step.error_code
            execution.error_message = "A human reviewer rejected the workflow"
            execution.completed_at = now
            await self._cancel_pending_steps(session, execution.id, step.error_code)
            workflow = await session.scalar(
                select(WorkflowDefinition).where(
                    WorkflowDefinition.id == execution.workflow_id,
                    WorkflowDefinition.organization_id == execution.organization_id,
                )
            )
            if workflow is not None and workflow.notify_on_failure:
                await self.deliver_notification(
                    session,
                    execution=execution,
                    title=f"{workflow.display_name} 审核未通过",
                    body="人工审核拒绝了本次自动化运行，请查看审核意见和证据链路。",
                    payload={
                        "workflow_id": str(workflow.id),
                        "status": "FAILED",
                        "code": step.error_code,
                    },
                    idempotency_key=f"execution:{execution.id}:failure",
                )
        execution.lease_owner = None
        execution.lease_expires_at = None
        await self._event(
            session,
            principal.organization_id,
            execution.id,
            "automation.review_decided",
            principal,
            {"step_id": str(step.id), "decision": request.decision.value},
            aggregate_type="automation_execution",
        )
        await self._audit(
            session,
            principal,
            step.id,
            "automation.review",
            {"execution_id": str(execution.id), "decision": request.decision.value},
            resource_type="automation_step",
        )
        return step

    async def list_notifications(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        unread_only: bool = False,
        limit: int = 100,
    ) -> list[NotificationDelivery]:
        self._require(principal, "notification.read")
        statement = select(NotificationDelivery).where(
            NotificationDelivery.organization_id == principal.organization_id
        )
        if not principal.can("notification.read.all"):
            statement = statement.where(NotificationDelivery.recipient_id == principal.id)
        if unread_only:
            statement = statement.where(NotificationDelivery.status == NotificationStatus.DELIVERED)
        return list(
            await session.scalars(
                statement.order_by(NotificationDelivery.created_at.desc()).limit(limit)
            )
        )

    async def mark_notification_read(
        self, session: AsyncSession, principal: Principal, notification_id: UUID
    ) -> NotificationDelivery:
        self._require(principal, "notification.read")
        notification = await session.scalar(
            select(NotificationDelivery)
            .where(
                NotificationDelivery.id == notification_id,
                NotificationDelivery.organization_id == principal.organization_id,
            )
            .with_for_update()
        )
        if notification is None:
            raise NotFoundError("Notification", notification_id)
        if notification.recipient_id != principal.id and not principal.can("notification.read.all"):
            raise NotFoundError("Notification", notification_id)
        if notification.status != NotificationStatus.READ:
            notification.status = NotificationStatus.READ
            notification.read_at = utc_now()
        return notification

    async def deliver_notification(
        self,
        session: AsyncSession,
        *,
        execution: AutomationExecution,
        title: str,
        body: str,
        payload: dict[str, Any],
        idempotency_key: str,
        step: AutomationStepExecution | None = None,
    ) -> NotificationDelivery:
        existing = await session.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.organization_id == execution.organization_id,
                NotificationDelivery.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing
        now = utc_now()
        delivery = NotificationDelivery(
            organization_id=execution.organization_id,
            workspace_id=execution.workspace_id,
            execution_id=execution.id,
            step_execution_id=step.id if step else None,
            recipient_id=execution.owner_id,
            title=title.strip(),
            body=body.strip(),
            payload=payload,
            status=NotificationStatus.DELIVERED,
            idempotency_key=idempotency_key,
            delivered_at=now,
            created_at=now,
        )
        session.add(delivery)
        await session.flush()
        system_principal = Principal(
            id=execution.owner_id,
            organization_id=execution.organization_id,
            external_id="automation-service",
            display_name="Obsion Automation",
        )
        await self._event(
            session,
            execution.organization_id,
            execution.id,
            "notification.delivered",
            system_principal,
            {"notification_id": str(delivery.id), "recipient_id": str(delivery.recipient_id)},
            actor_type=ActorType.SYSTEM,
            aggregate_type="automation_execution",
        )
        await self._audit(
            session,
            system_principal,
            delivery.id,
            "notification.deliver",
            {"execution_id": str(execution.id)},
            resource_type="notification",
            actor_type=ActorType.SYSTEM,
        )
        return delivery

    async def dependency_artifacts(
        self,
        session: AsyncSession,
        execution: AutomationExecution,
        dependencies: list[str],
    ) -> list[Artifact]:
        if not dependencies:
            return []
        run_ids = list(
            await session.scalars(
                select(AutomationStepExecution.run_id).where(
                    AutomationStepExecution.organization_id == execution.organization_id,
                    AutomationStepExecution.execution_id == execution.id,
                    AutomationStepExecution.step_key.in_(dependencies),
                    AutomationStepExecution.run_id.is_not(None),
                )
            )
        )
        if not run_ids:
            return []
        return list(
            await session.scalars(
                select(Artifact)
                .where(
                    Artifact.organization_id == execution.organization_id,
                    Artifact.workspace_id == execution.workspace_id,
                    Artifact.run_id.in_(run_ids),
                )
                .order_by(Artifact.created_at)
            )
        )

    async def _published_version(
        self,
        session: AsyncSession,
        organization_id: UUID,
        workflow_id: UUID,
        version_number: int,
    ) -> WorkflowVersion:
        version = await session.scalar(
            select(WorkflowVersion).where(
                WorkflowVersion.organization_id == organization_id,
                WorkflowVersion.workflow_id == workflow_id,
                WorkflowVersion.version == version_number,
                WorkflowVersion.published_at.is_not(None),
            )
        )
        if version is None:
            raise NotFoundError("Published workflow version", version_number)
        return version

    async def _require_owner(
        self,
        session: AsyncSession,
        principal: Principal,
        owner_id: UUID,
        workspace_id: UUID,
    ) -> Principal:
        if owner_id != principal.id and not principal.can("automation.manage.all"):
            raise AuthorizationError(
                "workflow_owner_assignment_denied", "Cannot assign another workflow owner"
            )
        owner = await load_principal_by_id(session, principal.organization_id, owner_id)
        if not owner.can("automation.trigger"):
            raise ValidationError(
                "workflow_owner_permission_missing",
                "Workflow owner must have automation.trigger permission",
            )
        await require_workspace_access(session, owner, workspace_id, write=True)
        return owner

    async def _cancel_execution_rows(
        self, session: AsyncSession, execution: AutomationExecution, actor_id: UUID
    ) -> None:
        if execution.status in _TERMINAL_EXECUTION_STATUSES:
            return
        now = utc_now()
        execution.cancellation_requested_at = now
        execution.status = AutomationStatus.CANCELLED
        execution.completed_at = now
        execution.lease_owner = None
        execution.lease_expires_at = None
        steps = list(
            await session.scalars(
                select(AutomationStepExecution)
                .where(
                    AutomationStepExecution.organization_id == execution.organization_id,
                    AutomationStepExecution.execution_id == execution.id,
                )
                .with_for_update()
            )
        )
        for step in steps:
            if step.status in {
                AutomationStepStatus.COMPLETED,
                AutomationStepStatus.FAILED,
                AutomationStepStatus.CANCELLED,
                AutomationStepStatus.SKIPPED,
            }:
                continue
            step.status = AutomationStepStatus.CANCELLED
            step.completed_at = now
            step.error_code = "automation_cancelled"
            if step.run_id is not None:
                run = await session.scalar(
                    select(Run)
                    .where(
                        Run.id == step.run_id,
                        Run.organization_id == execution.organization_id,
                    )
                    .with_for_update()
                )
                if run is not None and run.status not in _TERMINAL_RUN_STATUSES:
                    run.cancellation_requested_at = now
                    if run.status == RunStatus.PENDING:
                        run.status = RunStatus.CANCELLED
                        run.completed_at = now
        await self.events.append(
            session,
            EventDraft(
                name="automation.cancelled",
                aggregate_type="automation_execution",
                aggregate_id=execution.id,
                organization_id=execution.organization_id,
                correlation_id=execution.id,
                actor_type=ActorType.USER,
                actor_id=actor_id,
                payload={},
            ),
        )

    async def _cancel_pending_steps(
        self, session: AsyncSession, execution_id: UUID, error_code: str
    ) -> None:
        steps = await session.scalars(
            select(AutomationStepExecution).where(
                AutomationStepExecution.execution_id == execution_id,
                AutomationStepExecution.status.in_(
                    {
                        AutomationStepStatus.PENDING,
                        AutomationStepStatus.WAITING_REVIEW,
                    }
                ),
            )
        )
        now = utc_now()
        for step in steps:
            step.status = AutomationStepStatus.SKIPPED
            step.completed_at = now
            step.error_code = error_code

    @staticmethod
    def _require(principal: Principal, permission: str) -> None:
        if not principal.can(permission):
            raise AuthorizationError(
                "automation_permission_denied", "Automation operation is not permitted"
            )

    async def _event(
        self,
        session: AsyncSession,
        organization_id: UUID,
        aggregate_id: UUID,
        name: str,
        principal: Principal,
        payload: dict[str, Any],
        *,
        actor_type: ActorType = ActorType.USER,
        aggregate_type: str = "workflow",
    ) -> None:
        await self.events.append(
            session,
            EventDraft(
                name=name,
                aggregate_type=aggregate_type,
                aggregate_id=aggregate_id,
                organization_id=organization_id,
                correlation_id=aggregate_id,
                actor_type=actor_type,
                actor_id=principal.id,
                payload=payload,
            ),
        )

    async def _audit(
        self,
        session: AsyncSession,
        principal: Principal,
        resource_id: UUID,
        action: str,
        metadata: dict[str, Any],
        *,
        outcome: str = "SUCCESS",
        resource_type: str = "workflow",
        actor_type: ActorType = ActorType.USER,
    ) -> None:
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=resource_id,
                actor_type=actor_type,
                actor_id=principal.id,
                action=action,
                resource_type=resource_type,
                resource_id=str(resource_id),
                outcome=outcome,
                metadata=metadata,
            ),
        )


async def load_schedule_owner(session: AsyncSession, schedule: WorkflowSchedule) -> Principal:
    owner = await session.scalar(
        select(User).where(
            User.id == schedule.owner_id,
            User.organization_id == schedule.organization_id,
            User.active.is_(True),
        )
    )
    if owner is None:
        raise AuthorizationError("workflow_owner_inactive", "Workflow owner is inactive")
    return await load_principal_by_id(session, schedule.organization_id, schedule.owner_id)
