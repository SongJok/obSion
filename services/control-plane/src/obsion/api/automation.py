from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.automation.schemas import (
    AutomationExecutionDetailView,
    AutomationExecutionView,
    AutomationStepView,
    CreateScheduleRequest,
    CreateWorkflowRequest,
    CreateWorkflowVersionRequest,
    NotificationView,
    ReviewAutomationStepRequest,
    TriggerWorkflowRequest,
    UpdateScheduleRequest,
    WorkflowCreatedView,
    WorkflowScheduleView,
    WorkflowVersionView,
    WorkflowView,
)
from obsion.automation.service import AutomationService
from obsion.domain.enums import WorkflowStatus
from obsion.security.auth import get_principal, get_session
from obsion.security.identity import Principal

router = APIRouter(tags=["automation"])


def get_automation_service() -> AutomationService:
    return AutomationService()


@router.post(
    "/workspaces/{workspace_id}/workflows",
    response_model=WorkflowCreatedView,
    status_code=status.HTTP_201_CREATED,
)
async def create_workflow(
    workspace_id: UUID,
    request: CreateWorkflowRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: AutomationService = Depends(get_automation_service),
) -> WorkflowCreatedView:
    async with session.begin():
        workflow, version = await service.create_workflow(session, principal, workspace_id, request)
    return WorkflowCreatedView(
        workflow=WorkflowView.model_validate(workflow),
        version=WorkflowVersionView.model_validate(version),
    )


@router.get("/workspaces/{workspace_id}/workflows", response_model=list[WorkflowView])
async def list_workflows(
    workspace_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: AutomationService = Depends(get_automation_service),
) -> list[WorkflowView]:
    workflows = await service.list_workflows(session, principal, workspace_id)
    return [WorkflowView.model_validate(item) for item in workflows]


@router.get("/workflows/{workflow_id}", response_model=WorkflowView)
async def get_workflow(
    workflow_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: AutomationService = Depends(get_automation_service),
) -> WorkflowView:
    return WorkflowView.model_validate(await service.get_workflow(session, principal, workflow_id))


@router.get("/workflows/{workflow_id}/versions", response_model=list[WorkflowVersionView])
async def list_workflow_versions(
    workflow_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: AutomationService = Depends(get_automation_service),
) -> list[WorkflowVersionView]:
    versions = await service.list_versions(session, principal, workflow_id)
    return [WorkflowVersionView.model_validate(item) for item in versions]


@router.post(
    "/workflows/{workflow_id}/versions",
    response_model=WorkflowVersionView,
    status_code=status.HTTP_201_CREATED,
)
async def create_workflow_version(
    workflow_id: UUID,
    request: CreateWorkflowVersionRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: AutomationService = Depends(get_automation_service),
) -> WorkflowVersionView:
    async with session.begin():
        version = await service.create_version(session, principal, workflow_id, request.spec)
    return WorkflowVersionView.model_validate(version)


@router.post(
    "/workflows/{workflow_id}/versions/{version}/publish",
    response_model=WorkflowCreatedView,
)
async def publish_workflow_version(
    workflow_id: UUID,
    version: int,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: AutomationService = Depends(get_automation_service),
) -> WorkflowCreatedView:
    async with session.begin():
        workflow, published = await service.publish_version(
            session, principal, workflow_id, version
        )
    return WorkflowCreatedView(
        workflow=WorkflowView.model_validate(workflow),
        version=WorkflowVersionView.model_validate(published),
    )


async def _set_workflow_status(
    workflow_id: UUID,
    target: WorkflowStatus,
    session: AsyncSession,
    principal: Principal,
    service: AutomationService,
) -> WorkflowView:
    async with session.begin():
        workflow = await service.set_workflow_status(session, principal, workflow_id, target)
    return WorkflowView.model_validate(workflow)


@router.post("/workflows/{workflow_id}/pause", response_model=WorkflowView)
async def pause_workflow(
    workflow_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: AutomationService = Depends(get_automation_service),
) -> WorkflowView:
    return await _set_workflow_status(
        workflow_id, WorkflowStatus.PAUSED, session, principal, service
    )


@router.post("/workflows/{workflow_id}/activate", response_model=WorkflowView)
async def activate_workflow(
    workflow_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: AutomationService = Depends(get_automation_service),
) -> WorkflowView:
    return await _set_workflow_status(
        workflow_id, WorkflowStatus.ACTIVE, session, principal, service
    )


