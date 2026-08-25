import hashlib
import json
import re
from collections.abc import Sequence
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.actions.gateway import ActionGateway, action_provider_payload
from obsion.actions.schemas import (
    ActionApprovalView,
    ActionAttemptView,
    ActionDetailView,
    ActionPlanView,
    ActionRequestView,
    CreateActionRequest,
)
from obsion.common.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ObsionError,
    ValidationError,
)
from obsion.common.time import ensure_utc, utc_now
from obsion.db.models import (
    ActionApproval,
    ActionAttempt,
    ActionPlan,
    ActionRequest,
    NotificationDelivery,
    Workspace,
)
from obsion.domain.enums import (
    ActionApprovalPurpose,
    ActionStatus,
    ActionType,
    ActorType,
    ApprovalStatus,
    NotificationStatus,
    RiskLevel,
)
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.persistence.events import EventDraft, EventStore
from obsion.security.auth import load_principal_by_id
from obsion.security.identity import Principal
from obsion.security.workspace_access import require_workspace_access, workspace_access_clause

_SECRET_KEY = re.compile(
    r"password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential",
    re.I,
)
_OPEN_ACTIONS = {ActionType.GENERATE_PR, ActionType.CREATE_TICKET}
_CONTRACTS: dict[ActionType, tuple[str, str]] = {
    ActionType.GENERATE_PR: ("action.pr.create", "action.pr.close"),
    ActionType.CREATE_TICKET: ("action.ticket.create", "action.ticket.close"),
}


