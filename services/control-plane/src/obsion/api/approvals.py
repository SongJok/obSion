from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import ApprovalDecisionRequest, ApprovalView
from obsion.application.approvals import ApprovalService
from obsion.common.errors import ConflictError
from obsion.domain.enums import ApprovalStatus
from obsion.security.auth import get_principal, get_session
from obsion.security.identity import Principal

router = APIRouter(tags=["approvals"])


def get_approval_service() -> ApprovalService:
    return ApprovalService()


@router.get("/approvals", response_model=list[ApprovalView])
async def list_approvals(
    approval_status: ApprovalStatus | None = Query(default=None, alias="status"),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ApprovalService = Depends(get_approval_service),
) -> list[ApprovalView]:
    approvals = await service.list(session, principal, approval_status)
    return [ApprovalView.model_validate(item) for item in approvals]


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalView)
async def approve(
    approval_id: UUID,
    request: ApprovalDecisionRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalView:
    expired_error: ConflictError | None = None
    approval = None
    async with session.begin():
        try:
            approval = await service.decide(
                session, principal, approval_id, approve=True, reason=request.reason
            )
        except ConflictError as exc:
            if exc.code != "approval_expired":
                raise
            expired_error = exc
    if expired_error is not None:
        raise expired_error
    assert approval is not None
    return ApprovalView.model_validate(approval)


@router.post("/approvals/{approval_id}/reject", response_model=ApprovalView)
async def reject(
    approval_id: UUID,
    request: ApprovalDecisionRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ApprovalService = Depends(get_approval_service),
) -> ApprovalView:
    expired_error: ConflictError | None = None
    approval = None
    async with session.begin():
        try:
            approval = await service.decide(
                session, principal, approval_id, approve=False, reason=request.reason
            )
        except ConflictError as exc:
            if exc.code != "approval_expired":
                raise
            expired_error = exc
    if expired_error is not None:
        raise expired_error
    assert approval is not None
    return ApprovalView.model_validate(approval)
