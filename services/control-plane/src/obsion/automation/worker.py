import asyncio
import os
import re
import socket
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import CreateTurnRequest
from obsion.application.workspaces import WorkspaceService
from obsion.automation.service import (
    _TERMINAL_EXECUTION_STATUSES,
    AutomationService,
    load_schedule_owner,
    next_cron_occurrence,
)
from obsion.common.errors import ObsionError, ValidationError
from obsion.common.time import ensure_utc, utc_now
from obsion.config import Settings
from obsion.db.models import (
    Artifact,
    AutomationExecution,
    AutomationStepExecution,
    Run,
    WorkflowDefinition,
    WorkflowSchedule,
    WorkflowVersion,
)
from obsion.db.session import Database
from obsion.domain.enums import (
    ActorType,
    AutomationStatus,
    AutomationStepStatus,
    AutomationTrigger,
    RunStatus,
    ScheduleMisfirePolicy,
    WorkflowStatus,
    WorkflowStepType,
)
from obsion.persistence.events import EventDraft, EventStore
from obsion.security.auth import load_principal_by_id
from obsion.security.identity import Principal
from obsion.security.workspace_access import require_workspace_access
from obsion.telemetry import automation_counter, automation_duration, tracer

logger = structlog.get_logger(__name__)

_PLACEHOLDER = re.compile(
    r"\{\{\s*(input(?:\.[a-zA-Z0-9_-]+)+|execution\.id|workflow\.id|scheduled_for)\s*\}\}"
)
_ANY_PLACEHOLDER = re.compile(r"\{\{.*?\}\}")
_STEP_TERMINAL = {
    AutomationStepStatus.COMPLETED,
    AutomationStepStatus.FAILED,
    AutomationStepStatus.CANCELLED,
    AutomationStepStatus.SKIPPED,
}


def _observe_automation(execution: AutomationExecution, status: str) -> None:
    attributes = {"status": status}
    automation_counter.add(1, attributes)
    started = execution.started_at or execution.created_at
    elapsed_ms = max(0.0, (utc_now() - ensure_utc(started)).total_seconds() * 1000)
    automation_duration.record(elapsed_ms, attributes)


def render_automation_template(
    template: str,
    *,
    execution: AutomationExecution,
    workflow: WorkflowDefinition,
) -> str:
    matches = _ANY_PLACEHOLDER.findall(template)
    if any(_PLACEHOLDER.fullmatch(match) is None for match in matches):
        raise ValidationError(
            "workflow_template_invalid", "Workflow template contains an unsupported placeholder"
        )

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key == "execution.id":
            return str(execution.id)
        if key == "workflow.id":
            return str(workflow.id)
        if key == "scheduled_for":
            return execution.scheduled_for.isoformat() if execution.scheduled_for else "manual"
        current: Any = execution.input_payload
        for part in key.split(".")[1:]:
            if not isinstance(current, dict) or part not in current:
                raise ValidationError(
                    "workflow_template_value_missing",
                    "Workflow input does not provide a required template value",
                    key=key,
                )
            current = current[part]
        if isinstance(current, (dict, list)):
            raise ValidationError(
                "workflow_template_value_invalid",
                "Workflow placeholders may only render scalar input values",
                key=key,
            )
        return str(current)

    return _PLACEHOLDER.sub(replace, template)


