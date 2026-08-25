import asyncio
import os
import socket
from datetime import timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import or_, select

from obsion.actions.gateway import (
    ActionGateway,
    ActionGatewayRequest,
    ActionGatewayStatus,
    action_provider_payload,
)
from obsion.actions.service import ActionService
from obsion.common.errors import ObsionError, ValidationError
from obsion.common.time import ensure_utc, utc_now
from obsion.config import Settings
from obsion.db.models import (
    ActionApproval,
    ActionAttempt,
    ActionPlan,
    ActionRequest,
)
from obsion.db.session import Database
from obsion.domain.enums import (
    ActionApprovalPurpose,
    ActionAttemptStatus,
    ActionStatus,
    ActorType,
    ApprovalStatus,
)
from obsion.persistence.events import EventDraft, EventStore
from obsion.security.auth import load_principal_by_id
from obsion.security.workspace_access import require_workspace_access
from obsion.telemetry import tracer

logger = structlog.get_logger(__name__)

_CLAIMABLE = {
    ActionStatus.APPROVED,
    ActionStatus.EXECUTING,
    ActionStatus.ROLLBACK_APPROVED,
    ActionStatus.ROLLING_BACK,
}


class ActionWorker:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        gateway: ActionGateway,
    ) -> None:
        self.database = database
        self.settings = settings
        self.gateway = gateway
        self.service = ActionService(gateway)
        self.events = EventStore()
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:action"
        self._stop = asyncio.Event()
        self._loop_task: asyncio.Task[None] | None = None
        self._semaphore = asyncio.Semaphore(settings.action_worker_concurrency)
        self._active: set[asyncio.Task[None]] = set()

    def start(self) -> None:
        if self.settings.actions_enabled and self._loop_task is None:
            self._loop_task = asyncio.create_task(self._loop(), name="obsion-action-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._loop_task is not None:
            await asyncio.gather(self._loop_task, return_exceptions=True)
        if self._active:
            await asyncio.gather(*self._active, return_exceptions=True)

    async def _loop(self) -> None:
        while not self._stop.is_set():
            await self._semaphore.acquire()
            try:
                claimed = await self._claim()
            except Exception:
                self._semaphore.release()
                logger.exception("action.claim_failed")
                await self._wait()
                continue
            if claimed is None:
                self._semaphore.release()
                await self._wait()
                continue
            task = asyncio.create_task(self._execute(claimed), name=f"obsion-action-{claimed}")
            self._active.add(task)
            task.add_done_callback(self._active.discard)

    async def _wait(self) -> None:
        try:
            await asyncio.wait_for(
                self._stop.wait(), timeout=self.settings.action_poll_interval_seconds
            )
        except TimeoutError:
            return

    async def _claim(self) -> UUID | None:
        now = utc_now()
        async with self.database.sessions() as session, session.begin():
            await self._expire_one_approval(session)
            action = await session.scalar(
                select(ActionRequest)
                .where(
                    ActionRequest.status.in_(_CLAIMABLE),
                    ActionRequest.cancellation_requested_at.is_(None),
                    or_(
                        ActionRequest.lease_expires_at.is_(None),
                        ActionRequest.lease_expires_at < now,
                    ),
                )
                .order_by(ActionRequest.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if action is None:
                return None
            if action.status in {ActionStatus.APPROVED, ActionStatus.EXECUTING}:
                action.status = ActionStatus.EXECUTING
                purpose = ActionApprovalPurpose.EXECUTE
            else:
                action.status = ActionStatus.ROLLING_BACK
                purpose = ActionApprovalPurpose.ROLLBACK
            action.started_at = action.started_at or now
            action.lease_owner = self.worker_id
            action.lease_expires_at = now + timedelta(seconds=self.settings.action_lease_seconds)
            await self._event(
                session,
                action,
                "action.claimed",
                {"purpose": purpose.value, "worker": self.worker_id},
            )
            return action.id

    async def _execute(self, action_id: UUID) -> None:
        try:
            with tracer.start_as_current_span("obsion.action.execute") as span:
                span.set_attribute("obsion.action.id", str(action_id))
                await self._invoke(action_id)
        except ObsionError as exc:
            logger.info("action.execution_rejected", action_id=str(action_id), code=exc.code)
            await self._fail_after_exception(action_id, exc.code, exc.message)
        except Exception:
            logger.exception("action.worker_failed", action_id=str(action_id))
            await self._fail_after_exception(
                action_id,
                "action_worker_failed",
                "The action worker could not complete this request",
            )
        finally:
            self._semaphore.release()

    async def _invoke(self, action_id: UUID) -> None:
        async with self.database.sessions() as session, session.begin():
            action = await session.scalar(
                select(ActionRequest).where(ActionRequest.id == action_id).with_for_update()
            )
            if action is None or action.status not in {
                ActionStatus.EXECUTING,
                ActionStatus.ROLLING_BACK,
            }:
                return
            now = utc_now()
            if action.deadline_at is None or ensure_utc(action.deadline_at) <= now:
                await self._fail(
                    session,
                    action,
                    "action_timeout",
                    "The approved action exceeded its execution deadline",
                )
                return
            principal = await load_principal_by_id(session, action.organization_id, action.owner_id)
            if not principal.can("action.execute"):
                raise ValidationError(
                    "action_owner_permission_missing",
                    "The action owner no longer has execution permission",
                )
            await require_workspace_access(session, principal, action.workspace_id, write=True)
            plan = await session.scalar(
                select(ActionPlan).where(
                    ActionPlan.organization_id == action.organization_id,
                    ActionPlan.action_request_id == action.id,
                )
            )
            if (
                plan is None
                or action.plan_checksum_sha256 is None
                or plan.checksum_sha256 != action.plan_checksum_sha256
            ):
                raise ValidationError(
                    "action_plan_invalid", "The immutable action plan is missing or mismatched"
                )
            purpose = (
                ActionApprovalPurpose.EXECUTE
                if action.status == ActionStatus.EXECUTING
                else ActionApprovalPurpose.ROLLBACK
            )
            approval = await session.scalar(
                select(ActionApproval)
                .where(
                    ActionApproval.organization_id == action.organization_id,
                    ActionApproval.action_request_id == action.id,
                    ActionApproval.purpose == purpose,
                    ActionApproval.status == ApprovalStatus.APPROVED,
                    ActionApproval.plan_checksum_sha256 == plan.checksum_sha256,
                )
                .order_by(ActionApproval.revision.desc())
                .limit(1)
            )
            if approval is None or ensure_utc(approval.expires_at) <= now:
                raise ValidationError(
                    "action_approval_invalid", "No valid approval exists for this action plan"
                )
            reference_key = "execute" if purpose == ActionApprovalPurpose.EXECUTE else "rollback"
            reference = plan.spec.get(reference_key)
            if not isinstance(reference, dict):
                raise ValidationError(
                    "action_plan_invalid", "The action plan capability reference is invalid"
                )
            try:
                capability_version_id = UUID(str(reference["capability_version_id"]))
                connector_id = UUID(str(reference["connector_id"]))
            except (KeyError, ValueError) as exc:
                raise ValidationError(
                    "action_plan_invalid", "The action plan capability reference is invalid"
                ) from exc
            attempt = await session.scalar(
                select(ActionAttempt)
                .where(
                    ActionAttempt.organization_id == action.organization_id,
                    ActionAttempt.action_request_id == action.id,
                    ActionAttempt.purpose == purpose,
                    ActionAttempt.ordinal == 1,
                )
                .with_for_update()
            )
            payload = action_provider_payload(
                action,
                plan_checksum_sha256=plan.checksum_sha256,
                purpose=purpose,
                parameters=(
                    plan.spec["parameters"]
                    if purpose == ActionApprovalPurpose.EXECUTE
                    else plan.spec["rollback_parameters"]
                ),
                original_output=(
                    action.result.get("execute", {})
                    if purpose == ActionApprovalPurpose.ROLLBACK
                    else None
                ),
            )
            if attempt is None:
                attempt = ActionAttempt(
                    organization_id=action.organization_id,
                    action_request_id=action.id,
                    purpose=purpose,
                    ordinal=1,
                    status=ActionAttemptStatus.PENDING,
                    capability_version_id=capability_version_id,
                    connector_id=connector_id,
                    approval_id=approval.id,
                    idempotency_key=(f"action:{action.id}:{purpose.value.casefold()}:1"),
                    input_payload=payload,
                )
                session.add(attempt)
                await session.flush()
            elif (
                attempt.capability_version_id != capability_version_id
                or attempt.connector_id != connector_id
                or attempt.input_payload != payload
            ):
                raise ValidationError(
                    "action_attempt_mismatch",
                    "A recovered action attempt does not match its sealed plan",
                )
            if attempt.status == ActionAttemptStatus.COMPLETED:
                await self._complete(session, action, purpose, attempt.output)
                return
            attempt.status = ActionAttemptStatus.RUNNING
            attempt.started_at = attempt.started_at or now
            result = await self.gateway.invoke(
                session,
                ActionGatewayRequest(
                    principal=principal,
                    action=action,
                    approval=approval,
                    attempt=attempt,
                    plan_checksum_sha256=plan.checksum_sha256,
                    purpose=purpose,
                    payload=payload,
                ),
            )
            attempt.policy_decision_id = result.policy_decision_id
            attempt.completed_at = utc_now()
            if result.status == ActionGatewayStatus.COMPLETED:
                attempt.status = ActionAttemptStatus.COMPLETED
                attempt.output = result.output or {}
                attempt.error_code = None
                attempt.error_message = None
                await self._complete(session, action, purpose, attempt.output)
            else:
                attempt.status = ActionAttemptStatus.FAILED
                attempt.error_code = result.error_code
                attempt.error_message = result.error_message
                await self._fail(
                    session,
                    action,
                    result.error_code or "action_provider_failed",
                    result.error_message or "The action provider could not complete the request",
                )

    async def _complete(
        self,
        session: Any,
        action: ActionRequest,
        purpose: ActionApprovalPurpose,
        output: dict[str, Any],
    ) -> None:
        now = utc_now()
        if purpose == ActionApprovalPurpose.EXECUTE:
            action.status = ActionStatus.COMPLETED
            action.result = {"execute": output}
            title = f"{action.title} 已执行"
            body = "受控动作已完成，可在动作详情中查看执行记录并按需申请回滚。"
            event = "completed"
        else:
            action.status = ActionStatus.ROLLED_BACK
            action.result = {**action.result, "rollback": output}
            title = f"{action.title} 已回滚"
            body = "补偿动作已完成，原变更已按封存的回滚计划处理。"
            event = "rolled-back"
        action.completed_at = now
        action.lease_owner = None
        action.lease_expires_at = None
        action.error_code = None
        action.error_message = None
        await self._event(
            session,
            action,
            "action.completed"
            if purpose == ActionApprovalPurpose.EXECUTE
            else "action.rolled_back",
            {"purpose": purpose.value},
        )
        await self.service.deliver_notification(
            session, action, title=title, body=body, event=event
        )

    async def _fail(
        self,
        session: Any,
        action: ActionRequest,
        code: str,
        message: str,
    ) -> None:
        rolling_back = action.status == ActionStatus.ROLLING_BACK
        action.status = ActionStatus.ROLLBACK_FAILED if rolling_back else ActionStatus.FAILED
        action.completed_at = utc_now()
        action.lease_owner = None
        action.lease_expires_at = None
        action.error_code = code
        action.error_message = message
        await self._event(
            session,
            action,
            "action.rollback_failed" if rolling_back else "action.failed",
            {"error_code": code},
        )
        await self.service.deliver_notification(
            session,
            action,
            title=f"{action.title} {'回滚失败' if rolling_back else '执行失败'}",
            body="受控动作未能完成，请检查动作详情、连接器状态和权限。",
            event="rollback-failed" if rolling_back else "failed",
        )

    async def _fail_after_exception(self, action_id: UUID, code: str, message: str) -> None:
        async with self.database.sessions() as session, session.begin():
            action = await session.scalar(
                select(ActionRequest).where(ActionRequest.id == action_id).with_for_update()
            )
            if action is None or action.status not in {
                ActionStatus.EXECUTING,
                ActionStatus.ROLLING_BACK,
            }:
                return
            await self._fail(session, action, code, message)

    async def _expire_one_approval(self, session: Any) -> None:
        now = utc_now()
        approval = await session.scalar(
            select(ActionApproval)
            .where(
                ActionApproval.status == ApprovalStatus.PENDING,
                ActionApproval.expires_at <= now,
            )
            .order_by(ActionApproval.expires_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if approval is None:
            return
        action = await session.scalar(
            select(ActionRequest)
            .where(ActionRequest.id == approval.action_request_id)
            .with_for_update()
        )
        approval.status = ApprovalStatus.EXPIRED
        approval.decided_at = now
        if action is None:
            return
        if approval.purpose == ActionApprovalPurpose.EXECUTE:
            action.status = ActionStatus.EXPIRED
            action.completed_at = now
        else:
            action.status = ActionStatus.ROLLBACK_REJECTED
        action.error_code = "action_approval_expired"
        action.error_message = "The action approval expired"
        await self._event(
            session,
            action,
            "action.approval_expired",
            {"approval_id": str(approval.id), "purpose": approval.purpose.value},
        )

    async def _event(
        self,
        session: Any,
        action: ActionRequest,
        name: str,
        payload: dict[str, Any],
    ) -> None:
        await self.events.append(
            session,
            EventDraft(
                name=name,
                aggregate_type="action_request",
                aggregate_id=action.id,
                organization_id=action.organization_id,
                correlation_id=action.id,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                payload=payload,
            ),
        )
