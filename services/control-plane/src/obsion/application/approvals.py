from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import AuthorizationError, ConflictError, NotFoundError
from obsion.common.time import ensure_utc, utc_now
from obsion.db.models import Approval, Run, Thread, Turn, Workspace
from obsion.domain.enums import ActorType, ApprovalStatus, RunStatus
from obsion.domain.run_state import is_terminal, validate_run_transition
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.persistence.events import EventDraft, EventStore
from obsion.security.identity import Principal
from obsion.security.workspace_access import require_run_access, workspace_access_clause
from obsion.telemetry import approval_counter


class ApprovalService:
    def __init__(self) -> None:
        self.events = EventStore()
        self.audit = AuditWriter()

    async def list(
        self,
        session: AsyncSession,
        principal: Principal,
        status: ApprovalStatus | None,
    ) -> list[Approval]:
        if not principal.can("approval.read") and not principal.can("approval.decide"):
            raise AuthorizationError("approval_read_denied", "Approval access is not permitted")
        statement = (
            select(Approval)
            .join(Run, Run.id == Approval.run_id)
            .join(Turn, Turn.id == Run.turn_id)
            .join(Thread, Thread.id == Turn.thread_id)
            .join(Workspace, Workspace.id == Thread.workspace_id)
            .where(
                Approval.organization_id == principal.organization_id,
                Workspace.organization_id == principal.organization_id,
                workspace_access_clause(principal),
            )
        )
        if status is not None:
            statement = statement.where(Approval.status == status)
        result = await session.scalars(statement.order_by(Approval.created_at.desc()).limit(500))
        return list(result)

    async def decide(
        self,
        session: AsyncSession,
        principal: Principal,
        approval_id: UUID,
        *,
        approve: bool,
        reason: str,
    ) -> Approval:
        if not principal.can("approval.decide"):
            raise AuthorizationError(
                "approval_decide_denied", "Approval decisions are not permitted"
            )
        approval = await session.scalar(
            select(Approval)
            .where(
                Approval.id == approval_id,
                Approval.organization_id == principal.organization_id,
            )
            .with_for_update()
        )
        if approval is None:
            raise NotFoundError("Approval", approval_id)
        if approval.status != ApprovalStatus.PENDING:
            raise ConflictError(
                "approval_already_decided",
                "The approval is no longer pending",
                status=approval.status,
            )
        run = await require_run_access(
            session,
            principal,
            approval.run_id,
            write=True,
            for_update=True,
        )
        now = utc_now()
        if ensure_utc(approval.expires_at) <= now:
            approval.status = ApprovalStatus.EXPIRED
            approval.decided_at = now
            await self.events.append(
                session,
                EventDraft(
                    name="approval.expired",
                    aggregate_type="run",
                    aggregate_id=run.id,
                    organization_id=principal.organization_id,
                    correlation_id=run.id,
                    actor_type=ActorType.SYSTEM,
                    actor_id=None,
                    run_id=run.id,
                    payload={"approval_id": str(approval.id)},
                ),
            )
            await self.audit.write(
                session,
                AuditDraft(
                    organization_id=principal.organization_id,
                    correlation_id=run.id,
                    actor_type=ActorType.SYSTEM,
                    actor_id=None,
                    action="approval.expire",
                    resource_type="approval",
                    resource_id=str(approval.id),
                    outcome=ApprovalStatus.EXPIRED,
                    approval_id=approval.id,
                ),
            )
            raise ConflictError("approval_expired", "The approval request has expired")
        constraints = approval.approver_constraints
        required_permission = constraints.get("permission")
        if required_permission and not principal.can(required_permission):
            raise AuthorizationError(
                "approver_constraint_failed", "The approver does not meet policy constraints"
            )
        if constraints.get("disallow_self") and approval.requested_by == principal.id:
            raise AuthorizationError(
                "self_approval_denied", "The requester cannot approve their own capability request"
            )

        approval.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
        approval.decided_by = principal.id
        approval.decision_reason = reason.strip()
        approval.decided_at = now

        event_name = "approval.approved" if approve else "approval.rejected"
        if not is_terminal(run.status):
            if approve and run.status == RunStatus.WAITING_APPROVAL:
                validate_run_transition(run.status, RunStatus.RUNNING)
                run.status = RunStatus.RUNNING
                run.lease_owner = None
                run.lease_expires_at = None
            elif not approve:
                validate_run_transition(run.status, RunStatus.FAILED)
                run.status = RunStatus.FAILED
                run.error_code = "approval_rejected"
                run.error_message = "The requested capability was rejected"
                run.completed_at = now

        await self.events.append(
            session,
            EventDraft(
                name=event_name,
                aggregate_type="run",
                aggregate_id=run.id,
                organization_id=principal.organization_id,
                correlation_id=run.id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                run_id=run.id,
                payload={"approval_id": str(approval.id), "reason": approval.decision_reason},
            ),
        )
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=run.id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action="approval.decide",
                resource_type="approval",
                resource_id=str(approval.id),
                outcome=approval.status,
                approval_id=approval.id,
            ),
        )
        approval_counter.add(1, {"decision": approval.status.value, "kind": "capability"})
        return approval