class AutomationWorker:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:automation"
        self.service = AutomationService()
        self.workspaces = WorkspaceService(settings)
        self.events = EventStore()
        self._stop = asyncio.Event()
        self._scheduler_task: asyncio.Task[None] | None = None
        self._execution_task: asyncio.Task[None] | None = None
        self._semaphore = asyncio.Semaphore(settings.automation_worker_concurrency)
        self._active: set[asyncio.Task[None]] = set()

    def start(self) -> None:
        if not self.settings.automation_enabled:
            return
        if self._scheduler_task is None:
            self._scheduler_task = asyncio.create_task(
                self._scheduler_loop(), name="obsion-automation-scheduler"
            )
            self._execution_task = asyncio.create_task(
                self._execution_loop(), name="obsion-automation-worker"
            )

    async def stop(self) -> None:
        self._stop.set()
        tasks = [task for task in (self._scheduler_task, self._execution_task) if task]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._active:
            await asyncio.gather(*self._active, return_exceptions=True)

    async def _scheduler_loop(self) -> None:
        while not self._stop.is_set():
            try:
                fired = await self.tick_schedules()
            except Exception:
                logger.exception("automation.scheduler_tick_failed")
                fired = False
            if not fired:
                await self._wait()

    async def _execution_loop(self) -> None:
        while not self._stop.is_set():
            await self._semaphore.acquire()
            try:
                claimed = await self._claim_execution()
            except Exception:
                self._semaphore.release()
                logger.exception("automation.execution_claim_failed")
                await self._wait()
                continue
            if claimed is None:
                self._semaphore.release()
                await self._wait()
                continue
            task = asyncio.create_task(self._execute(claimed), name=f"obsion-automation-{claimed}")
            self._active.add(task)
            task.add_done_callback(self._active.discard)

    async def _wait(self) -> None:
        try:
            await asyncio.wait_for(
                self._stop.wait(), timeout=self.settings.automation_poll_interval_seconds
            )
        except TimeoutError:
            return

    async def tick_schedules(self, now: datetime | None = None) -> bool:
        current = ensure_utc(now or utc_now())
        async with self.database.sessions() as session, session.begin():
            schedule = await session.scalar(
                select(WorkflowSchedule)
                .where(
                    WorkflowSchedule.enabled.is_(True),
                    WorkflowSchedule.next_fire_at <= current,
                )
                .order_by(WorkflowSchedule.next_fire_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if schedule is None:
                return False
            scheduled_for = ensure_utc(schedule.next_fire_at)
            workflow = await session.scalar(
                select(WorkflowDefinition)
                .where(
                    WorkflowDefinition.id == schedule.workflow_id,
                    WorkflowDefinition.organization_id == schedule.organization_id,
                )
                .with_for_update()
            )
            if workflow is None or workflow.status != WorkflowStatus.ACTIVE:
                schedule.enabled = False
                schedule.last_error_code = "workflow_not_active"
                return True
            overdue_seconds = max(0.0, (current - scheduled_for).total_seconds())
            misfired = overdue_seconds > schedule.misfire_grace_seconds
            schedule.last_fire_at = scheduled_for
            schedule.next_fire_at = next_cron_occurrence(
                schedule.cron_expression,
                schedule.timezone,
                current if misfired else scheduled_for,
            )
            if misfired and schedule.misfire_policy == ScheduleMisfirePolicy.SKIP:
                schedule.last_error_code = "schedule_misfire_skipped"
                await self.events.append(
                    session,
                    EventDraft(
                        name="schedule.misfire_skipped",
                        aggregate_type="workflow",
                        aggregate_id=schedule.workflow_id,
                        organization_id=schedule.organization_id,
                        correlation_id=schedule.id,
                        actor_type=ActorType.SYSTEM,
                        actor_id=None,
                        payload={
                            "schedule_id": str(schedule.id),
                            "scheduled_for": scheduled_for.isoformat(),
                        },
                    ),
                )
                return True
            try:
                owner = await load_schedule_owner(session, schedule)
                if not owner.can("automation.trigger"):
                    raise ValidationError(
                        "workflow_owner_permission_missing",
                        "Workflow owner no longer has automation.trigger permission",
                    )
                await require_workspace_access(session, owner, schedule.workspace_id, write=True)
                workflow_version = await session.scalar(
                    select(WorkflowVersion).where(
                        WorkflowVersion.id == schedule.workflow_version_id,
                        WorkflowVersion.organization_id == schedule.organization_id,
                        WorkflowVersion.workflow_id == schedule.workflow_id,
                        WorkflowVersion.published_at.is_not(None),
                    )
                )
                if workflow_version is None:
                    raise ValidationError(
                        "workflow_version_unavailable", "Pinned workflow version is unavailable"
                    )
                await self.service.trigger_workflow(
                    session,
                    owner,
                    schedule.workflow_id,
                    input_payload=schedule.input_payload,
                    idempotency_key=f"schedule:{schedule.id}:{scheduled_for.isoformat()}",
                    trigger=AutomationTrigger.SCHEDULE,
                    schedule=schedule,
                    scheduled_for=scheduled_for,
                    pinned_version=workflow_version,
                )
                schedule.last_error_code = None
            except ObsionError as exc:
                schedule.enabled = False
                schedule.last_error_code = exc.code
                await self.events.append(
                    session,
                    EventDraft(
                        name="schedule.disabled",
                        aggregate_type="workflow",
                        aggregate_id=schedule.workflow_id,
                        organization_id=schedule.organization_id,
                        correlation_id=schedule.id,
                        actor_type=ActorType.SYSTEM,
                        actor_id=None,
                        payload={"schedule_id": str(schedule.id), "error_code": exc.code},
                    ),
                )
            return True

    async def _claim_execution(self) -> UUID | None:
        now = utc_now()
        async with self.database.sessions() as session, session.begin():
            execution = await session.scalar(
                select(AutomationExecution)
                .where(
                    AutomationExecution.status.in_(
                        {AutomationStatus.PENDING, AutomationStatus.RUNNING}
                    ),
                    AutomationExecution.cancellation_requested_at.is_(None),
                    or_(
                        AutomationExecution.lease_expires_at.is_(None),
                        AutomationExecution.lease_expires_at < now,
                    ),
                )
                .order_by(AutomationExecution.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if execution is None:
                return None
            if execution.status == AutomationStatus.PENDING:
                execution.status = AutomationStatus.RUNNING
                execution.started_at = now
                await self._execution_event(session, execution, "automation.started", {})
            execution.lease_owner = self.worker_id
            execution.lease_expires_at = now + timedelta(
                seconds=self.settings.automation_lease_seconds
            )
            return execution.id

    async def _execute(self, execution_id: UUID) -> None:
        try:
            with tracer.start_as_current_span("obsion.automation.execute") as span:
                span.set_attribute("obsion.automation.execution_id", str(execution_id))
                await self._advance(execution_id)
        except ObsionError as exc:
            logger.info(
                "automation.execution_rejected", execution_id=str(execution_id), code=exc.code
            )
            await self._fail_after_exception(execution_id, exc.code, exc.message)
        except Exception:
            logger.exception("automation.worker_failed", execution_id=str(execution_id))
            await self._fail_after_exception(
                execution_id,
                "automation_worker_failed",
                "The automation worker could not advance this execution",
            )
        finally:
            self._semaphore.release()

    async def _advance(self, execution_id: UUID) -> None:
        async with self.database.sessions() as session, session.begin():
            execution = await session.scalar(
                select(AutomationExecution)
                .where(AutomationExecution.id == execution_id)
                .with_for_update()
            )
            if execution is None or execution.status in _TERMINAL_EXECUTION_STATUSES:
                return
            now = utc_now()
            if ensure_utc(execution.deadline_at) <= now:
                await self._fail_execution(
                    session,
                    execution,
                    "automation_timeout",
                    "Workflow execution exceeded its deadline",
                )
                return
            principal = await load_principal_by_id(
                session, execution.organization_id, execution.owner_id
            )
            if not principal.can("automation.trigger"):
                await self._fail_execution(
                    session,
                    execution,
                    "workflow_owner_permission_missing",
                    "Workflow owner no longer has automation permission",
                )
                return
            await require_workspace_access(session, principal, execution.workspace_id, write=True)
            workflow = await session.scalar(
                select(WorkflowDefinition).where(
                    WorkflowDefinition.id == execution.workflow_id,
                    WorkflowDefinition.organization_id == execution.organization_id,
                )
            )
            if workflow is None:
                await self._fail_execution(
                    session, execution, "workflow_not_found", "Workflow definition is missing"
                )
                return
            steps = list(
                await session.scalars(
                    select(AutomationStepExecution)
                    .where(
                        AutomationStepExecution.execution_id == execution.id,
                        AutomationStepExecution.organization_id == execution.organization_id,
                    )
                    .order_by(AutomationStepExecution.ordinal)
                    .with_for_update()
                )
            )
            await self._reconcile_analysis_steps(session, execution, steps)
            failed = next(
                (step for step in steps if step.status == AutomationStepStatus.FAILED), None
            )
            if failed is not None:
                await self._fail_execution(
                    session,
                    execution,
                    failed.error_code or "automation_step_failed",
                    failed.error_message or f"Workflow step {failed.name} failed",
                    steps=steps,
                )
                return

            by_key = {step.step_key: step for step in steps}
            for step in steps:
                if step.status != AutomationStepStatus.PENDING:
                    continue
                dependencies = [by_key[key] for key in step.depends_on]
                if not all(item.status == AutomationStepStatus.COMPLETED for item in dependencies):
                    continue
                if step.step_type == WorkflowStepType.ANALYSIS:
                    await self._start_analysis(session, principal, workflow, execution, step)
                elif step.step_type == WorkflowStepType.NOTIFICATION:
                    await self._deliver_step_notification(session, workflow, execution, step)
                elif step.step_type == WorkflowStepType.HUMAN_REVIEW:
                    step.status = AutomationStepStatus.WAITING_REVIEW
                    step.started_at = now
                    execution.status = AutomationStatus.WAITING_REVIEW
                    await self._execution_event(
                        session,
                        execution,
                        "automation.review_requested",
                        {"step_id": str(step.id), "step_key": step.step_key},
                    )

            if all(step.status == AutomationStepStatus.COMPLETED for step in steps):
                execution.status = AutomationStatus.COMPLETED
                execution.completed_at = now
                execution.summary = self._summary(steps)
                await self._execution_event(
                    session, execution, "automation.completed", execution.summary
                )
                _observe_automation(execution, AutomationStatus.COMPLETED.value)
                if workflow.notify_on_success:
                    await self.service.deliver_notification(
                        session,
                        execution=execution,
                        title=f"{workflow.display_name} 已完成",
                        body="自动分析已完成，可在工作流运行详情中查看证据与产物。",
                        payload={"workflow_id": str(workflow.id), "status": "COMPLETED"},
                        idempotency_key=f"execution:{execution.id}:success",
                    )
            elif any(step.status == AutomationStepStatus.WAITING_REVIEW for step in steps):
                execution.status = AutomationStatus.WAITING_REVIEW
            else:
                execution.status = AutomationStatus.RUNNING
            execution.lease_owner = None
            execution.lease_expires_at = (
                now + timedelta(seconds=self.settings.automation_poll_interval_seconds)
                if execution.status == AutomationStatus.RUNNING
                else None
            )

    async def _reconcile_analysis_steps(
        self,
        session: AsyncSession,
        execution: AutomationExecution,
        steps: list[AutomationStepExecution],
    ) -> None:
        for step in steps:
            if step.status != AutomationStepStatus.RUNNING or step.run_id is None:
                continue
            run = await session.scalar(
                select(Run).where(
                    Run.id == step.run_id,
                    Run.organization_id == execution.organization_id,
                )
            )
            if run is None:
                step.status = AutomationStepStatus.FAILED
                step.error_code = "automation_child_run_missing"
                step.error_message = "The child Harness run is missing"
            elif run.status == RunStatus.COMPLETED:
                artifacts = list(
                    await session.scalars(
                        select(Artifact)
                        .where(
                            Artifact.organization_id == execution.organization_id,
                            Artifact.run_id == run.id,
                        )
                        .order_by(Artifact.created_at)
                    )
                )
                step.status = AutomationStepStatus.COMPLETED
                step.completed_at = utc_now()
                step.output_refs = [
                    {"type": "artifact", "artifact_id": str(item.id), "kind": item.kind.value}
                    for item in artifacts
                ]
                await self._execution_event(
                    session,
                    execution,
                    "automation.step_completed",
                    {"step_id": str(step.id), "run_id": str(run.id)},
                )
            elif run.status in {RunStatus.FAILED, RunStatus.CANCELLED}:
                step.status = AutomationStepStatus.FAILED
                step.completed_at = utc_now()
                step.error_code = run.error_code or "automation_child_run_failed"
                step.error_message = run.error_message or "The child Harness run did not complete"

    async def _start_analysis(
        self,
        session: AsyncSession,
        principal: Principal,
        workflow: WorkflowDefinition,
        execution: AutomationExecution,
        step: AutomationStepExecution,
    ) -> None:
        prompt_value = step.spec.get("prompt")
        if not isinstance(prompt_value, str):
            raise ValidationError("workflow_step_invalid", "Analysis step prompt is invalid")
        prompt = render_automation_template(prompt_value, execution=execution, workflow=workflow)
        artifacts = await self.service.dependency_artifacts(
            session, execution, list(step.depends_on)
        )
        attachments = [
            {
                "type": "artifact",
                "artifact_id": str(item.id),
                "title": item.title,
                "media_type": item.media_type,
            }
            for item in artifacts[-50:]
        ]
        thread = await self.workspaces.create_thread(
            session,
            principal,
            execution.workspace_id,
            f"{workflow.display_name} · {step.name}",
            actor_type=ActorType.SERVICE,
        )
        model_profile = step.spec.get("model_profile")
        request = CreateTurnRequest(
            input=prompt,
            context_refs=[
                {
                    "type": "automation_execution",
                    "execution_id": str(execution.id),
                    "workflow_id": str(workflow.id),
                    "step_key": step.step_key,
                }
            ],
            attachment_refs=attachments,
            model_profile=model_profile if isinstance(model_profile, str) else None,
        )
        _, run = await self.workspaces.create_turn(
            session,
            principal,
            thread.id,
            request,
            actor_type=ActorType.SERVICE,
        )
        step.status = AutomationStepStatus.RUNNING
        step.run_id = run.id
        step.started_at = utc_now()
        await self._execution_event(
            session,
            execution,
            "automation.step_started",
            {"step_id": str(step.id), "run_id": str(run.id), "thread_id": str(thread.id)},
        )

    async def _deliver_step_notification(
        self,
        session: AsyncSession,
        workflow: WorkflowDefinition,
        execution: AutomationExecution,
        step: AutomationStepExecution,
    ) -> None:
        title = step.spec.get("title")
        body = step.spec.get("body")
        if not isinstance(title, str) or not isinstance(body, str):
            raise ValidationError("workflow_step_invalid", "Notification step is invalid")
        rendered_title = render_automation_template(title, execution=execution, workflow=workflow)
        rendered_body = render_automation_template(body, execution=execution, workflow=workflow)
        delivery = await self.service.deliver_notification(
            session,
            execution=execution,
            step=step,
            title=rendered_title,
            body=rendered_body,
            payload={"workflow_id": str(workflow.id), "step_key": step.step_key},
            idempotency_key=f"execution:{execution.id}:step:{step.step_key}",
        )
        step.status = AutomationStepStatus.COMPLETED
        step.started_at = step.started_at or utc_now()
        step.completed_at = utc_now()
        step.output_refs = [{"type": "notification", "notification_id": str(delivery.id)}]

    async def _fail_execution(
        self,
        session: AsyncSession,
        execution: AutomationExecution,
        code: str,
        message: str,
        *,
        steps: list[AutomationStepExecution] | None = None,
    ) -> None:
        now = utc_now()
        execution.status = AutomationStatus.FAILED
        execution.error_code = code
        execution.error_message = message
        execution.completed_at = now
        execution.lease_owner = None
        execution.lease_expires_at = None
        current_steps = steps or list(
            await session.scalars(
                select(AutomationStepExecution)
                .where(AutomationStepExecution.execution_id == execution.id)
                .with_for_update()
            )
        )
        for step in current_steps:
            if step.status in _STEP_TERMINAL:
                continue
            step.status = AutomationStepStatus.SKIPPED
            step.completed_at = now
            step.error_code = code
            if step.run_id is not None:
                run = await session.scalar(
                    select(Run).where(
                        Run.id == step.run_id,
                        Run.organization_id == execution.organization_id,
                    )
                )
                if run is not None and run.status not in {
                    RunStatus.COMPLETED,
                    RunStatus.FAILED,
                    RunStatus.CANCELLED,
                }:
                    run.cancellation_requested_at = now
        execution.summary = self._summary(current_steps)
        await self._execution_event(session, execution, "automation.failed", {"error_code": code})
        _observe_automation(execution, AutomationStatus.FAILED.value)
        workflow = await session.scalar(
            select(WorkflowDefinition).where(
                WorkflowDefinition.id == execution.workflow_id,
                WorkflowDefinition.organization_id == execution.organization_id,
            )
        )
        if workflow is not None and workflow.notify_on_failure:
            await self.service.deliver_notification(
                session,
                execution=execution,
                title=f"{workflow.display_name} 运行失败",
                body="自动化运行未能完成，请检查运行详情和责任人权限。",
                payload={"workflow_id": str(workflow.id), "status": "FAILED", "code": code},
                idempotency_key=f"execution:{execution.id}:failure",
            )

    async def _fail_after_exception(self, execution_id: UUID, code: str, message: str) -> None:
        async with self.database.sessions() as session, session.begin():
            execution = await session.scalar(
                select(AutomationExecution)
                .where(AutomationExecution.id == execution_id)
                .with_for_update()
            )
            if execution is None or execution.status in _TERMINAL_EXECUTION_STATUSES:
                return
            await self._fail_execution(
                session,
                execution,
                code,
                message,
            )

    async def _execution_event(
        self,
        session: AsyncSession,
        execution: AutomationExecution,
        name: str,
        payload: dict[str, Any],
    ) -> None:
        await self.events.append(
            session,
            EventDraft(
                name=name,
                aggregate_type="automation_execution",
                aggregate_id=execution.id,
                organization_id=execution.organization_id,
                correlation_id=execution.id,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                payload=payload,
            ),
        )

    @staticmethod
    def _summary(steps: list[AutomationStepExecution]) -> dict[str, Any]:
        counts: dict[str, int] = {}
        run_ids: list[str] = []
        output_refs: list[dict[str, Any]] = []
        for step in steps:
            counts[step.status.value] = counts.get(step.status.value, 0) + 1
            if step.run_id is not None:
                run_ids.append(str(step.run_id))
            output_refs.extend(step.output_refs)
        return {"step_counts": counts, "run_ids": run_ids, "output_refs": output_refs}
