from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import ConflictError
from obsion.common.ids import new_id
from obsion.db.models import RunFeedback, Thread, Turn, Workspace
from obsion.domain.enums import ActorType
from obsion.domain.run_state import is_terminal
from obsion.feedback.schemas import RecordRunFeedbackRequest
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.persistence.events import EventDraft, EventStore
from obsion.security.identity import Principal
from obsion.security.redaction import redact_text
from obsion.security.workspace_access import require_run_access
from obsion.telemetry import run_satisfaction


class RunFeedbackService:
    def __init__(
        self,
        event_store: EventStore | None = None,
        audit: AuditWriter | None = None,
    ) -> None:
        self.events = event_store or EventStore()
        self.audit = audit or AuditWriter()

    async def get_feedback(
        self,
        session: AsyncSession,
        principal: Principal,
        run_id: UUID,
    ) -> RunFeedback | None:
        await require_run_access(session, principal, run_id)
        feedback = await session.scalar(
            select(RunFeedback).where(
                RunFeedback.organization_id == principal.organization_id,
                RunFeedback.run_id == run_id,
                RunFeedback.user_id == principal.id,
            )
        )
        return feedback

    async def record_feedback(
        self,
        session: AsyncSession,
        principal: Principal,
        run_id: UUID,
        request: RecordRunFeedbackRequest,
    ) -> RunFeedback:
        # Serializing mutations on the run prevents concurrent first submissions
        # from escaping the feedback version contract through the unique index.
        run = await require_run_access(session, principal, run_id, for_update=True)
        if not is_terminal(run.status):
            raise ConflictError(
                "run_feedback_run_active",
                "Feedback can be recorded after the run reaches a terminal state",
                current_status=run.status,
            )
        workspace = await session.scalar(
            select(Workspace)
            .select_from(Turn)
            .join(Thread, Thread.id == Turn.thread_id)
            .join(Workspace, Workspace.id == Thread.workspace_id)
            .where(
                Turn.id == run.turn_id,
                Workspace.organization_id == principal.organization_id,
            )
        )
        if workspace is None:
            raise ConflictError(
                "run_feedback_workspace_missing",
                "The run workspace is unavailable",
            )
        reason = redact_text(request.reason.strip())
        feedback = await session.scalar(
            select(RunFeedback)
            .where(
                RunFeedback.organization_id == principal.organization_id,
                RunFeedback.run_id == run.id,
                RunFeedback.user_id == principal.id,
            )
            .with_for_update()
        )
        created = feedback is None
        if feedback is None:
            if request.expected_version is not None:
                raise ConflictError(
                    "run_feedback_version_conflict",
                    "No prior feedback version exists for this run",
                    expected_version=request.expected_version,
                    current_version=None,
                )
            feedback = RunFeedback(
                organization_id=principal.organization_id,
                run_id=run.id,
                user_id=principal.id,
                rating=request.rating,
                reason=reason,
                version=1,
            )
            session.add(feedback)
        else:
            if feedback.rating == request.rating and feedback.reason == reason:
                return feedback
            if request.expected_version != feedback.version:
                raise ConflictError(
                    "run_feedback_version_conflict",
                    "Feedback changed after it was loaded; refresh and retry",
                    expected_version=request.expected_version,
                    current_version=feedback.version,
                )
            feedback.rating = request.rating
            feedback.reason = reason
            feedback.version += 1
        await session.flush()
        run_satisfaction.add(
            1,
            {
                "rating": str(feedback.rating),
                "revised": str(not created).lower(),
            },
        )

        correlation_id = new_id()
        await self.events.append(
            session,
            EventDraft(
                name="run.feedback.recorded" if created else "run.feedback.revised",
                aggregate_type="run",
                aggregate_id=run.id,
                organization_id=principal.organization_id,
                correlation_id=correlation_id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                run_id=run.id,
                classification=workspace.classification,
                payload={
                    "rating": feedback.rating,
                    "reason_provided": bool(feedback.reason),
                    "feedback_version": feedback.version,
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
                action="run_feedback.record" if created else "run_feedback.revise",
                resource_type="run_feedback",
                resource_id=str(feedback.id),
                outcome="SUCCESS",
                metadata={
                    "run_id": str(run.id),
                    "rating": feedback.rating,
                    "reason_provided": bool(feedback.reason),
                    "version": feedback.version,
                },
            ),
        )
        return feedback