def action_plan_checksum(spec: dict[str, Any]) -> str:
    payload = json.dumps(spec, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


class ActionService:
    def __init__(self, gateway: ActionGateway) -> None:
        self.gateway = gateway
        self.events = EventStore()
        self.audit = AuditWriter()

    async def create(
        self,
        session: AsyncSession,
        principal: Principal,
        workspace_id: UUID,
        request: CreateActionRequest,
    ) -> ActionRequest:
        self._require(principal, "action.request")
        await require_workspace_access(session, principal, workspace_id, write=True)
        self._deny_secret_fields(request.target)
        self._deny_secret_fields(request.parameters)
        self._deny_secret_fields(request.rollback_parameters)
        owner_id = request.owner_id or principal.id
        if owner_id != principal.id and not principal.can("action.request.all"):
            raise AuthorizationError(
                "action_owner_assignment_denied", "Cannot assign another action owner"
            )
        owner = await load_principal_by_id(session, principal.organization_id, owner_id)
        await require_workspace_access(
            session,
            owner,
            workspace_id,
            write=True,
        )
        existing = await session.scalar(
            select(ActionRequest).where(
                ActionRequest.organization_id == principal.organization_id,
                ActionRequest.idempotency_key == request.idempotency_key,
            )
        )
        if existing is not None:
            if not self._same_request(existing, workspace_id, request, owner_id):
                raise ConflictError(
                    "action_idempotency_conflict",
                    "The idempotency key is already used by a different action request",
                )
            await require_workspace_access(session, principal, existing.workspace_id)
            return existing
        action = ActionRequest(
            organization_id=principal.organization_id,
            workspace_id=workspace_id,
            action_type=request.action_type,
            title=request.title.strip(),
            description=request.description.strip(),
            environment=request.environment,
            target=request.target,
            parameters=request.parameters,
            rollback_parameters=request.rollback_parameters,
            status=ActionStatus.DRAFT,
            owner_id=owner_id,
            requested_by=principal.id,
            idempotency_key=request.idempotency_key,
            timeout_seconds=request.timeout_seconds,
        )
        session.add(action)
        await session.flush()
        await self._event(
            session,
            action,
            "action.created",
            ActorType.USER,
            principal.id,
            {"action_type": action.action_type.value, "environment": action.environment},
        )
        await self._audit(
            session,
            action,
            principal,
            "action.create",
            "SUCCESS",
            {"action_type": action.action_type.value, "environment": action.environment},
        )
        return action

    async def list(
        self,
        session: AsyncSession,
        principal: Principal,
        workspace_id: UUID,
        *,
        status: ActionStatus | None = None,
        limit: int = 100,
    ) -> list[ActionRequest]:
        await require_workspace_access(session, principal, workspace_id)
        statement = select(ActionRequest).where(
            ActionRequest.organization_id == principal.organization_id,
            ActionRequest.workspace_id == workspace_id,
        )
        if status is not None:
            statement = statement.where(ActionRequest.status == status)
        return list(
            await session.scalars(statement.order_by(ActionRequest.created_at.desc()).limit(limit))
        )

    async def get(
        self,
        session: AsyncSession,
        principal: Principal,
        action_id: UUID,
        *,
        write: bool = False,
        for_update: bool = False,
    ) -> ActionRequest:
        statement = select(ActionRequest).where(
            ActionRequest.id == action_id,
            ActionRequest.organization_id == principal.organization_id,
        )
        if for_update:
            statement = statement.with_for_update()
        action = await session.scalar(statement)
        if action is None:
            raise NotFoundError("Action request", action_id)
        await require_workspace_access(session, principal, action.workspace_id, write=write)
        return action

    async def detail(
        self, session: AsyncSession, principal: Principal, action_id: UUID
    ) -> ActionDetailView:
        action = await self.get(session, principal, action_id)
        plan = await session.scalar(
            select(ActionPlan).where(
                ActionPlan.organization_id == principal.organization_id,
                ActionPlan.action_request_id == action.id,
            )
        )
        approvals = list(
            await session.scalars(
                select(ActionApproval)
                .where(
                    ActionApproval.organization_id == principal.organization_id,
                    ActionApproval.action_request_id == action.id,
                )
                .order_by(ActionApproval.created_at)
            )
        )
        attempts = list(
            await session.scalars(
                select(ActionAttempt)
                .where(
                    ActionAttempt.organization_id == principal.organization_id,
                    ActionAttempt.action_request_id == action.id,
                )
                .order_by(ActionAttempt.created_at)
            )
        )
        return ActionDetailView(
            action=ActionRequestView.model_validate(action),
            plan=ActionPlanView.model_validate(plan) if plan else None,
            approvals=[ActionApprovalView.model_validate(item) for item in approvals],
            attempts=[ActionAttemptView.model_validate(item) for item in attempts],
        )

    async def preflight(
        self,
        session: AsyncSession,
        principal: Principal,
        action_id: UUID,
        *,
        reason: str,
        approval_ttl_minutes: int,
    ) -> ActionRequest:
        self._require(principal, "action.request")
        action = await self.get(session, principal, action_id, write=True, for_update=True)
        if action.status not in {ActionStatus.DRAFT, ActionStatus.PREFLIGHT_FAILED}:
            raise ConflictError(
                "action_not_preflightable",
                "Only a draft or failed preflight action can be checked",
                status=action.status,
            )
        if await session.scalar(
            select(ActionPlan.id).where(ActionPlan.action_request_id == action.id)
        ):
            raise ConflictError("action_plan_exists", "The action already has a sealed plan")
        try:
            if action.action_type not in _OPEN_ACTIONS:
                raise ValidationError(
                    "v1_action_type_boundary",
                    "This action type is planned but cannot execute in V1",
                    action_type=action.action_type.value,
                )
            if action.environment not in {"development", "staging"}:
                raise ValidationError(
                    "v1_production_action_boundary",
                    "V1 actions are limited to development and staging",
                )
            owner = await self._require_owner(session, action)
            execute_name, rollback_name = _CONTRACTS[action.action_type]
            execute = await self.gateway.preflight(
                session,
                owner,
                capability_name=execute_name,
                environment=action.environment,
                resource=action.target,
            )
            rollback = await self.gateway.preflight(
                session,
                owner,
                capability_name=rollback_name,
                environment=action.environment,
                resource=action.target,
            )
            now = utc_now()
            plan_spec = {
                "schema_version": 1,
                "action_type": action.action_type.value,
                "environment": action.environment,
                "target": action.target,
                "parameters": action.parameters,
                "rollback_parameters": action.rollback_parameters,
                "execute": execute.plan_reference(),
                "rollback": rollback.plan_reference(),
            }
            checksum = action_plan_checksum(plan_spec)
            self.gateway.validate_preflight_payload(
                execute,
                action_provider_payload(
                    action,
                    plan_checksum_sha256=checksum,
                    purpose=ActionApprovalPurpose.EXECUTE,
                    parameters=action.parameters,
                ),
            )
            self.gateway.validate_preflight_payload(
                rollback,
                action_provider_payload(
                    action,
                    plan_checksum_sha256=checksum,
                    purpose=ActionApprovalPurpose.ROLLBACK,
                    parameters=action.rollback_parameters,
                    original_output={},
                ),
            )
        except ObsionError as exc:
            action.status = ActionStatus.PREFLIGHT_FAILED
            action.error_code = exc.code
            action.error_message = exc.message
            action.preflight = {
                "passed": False,
                "checked_at": utc_now().isoformat(),
                "error_code": exc.code,
                "details": exc.details,
            }
            await self._event(
                session,
                action,
                "action.preflight_failed",
                ActorType.USER,
                principal.id,
                {"error_code": exc.code},
            )
            await self._audit(
                session,
                action,
                principal,
                "action.preflight",
                "FAILED",
                {"error_code": exc.code},
            )
            return action

        plan = ActionPlan(
            organization_id=action.organization_id,
            action_request_id=action.id,
            spec=plan_spec,
            checksum_sha256=checksum,
            created_by=principal.id,
            created_at=now,
        )
        session.add(plan)
        action.plan_checksum_sha256 = checksum
        action.preflight = {
            "passed": True,
            "checked_at": now.isoformat(),
            "execute_capability": execute.definition.name,
            "rollback_capability": rollback.definition.name,
        }
        action.status = ActionStatus.WAITING_APPROVAL
        action.error_code = None
        action.error_message = None
        await session.flush()
        await self._create_approval(
            session,
            action,
            ActionApprovalPurpose.EXECUTE,
            reason,
            approval_ttl_minutes,
        )
        await self._event(
            session,
            action,
            "action.preflight_passed",
            ActorType.USER,
            principal.id,
            {
                "plan_id": str(plan.id),
                "plan_checksum_sha256": checksum,
                "execute_capability": execute.definition.name,
                "rollback_capability": rollback.definition.name,
            },
        )
        await self._audit(
            session,
            action,
            principal,
            "action.preflight",
            "SUCCESS",
            {"plan_checksum_sha256": checksum},
        )
        return action

    async def list_approvals(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        status: ApprovalStatus | None = None,
        limit: int = 200,
    ) -> Sequence[ActionApproval]:
        if not principal.can("action.approval.read") and not principal.can("action.approve"):
            raise AuthorizationError(
                "action_approval_read_denied", "Action approval access is not permitted"
            )
        statement = (
            select(ActionApproval)
            .join(ActionRequest, ActionRequest.id == ActionApproval.action_request_id)
            .join(Workspace, Workspace.id == ActionRequest.workspace_id)
            .where(
                ActionApproval.organization_id == principal.organization_id,
                ActionRequest.organization_id == principal.organization_id,
                Workspace.organization_id == principal.organization_id,
                workspace_access_clause(principal),
            )
        )
        if status is not None:
            statement = statement.where(ActionApproval.status == status)
        return list(
            await session.scalars(statement.order_by(ActionApproval.created_at.desc()).limit(limit))
        )

    async def decide(
        self,
        session: AsyncSession,
        principal: Principal,
        approval_id: UUID,
        *,
        approve: bool,
        reason: str,
    ) -> ActionApproval:
        self._require(principal, "action.approve")
        approval = await session.scalar(
            select(ActionApproval)
            .where(
                ActionApproval.id == approval_id,
                ActionApproval.organization_id == principal.organization_id,
            )
            .with_for_update()
        )
        if approval is None:
            raise NotFoundError("Action approval", approval_id)
        if approval.status != ApprovalStatus.PENDING:
            raise ConflictError(
                "action_approval_already_decided",
                "The action approval is no longer pending",
                status=approval.status,
            )
        action = await self.get(
            session,
            principal,
            approval.action_request_id,
            write=True,
            for_update=True,
        )
        now = utc_now()
        if ensure_utc(approval.expires_at) <= now:
            approval.status = ApprovalStatus.EXPIRED
            approval.decided_at = now
            action.status = ActionStatus.EXPIRED
            action.error_code = "action_approval_expired"
            action.error_message = "The action approval expired"
            await self._event(
                session,
                action,
                "action.approval_expired",
                ActorType.SYSTEM,
                None,
                {"approval_id": str(approval.id), "purpose": approval.purpose.value},
            )
            raise ConflictError("action_approval_expired", "The action approval has expired")
        if approval.requested_by == principal.id:
            raise AuthorizationError(
                "action_self_approval_denied", "The requester cannot approve their own action"
            )
        if action.plan_checksum_sha256 != approval.plan_checksum_sha256:
            raise ConflictError(
                "action_plan_changed", "The approved action plan no longer matches the request"
            )
        approval.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
        approval.decided_by = principal.id
        approval.decision_reason = reason.strip()
        approval.decided_at = now
        if approve:
            action.status = (
                ActionStatus.APPROVED
                if approval.purpose == ActionApprovalPurpose.EXECUTE
                else ActionStatus.ROLLBACK_APPROVED
            )
            action.deadline_at = now + timedelta(seconds=action.timeout_seconds)
            action.error_code = None
            action.error_message = None
        elif approval.purpose == ActionApprovalPurpose.EXECUTE:
            action.status = ActionStatus.REJECTED
            action.completed_at = now
            action.error_code = "action_rejected"
            action.error_message = "The action request was rejected"
        else:
            action.status = ActionStatus.ROLLBACK_REJECTED
            action.error_code = "action_rollback_rejected"
            action.error_message = "The rollback request was rejected"
        await self._event(
            session,
            action,
            "action.approval_decided",
            ActorType.USER,
            principal.id,
            {
                "approval_id": str(approval.id),
                "purpose": approval.purpose.value,
                "decision": approval.status.value,
                "reason": approval.decision_reason,
            },
        )
        await self._audit(
            session,
            action,
            principal,
            "action.approval.decide",
            approval.status.value,
            {"approval_id": str(approval.id), "purpose": approval.purpose.value},
        )
        return approval

    async def request_rollback(
        self,
        session: AsyncSession,
        principal: Principal,
        action_id: UUID,
        *,
        reason: str,
        approval_ttl_minutes: int,
    ) -> ActionRequest:
        self._require(principal, "action.rollback")
        action = await self.get(session, principal, action_id, write=True, for_update=True)
        if action.owner_id != principal.id and not principal.can("action.rollback.all"):
            raise AuthorizationError(
                "action_rollback_denied", "Only the action owner can request rollback"
            )
        if action.status not in {
            ActionStatus.COMPLETED,
            ActionStatus.FAILED,
            ActionStatus.ROLLBACK_FAILED,
            ActionStatus.ROLLBACK_REJECTED,
        }:
            raise ConflictError(
                "action_not_rollbackable",
                "The action is not in a rollbackable state",
                status=action.status,
            )
        await self._create_approval(
            session,
            action,
            ActionApprovalPurpose.ROLLBACK,
            reason,
            approval_ttl_minutes,
        )
        action.status = ActionStatus.WAITING_ROLLBACK_APPROVAL
        action.completed_at = None
        action.error_code = None
        action.error_message = None
        await self._event(
            session,
            action,
            "action.rollback_requested",
            ActorType.USER,
            principal.id,
            {},
        )
        await self._audit(session, action, principal, "action.rollback.request", "SUCCESS", {})
        return action

    async def cancel(
        self, session: AsyncSession, principal: Principal, action_id: UUID
    ) -> ActionRequest:
        action = await self.get(session, principal, action_id, write=True, for_update=True)
        if action.owner_id != principal.id and not principal.can("action.cancel.all"):
            raise AuthorizationError("action_cancel_denied", "Only the action owner can cancel")
        if action.status not in {
            ActionStatus.DRAFT,
            ActionStatus.PREFLIGHT_FAILED,
            ActionStatus.WAITING_APPROVAL,
            ActionStatus.APPROVED,
            ActionStatus.WAITING_ROLLBACK_APPROVAL,
            ActionStatus.ROLLBACK_APPROVED,
        }:
            raise ConflictError(
                "action_not_cancellable",
                "The action can no longer be cancelled safely",
                status=action.status,
            )
        now = utc_now()
        action.status = ActionStatus.CANCELLED
        action.cancellation_requested_at = now
        action.completed_at = now
        pending = list(
            await session.scalars(
                select(ActionApproval)
                .where(
                    ActionApproval.action_request_id == action.id,
                    ActionApproval.status == ApprovalStatus.PENDING,
                )
                .with_for_update()
            )
        )
        for approval in pending:
            approval.status = ApprovalStatus.CANCELLED
            approval.decided_at = now
        await self._event(session, action, "action.cancelled", ActorType.USER, principal.id, {})
        await self._audit(session, action, principal, "action.cancel", "SUCCESS", {})
        return action

    async def _create_approval(
        self,
        session: AsyncSession,
        action: ActionRequest,
        purpose: ActionApprovalPurpose,
        reason: str,
        ttl_minutes: int,
    ) -> ActionApproval:
        if action.plan_checksum_sha256 is None:
            raise ConflictError("action_plan_missing", "The action has no sealed execution plan")
        latest = await session.scalar(
            select(func.max(ActionApproval.revision)).where(
                ActionApproval.action_request_id == action.id,
                ActionApproval.purpose == purpose,
            )
        )
        now = utc_now()
        approval = ActionApproval(
            organization_id=action.organization_id,
            action_request_id=action.id,
            purpose=purpose,
            revision=(latest or 0) + 1,
            plan_checksum_sha256=action.plan_checksum_sha256,
            status=ApprovalStatus.PENDING,
            reason=reason.strip(),
            requested_by=action.requested_by,
            approver_constraints={"permission": "action.approve", "disallow_self": True},
            expires_at=now + timedelta(minutes=ttl_minutes),
        )
        session.add(approval)
        await session.flush()
        await self._event(
            session,
            action,
            "action.approval_requested",
            ActorType.USER,
            action.requested_by,
            {
                "approval_id": str(approval.id),
                "purpose": purpose.value,
                "expires_at": approval.expires_at.isoformat(),
            },
        )
        return approval

    async def deliver_notification(
        self,
        session: AsyncSession,
        action: ActionRequest,
        *,
        title: str,
        body: str,
        event: str,
    ) -> NotificationDelivery:
        idempotency_key = f"action:{action.id}:{event}"
        existing = await session.scalar(
            select(NotificationDelivery).where(
                NotificationDelivery.organization_id == action.organization_id,
                NotificationDelivery.idempotency_key == idempotency_key,
            )
        )
        if existing is not None:
            return existing
        now = utc_now()
        delivery = NotificationDelivery(
            organization_id=action.organization_id,
            workspace_id=action.workspace_id,
            execution_id=None,
            action_request_id=action.id,
            step_execution_id=None,
            recipient_id=action.owner_id,
            title=title,
            body=body,
            payload={
                "action_request_id": str(action.id),
                "action_type": action.action_type.value,
                "status": action.status.value,
            },
            status=NotificationStatus.DELIVERED,
            idempotency_key=idempotency_key,
            delivered_at=now,
            created_at=now,
        )
        session.add(delivery)
        await session.flush()
        await self._event(
            session,
            action,
            "notification.delivered",
            ActorType.SYSTEM,
            None,
            {"notification_id": str(delivery.id), "event": event},
        )
        return delivery

    async def _require_owner(self, session: AsyncSession, action: ActionRequest) -> Principal:
        owner = await load_principal_by_id(session, action.organization_id, action.owner_id)
        if not owner.can("action.execute"):
            raise ValidationError(
                "action_owner_permission_missing",
                "The action owner must have action.execute permission",
            )
        await require_workspace_access(session, owner, action.workspace_id, write=True)
        return owner

    @staticmethod
    def _same_request(
        existing: ActionRequest,
        workspace_id: UUID,
        request: CreateActionRequest,
        owner_id: UUID,
    ) -> bool:
        return bool(
            existing.workspace_id == workspace_id
            and existing.action_type == request.action_type
            and existing.title == request.title.strip()
            and existing.description == request.description.strip()
            and existing.environment == request.environment
            and existing.target == request.target
            and existing.parameters == request.parameters
            and existing.rollback_parameters == request.rollback_parameters
            and existing.owner_id == owner_id
            and existing.timeout_seconds == request.timeout_seconds
        )

    @classmethod
    def _deny_secret_fields(cls, value: Any, path: str = "payload") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if _SECRET_KEY.search(str(key)):
                    raise ValidationError(
                        "action_inline_secret_denied",
                        "Action requests cannot contain credentials or secret fields",
                        path=f"{path}.{key}",
                    )
                cls._deny_secret_fields(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                cls._deny_secret_fields(item, f"{path}.{index}")

    @staticmethod
    def _require(principal: Principal, permission: str) -> None:
        if not principal.can(permission):
            raise AuthorizationError(
                "action_permission_denied", "Action access is not permitted", permission=permission
            )

    async def _event(
        self,
        session: AsyncSession,
        action: ActionRequest,
        name: str,
        actor_type: ActorType,
        actor_id: UUID | None,
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
                actor_type=actor_type,
                actor_id=actor_id,
                payload=payload,
            ),
        )

    async def _audit(
        self,
        session: AsyncSession,
        action: ActionRequest,
        principal: Principal,
        operation: str,
        outcome: str,
        metadata: dict[str, Any],
    ) -> None:
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=action.organization_id,
                correlation_id=action.id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action=operation,
                resource_type="action_request",
                resource_id=str(action.id),
                outcome=outcome,
                risk_level=RiskLevel.L3,
                metadata=metadata,
            ),
        )
