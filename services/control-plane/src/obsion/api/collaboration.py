from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import EventView
from obsion.collaboration.schemas import (
    CreateWorkspaceDecisionRequest,
    CreateWorkspaceTaskRequest,
    DecideWorkspaceDecisionRequest,
    ReviseWorkspaceDecisionRequest,
    UpdateWorkspaceTaskRequest,
    WorkspaceDecisionVersionView,
    WorkspaceDecisionView,
    WorkspaceTaskView,
)
from obsion.collaboration.service import WorkspaceCollaborationService
from obsion.db.models import WorkspaceDecision, WorkspaceDecisionVersion
from obsion.domain.enums import WorkspaceDecisionStatus, WorkspaceTaskStatus
from obsion.security.auth import get_principal, get_session
from obsion.security.identity import Principal

router = APIRouter(tags=["workspace collaboration"])


def get_collaboration_service() -> WorkspaceCollaborationService:
    return WorkspaceCollaborationService()


def _decision_view(
    decision: WorkspaceDecision, version: WorkspaceDecisionVersion
) -> WorkspaceDecisionView:
    return WorkspaceDecisionView(
        id=decision.id,
        workspace_id=decision.workspace_id,
        status=decision.status,
        current_version=decision.current_version,
        created_by=decision.created_by,
        decided_by=decision.decided_by,
        source_run_id=decision.source_run_id,
        supersedes_decision_id=decision.supersedes_decision_id,
        decided_at=decision.decided_at,
        created_at=decision.created_at,
        updated_at=decision.updated_at,
        title=version.title,
        summary=version.summary,
        rationale=version.rationale,
        alternatives=version.alternatives,
        checksum_sha256=version.checksum_sha256,
    )


@router.post(
    "/workspaces/{workspace_id}/tasks",
    response_model=WorkspaceTaskView,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_task(
    workspace_id: UUID,
    request: CreateWorkspaceTaskRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceCollaborationService = Depends(get_collaboration_service),
) -> WorkspaceTaskView:
    async with session.begin():
        task = await service.create_task(session, principal, workspace_id, request)
    return WorkspaceTaskView.model_validate(task)


@router.get("/workspaces/{workspace_id}/tasks", response_model=list[WorkspaceTaskView])
async def list_workspace_tasks(
    workspace_id: UUID,
    task_status: Annotated[WorkspaceTaskStatus | None, Query(alias="status")] = None,
    assignee_id: UUID | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceCollaborationService = Depends(get_collaboration_service),
) -> list[WorkspaceTaskView]:
    tasks = await service.list_tasks(
        session,
        principal,
        workspace_id,
        status=task_status,
        assignee_id=assignee_id,
        limit=limit,
    )
    return [WorkspaceTaskView.model_validate(item) for item in tasks]


@router.patch("/workspace-tasks/{task_id}", response_model=WorkspaceTaskView)
async def update_workspace_task(
    task_id: UUID,
    request: UpdateWorkspaceTaskRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceCollaborationService = Depends(get_collaboration_service),
) -> WorkspaceTaskView:
    async with session.begin():
        task = await service.update_task(session, principal, task_id, request)
    return WorkspaceTaskView.model_validate(task)


@router.get("/workspace-tasks/{task_id}/events", response_model=list[EventView])
async def list_workspace_task_events(
    task_id: UUID,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceCollaborationService = Depends(get_collaboration_service),
) -> list[EventView]:
    events = await service.list_task_events(
        session,
        principal,
        task_id,
        after_sequence=after_sequence,
        limit=limit,
    )
    return [EventView.model_validate(item) for item in events]


@router.post(
    "/workspaces/{workspace_id}/decisions",
    response_model=WorkspaceDecisionView,
    status_code=status.HTTP_201_CREATED,
)
async def create_workspace_decision(
    workspace_id: UUID,
    request: CreateWorkspaceDecisionRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceCollaborationService = Depends(get_collaboration_service),
) -> WorkspaceDecisionView:
    async with session.begin():
        decision, version = await service.create_decision(session, principal, workspace_id, request)
    return _decision_view(decision, version)


@router.get(
    "/workspaces/{workspace_id}/decisions",
    response_model=list[WorkspaceDecisionView],
)
async def list_workspace_decisions(
    workspace_id: UUID,
    decision_status: Annotated[WorkspaceDecisionStatus | None, Query(alias="status")] = None,
    limit: int = Query(default=200, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceCollaborationService = Depends(get_collaboration_service),
) -> list[WorkspaceDecisionView]:
    decisions = await service.list_decisions(
        session, principal, workspace_id, status=decision_status, limit=limit
    )
    return [_decision_view(decision, version) for decision, version in decisions]


@router.get(
    "/workspace-decisions/{decision_id}/versions",
    response_model=list[WorkspaceDecisionVersionView],
)
async def list_workspace_decision_versions(
    decision_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceCollaborationService = Depends(get_collaboration_service),
) -> list[WorkspaceDecisionVersionView]:
    versions = await service.list_decision_versions(session, principal, decision_id)
    return [WorkspaceDecisionVersionView.model_validate(item) for item in versions]


@router.patch("/workspace-decisions/{decision_id}", response_model=WorkspaceDecisionView)
async def revise_workspace_decision(
    decision_id: UUID,
    request: ReviseWorkspaceDecisionRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceCollaborationService = Depends(get_collaboration_service),
) -> WorkspaceDecisionView:
    async with session.begin():
        decision, version = await service.revise_decision(session, principal, decision_id, request)
    return _decision_view(decision, version)


async def _decide_workspace_decision(
    decision_id: UUID,
    request: DecideWorkspaceDecisionRequest,
    target: WorkspaceDecisionStatus,
    session: AsyncSession,
    principal: Principal,
    service: WorkspaceCollaborationService,
) -> WorkspaceDecisionView:
    async with session.begin():
        decision, version = await service.decide(
            session, principal, decision_id, request.expected_version, target
        )
    return _decision_view(decision, version)


@router.post("/workspace-decisions/{decision_id}/accept", response_model=WorkspaceDecisionView)
async def accept_workspace_decision(
    decision_id: UUID,
    request: DecideWorkspaceDecisionRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceCollaborationService = Depends(get_collaboration_service),
) -> WorkspaceDecisionView:
    return await _decide_workspace_decision(
        decision_id,
        request,
        WorkspaceDecisionStatus.ACCEPTED,
        session,
        principal,
        service,
    )


@router.post("/workspace-decisions/{decision_id}/reject", response_model=WorkspaceDecisionView)
async def reject_workspace_decision(
    decision_id: UUID,
    request: DecideWorkspaceDecisionRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceCollaborationService = Depends(get_collaboration_service),
) -> WorkspaceDecisionView:
    return await _decide_workspace_decision(
        decision_id,
        request,
        WorkspaceDecisionStatus.REJECTED,
        session,
        principal,
        service,
    )


@router.get("/workspace-decisions/{decision_id}/events", response_model=list[EventView])
async def list_workspace_decision_events(
    decision_id: UUID,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceCollaborationService = Depends(get_collaboration_service),
) -> list[EventView]:
    events = await service.list_decision_events(
        session,
        principal,
        decision_id,
        after_sequence=after_sequence,
        limit=limit,
    )
    return [EventView.model_validate(item) for item in events]
