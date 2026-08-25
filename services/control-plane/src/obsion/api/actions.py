from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.actions.gateway import ActionGateway
from obsion.actions.schemas import (
    ActionApprovalView,
    ActionDetailView,
    ActionRequestView,
    CreateActionRequest,
    DecideActionApprovalRequest,
    PreflightActionRequest,
    RequestRollbackRequest,
)
from obsion.actions.service import ActionService
from obsion.api.dependencies import get_action_gateway
from obsion.api.schemas import EventView
from obsion.common.errors import ConflictError
from obsion.db.models import Event
from obsion.domain.enums import ActionStatus, ApprovalStatus
from obsion.security.auth import get_principal, get_session
from obsion.security.identity import Principal

router = APIRouter(tags=["actions"])


def get_action_service(
    gateway: ActionGateway = Depends(get_action_gateway),
) -> ActionService:
    return ActionService(gateway)


@router.post(
    "/workspaces/{workspace_id}/actions",
    response_model=ActionRequestView,
    status_code=status.HTTP_201_CREATED,
)
async def create_action(
    workspace_id: UUID,
    request: CreateActionRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ActionService = Depends(get_action_service),
) -> ActionRequestView:
    async with session.begin():
        action = await service.create(session, principal, workspace_id, request)
    return ActionRequestView.model_validate(action)


@router.get("/workspaces/{workspace_id}/actions", response_model=list[ActionRequestView])
async def list_actions(
    workspace_id: UUID,
    action_status: ActionStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ActionService = Depends(get_action_service),
) -> list[ActionRequestView]:
    actions = await service.list(
        session, principal, workspace_id, status=action_status, limit=limit
    )
    return [ActionRequestView.model_validate(item) for item in actions]


@router.get("/actions/{action_id}", response_model=ActionDetailView)
async def get_action(
    action_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ActionService = Depends(get_action_service),
) -> ActionDetailView:
    return await service.detail(session, principal, action_id)


@router.post("/actions/{action_id}/preflight", response_model=ActionDetailView)
async def preflight_action(
    action_id: UUID,
    request: PreflightActionRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ActionService = Depends(get_action_service),
) -> ActionDetailView:
    async with session.begin():
        await service.preflight(
            session,
            principal,
            action_id,
            reason=request.reason,
            approval_ttl_minutes=request.approval_ttl_minutes,
        )
    return await service.detail(session, principal, action_id)


@router.get("/action-approvals", response_model=list[ActionApprovalView])
async def list_action_approvals(
    approval_status: ApprovalStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ActionService = Depends(get_action_service),
) -> list[ActionApprovalView]:
    approvals = await service.list_approvals(
        session, principal, status=approval_status, limit=limit
    )
    return [ActionApprovalView.model_validate(item) for item in approvals]


async def _decide(
    approval_id: UUID,
    request: DecideActionApprovalRequest,
    approve: bool,
    session: AsyncSession,
    principal: Principal,
    service: ActionService,
) -> ActionApprovalView:
    approval = None
    try:
        async with session.begin():
            approval = await service.decide(
                session,
                principal,
                approval_id,
                approve=approve,
                reason=request.reason,
            )
    except ConflictError as exc:
        if exc.code != "action_approval_expired":
            raise
        await session.commit()
        raise
    assert approval is not None
    return ActionApprovalView.model_validate(approval)


@router.post("/action-approvals/{approval_id}/approve", response_model=ActionApprovalView)
async def approve_action(
    approval_id: UUID,
    request: DecideActionApprovalRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ActionService = Depends(get_action_service),
) -> ActionApprovalView:
    return await _decide(approval_id, request, True, session, principal, service)


@router.post("/action-approvals/{approval_id}/reject", response_model=ActionApprovalView)
async def reject_action(
    approval_id: UUID,
    request: DecideActionApprovalRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ActionService = Depends(get_action_service),
) -> ActionApprovalView:
    return await _decide(approval_id, request, False, session, principal, service)


@router.post("/actions/{action_id}/rollback", response_model=ActionRequestView)
async def request_action_rollback(
    action_id: UUID,
    request: RequestRollbackRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ActionService = Depends(get_action_service),
) -> ActionRequestView:
    async with session.begin():
        action = await service.request_rollback(
            session,
            principal,
            action_id,
            reason=request.reason,
            approval_ttl_minutes=request.approval_ttl_minutes,
        )
    return ActionRequestView.model_validate(action)


@router.post("/actions/{action_id}/cancel", response_model=ActionRequestView)
async def cancel_action(
    action_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ActionService = Depends(get_action_service),
) -> ActionRequestView:
    async with session.begin():
        action = await service.cancel(session, principal, action_id)
    return ActionRequestView.model_validate(action)


@router.get("/actions/{action_id}/events", response_model=list[EventView])
async def list_action_events(
    action_id: UUID,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ActionService = Depends(get_action_service),
) -> list[EventView]:
    await service.get(session, principal, action_id)
    events = await session.scalars(
        select(Event)
        .where(
            Event.organization_id == principal.organization_id,
            Event.aggregate_type == "action_request",
            Event.aggregate_id == action_id,
            Event.sequence > after,
        )
        .order_by(Event.sequence)
        .limit(limit)
    )
    return [EventView.model_validate(item) for item in events]