@router.post("/workflows/{workflow_id}/retire", response_model=WorkflowView)
async def retire_workflow(
    workflow_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: AutomationService = Depends(get_automation_service),
) -> WorkflowView:
    return await _set_workflow_status(
        workflow_id, WorkflowStatus.RETIRED, session, principal, service
    )


@router.post(
    "/workflows/{workflow_id}/schedules",
    response_model=WorkflowScheduleView,
    status_code=status.HTTP_201_CREATED,
)
async def create_schedule(
    workflow_id: UUID,
    request: CreateScheduleRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: AutomationService = Depends(get_automation_service),
) -> WorkflowScheduleView:
    async with session.begin():
        schedule = await service.create_schedule(session, principal, workflow_id, request)
    return WorkflowScheduleView.model_validate(schedule)


@router.get("/workflows/{workflow_id}/schedules", response_model=list[WorkflowScheduleView])
async def list_schedules(
    workflow_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: AutomationService = Depends(get_automation_service),
) -> list[WorkflowScheduleView]:
    schedules = await service.list_schedules(session, principal, workflow_id)
    return [WorkflowScheduleView.model_validate(item) for item in schedules]


@router.patch("/automation/schedules/{schedule_id}", response_model=WorkflowScheduleView)
async def update_schedule(
    schedule_id: UUID,
    request: UpdateScheduleRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: AutomationService = Depends(get_automation_service),
) -> WorkflowScheduleView:
    async with session.begin():
        schedule = await service.set_schedule_enabled(
            session, principal, schedule_id, request.enabled
        )
    return WorkflowScheduleView.model_validate(schedule)


@router.post(
    "/workflows/{workflow_id}/trigger",
    response_model=AutomationExecutionView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_workflow(
    workflow_id: UUID,
    request: TriggerWorkflowRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: AutomationService = Depends(get_automation_service),
) -> AutomationExecutionView:
    async with session.begin():
        execution = await service.trigger_workflow(
            session,
            principal,
            workflow_id,
            input_payload=request.input_payload,
            idempotency_key=request.idempotency_key,
        )
    return AutomationExecutionView.model_validate(execution)


@router.get("/workflows/{workflow_id}/executions", response_model=list[AutomationExecutionView])
async def list_executions(
    workflow_id: UUID,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: AutomationService = Depends(get_automation_service),
) -> list[AutomationExecutionView]:
    executions = await service.list_executions(session, principal, workflow_id, limit=limit)
    return [AutomationExecutionView.model_validate(item) for item in executions]


@router.get("/automation/executions/{execution_id}", response_model=AutomationExecutionDetailView)
async def get_execution(
    execution_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: AutomationService = Depends(get_automation_service),
) -> AutomationExecutionDetailView:
    execution = await service.get_execution(session, principal, execution_id)
    steps = await service.list_steps(session, principal, execution_id)
    return AutomationExecutionDetailView(
        **AutomationExecutionView.model_validate(execution).model_dump(),
        steps=[AutomationStepView.model_validate(item) for item in steps],
    )


@router.post("/automation/executions/{execution_id}/cancel", response_model=AutomationExecutionView)
async def cancel_execution(
    execution_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: AutomationService = Depends(get_automation_service),
) -> AutomationExecutionView:
    async with session.begin():
        execution = await service.cancel_execution(session, principal, execution_id)
    return AutomationExecutionView.model_validate(execution)


@router.post("/automation/steps/{step_id}/review", response_model=AutomationStepView)
async def review_step(
    step_id: UUID,
    request: ReviewAutomationStepRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: AutomationService = Depends(get_automation_service),
) -> AutomationStepView:
    async with session.begin():
        step = await service.review_step(session, principal, step_id, request)
    return AutomationStepView.model_validate(step)


@router.get("/notifications", response_model=list[NotificationView])
async def list_notifications(
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: AutomationService = Depends(get_automation_service),
) -> list[NotificationView]:
    notifications = await service.list_notifications(
        session, principal, unread_only=unread_only, limit=limit
    )
    return [NotificationView.model_validate(item) for item in notifications]


@router.post("/notifications/{notification_id}/read", response_model=NotificationView)
async def mark_notification_read(
    notification_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: AutomationService = Depends(get_automation_service),
) -> NotificationView:
    async with session.begin():
        notification = await service.mark_notification_read(session, principal, notification_id)
    return NotificationView.model_validate(notification)
