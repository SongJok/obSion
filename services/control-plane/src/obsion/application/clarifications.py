from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import ConflictError, ValidationError
from obsion.common.time import ensure_utc, utc_now
from obsion.db.models import Run
from obsion.domain.enums import ActorType, RunStatus
from obsion.domain.run_intent import (
    ClarificationAnswerInvalid,
    ClarificationAnswerSubmission,
    ClarificationDomainError,
    ClarificationNotPending,
    ClarificationStaleRevision,
    RunIntent,
    apply_clarification_answer,
    close_active_clarification,
    parse_run_intent,
)
from obsion.domain.run_state import validate_run_transition
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.persistence.events import EventDraft, EventStore
from obsion.security.identity import Principal
from obsion.security.workspace_access import require_run_access


class ClarificationService:
    def __init__(self) -> None:
        self.events = EventStore()
        self.audit = AuditWriter()

    async def answer(
        self,
        session: AsyncSession,
        principal: Principal,
        run_id: UUID,
        clarification_id: UUID,
        submission: ClarificationAnswerSubmission,
    ) -> Run:
        run = await require_run_access(
            session,
            principal,
            run_id,
            write=True,
            for_update=True,
        )
        if run.status != RunStatus.WAITING_USER:
            raise ConflictError(
                "clarification_not_pending",
                "The run is not waiting for clarification",
                status=run.status,
            )
        try:
            intent = parse_run_intent(run.intent, run.status)
        except ClarificationDomainError as exc:
            raise ConflictError(
                "clarification_state_invalid",
                "The persisted clarification state cannot be resumed",
            ) from exc
        if intent is None:
            raise ConflictError(
                "clarification_state_invalid",
                "The persisted clarification state cannot be resumed",
            )
        active = intent.clarification.active_request()
        if active is None or active.id != str(clarification_id):
            raise ConflictError(
                "clarification_not_pending",
                "The clarification is no longer the active request",
            )
        now = utc_now()
        if ensure_utc(datetime.fromisoformat(active.expires_at)) <= now:
            await self.expire(session, run, intent, expired_at=now)
            raise ConflictError("clarification_expired", "The clarification request has expired")
        remaining = intent.clarification.remaining_execution_seconds
        if remaining is None:
            raise ConflictError(
                "clarification_state_invalid",
                "The clarification has no resumable execution budget",
            )
        try:
            applied = apply_clarification_answer(
                intent,
                submission,
                clarification_id=str(clarification_id),
                answered_by=str(principal.id),
                answered_at=now,
            )
        except ClarificationStaleRevision as exc:
            raise ConflictError(
                "clarification_stale_revision",
                "The clarification answer targets a stale intent revision",
            ) from exc
        except ClarificationNotPending as exc:
            raise ConflictError(
                "clarification_not_pending",
                "The clarification is no longer pending",
            ) from exc
        except ClarificationAnswerInvalid as exc:
            raise ValidationError("clarification_answer_invalid", str(exc)) from exc
        except ClarificationDomainError as exc:
            raise ConflictError(
                "clarification_state_invalid",
                "The clarification state rejected the answer",
            ) from exc

        validate_run_transition(run.status, RunStatus.RUNNING)
        run.intent = applied.intent.model_dump(mode="json")
        run.status = RunStatus.RUNNING
        run.deadline_at = now + timedelta(seconds=remaining)
        run.waiting_user_expires_at = None
        run.lease_owner = None
        run.lease_expires_at = None
        await self.events.append(
            session,
            EventDraft(
                name="clarification.answered",
                aggregate_type="run",
                aggregate_id=run.id,
                organization_id=run.organization_id,
                correlation_id=run.id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                run_id=run.id,
                payload={
                    "clarification_id": applied.clarification_id,
                    "intent_revision": applied.intent.intent_revision,
                    "answered_slots": applied.answered_slots,
                    "response_fingerprint": applied.response_fingerprint,
                },
            ),
        )
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=run.organization_id,
                correlation_id=run.id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action="run.clarification.answer",
                resource_type="run",
                resource_id=str(run.id),
                outcome="SUCCESS",
                metadata={
                    "clarification_id": applied.clarification_id,
                    "intent_revision": applied.intent.intent_revision,
                    "answered_slots": applied.answered_slots,
                    "response_fingerprint": applied.response_fingerprint,
                },
            ),
        )
        return run

    async def expire(
        self,
        session: AsyncSession,
        run: Run,
        intent: RunIntent,
        *,
        expired_at: datetime,
    ) -> None:
        closed = close_active_clarification(intent, status="EXPIRED", closed_at=expired_at)
        validate_run_transition(run.status, RunStatus.FAILED)
        run.intent = closed.model_dump(mode="json")
        run.status = RunStatus.FAILED
        run.error_code = "clarification_expired"
        run.error_message = "The clarification request expired before an answer was received"
        run.completed_at = expired_at
        run.deadline_at = None
        run.waiting_user_expires_at = None
        run.lease_owner = None
        run.lease_expires_at = None
        clarification = intent.clarification.active_request()
        clarification_id = clarification.id if clarification is not None else None
        await self.events.append(
            session,
            EventDraft(
                name="clarification.expired",
                aggregate_type="run",
                aggregate_id=run.id,
                organization_id=run.organization_id,
                correlation_id=run.id,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                run_id=run.id,
                payload={"clarification_id": clarification_id},
            ),
        )
        await self.events.append(
            session,
            EventDraft(
                name="run.failed",
                aggregate_type="run",
                aggregate_id=run.id,
                organization_id=run.organization_id,
                correlation_id=run.id,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                run_id=run.id,
                payload={"error_code": "clarification_expired"},
            ),
        )
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=run.organization_id,
                correlation_id=run.id,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                action="run.clarification.expire",
                resource_type="run",
                resource_id=str(run.id),
                outcome="EXPIRED",
                metadata={"clarification_id": clarification_id},
            ),
        )
