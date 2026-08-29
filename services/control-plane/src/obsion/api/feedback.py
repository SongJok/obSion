from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.feedback.schemas import RecordRunFeedbackRequest, RunFeedbackView
from obsion.feedback.service import RunFeedbackService
from obsion.security.auth import get_principal, get_session
from obsion.security.identity import Principal

router = APIRouter(tags=["run feedback"])


def get_feedback_service() -> RunFeedbackService:
    return RunFeedbackService()


@router.get("/runs/{run_id}/feedback", response_model=RunFeedbackView | None)
async def get_run_feedback(
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: RunFeedbackService = Depends(get_feedback_service),
) -> RunFeedbackView | None:
    feedback = await service.get_feedback(session, principal, run_id)
    return RunFeedbackView.model_validate(feedback) if feedback else None


@router.put("/runs/{run_id}/feedback", response_model=RunFeedbackView)
async def record_run_feedback(
    run_id: UUID,
    request: RecordRunFeedbackRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: RunFeedbackService = Depends(get_feedback_service),
) -> RunFeedbackView:
    async with session.begin():
        feedback = await service.record_feedback(session, principal, run_id, request)
    return RunFeedbackView.model_validate(feedback)
