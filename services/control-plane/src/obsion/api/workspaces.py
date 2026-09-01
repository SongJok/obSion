from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.dependencies import get_workspace_service
from obsion.api.schemas import (
    CreateThreadRequest,
    CreateTurnRequest,
    CreateWorkspaceRequest,
    EventView,
    ForkThreadRequest,
    RunView,
    SetWorkspaceMemberRequest,
    ThreadView,
    TurnCreatedView,
    TurnView,
    WorkspaceMemberView,
    WorkspaceView,
)
from obsion.application.workspaces import WorkspaceService
from obsion.db.models import User, WorkspaceMember
from obsion.security.auth import get_principal, get_session
from obsion.security.identity import Principal

router = APIRouter(tags=["workspace"])


async def _member_views(
    session: AsyncSession,
    principal: Principal,
    members: list[WorkspaceMember],
) -> list[WorkspaceMemberView]:
    users = {
        user.id: user
        for user in await session.scalars(
            select(User).where(
                User.organization_id == principal.organization_id,
                User.id.in_([member.user_id for member in members]),
            )
        )
    }
    return [
        WorkspaceMemberView(
            workspace_id=member.workspace_id,
            user_id=member.user_id,
            display_name=users[member.user_id].display_name,
            email=users[member.user_id].email,
            permissions=list(member.permissions),
            created_by=member.created_by,
            created_at=member.created_at,
        )
        for member in members
        if member.user_id in users
    ]


@router.post("/workspaces", response_model=WorkspaceView, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    request: CreateWorkspaceRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceView:
    async with session.begin():
        workspace = await service.create_workspace(session, principal, request)
    return WorkspaceView.model_validate(workspace)


@router.get("/workspaces", response_model=list[WorkspaceView])
async def list_workspaces(
    include_archived: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceService = Depends(get_workspace_service),
) -> list[WorkspaceView]:
    workspaces = await service.list_workspaces(session, principal, include_archived)
    return [WorkspaceView.model_validate(item) for item in workspaces]


@router.put("/workspaces/{workspace_id}/members", response_model=WorkspaceMemberView)
async def set_workspace_member(
    workspace_id: UUID,
    request: SetWorkspaceMemberRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceService = Depends(get_workspace_service),
) -> WorkspaceMemberView:
    async with session.begin():
        member = await service.add_member(
            session,
            principal,
            workspace_id,
            request.user_id,
            request.permissions,
        )
    views = await _member_views(session, principal, [member])
    return views[0]


@router.get("/workspaces/{workspace_id}/members", response_model=list[WorkspaceMemberView])
async def list_workspace_members(
    workspace_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceService = Depends(get_workspace_service),
) -> list[WorkspaceMemberView]:
    members = await service.list_members(session, principal, workspace_id)
    return await _member_views(session, principal, members)


@router.delete(
    "/workspaces/{workspace_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_workspace_member(
    workspace_id: UUID,
    user_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceService = Depends(get_workspace_service),
) -> None:
    async with session.begin():
        await service.remove_member(session, principal, workspace_id, user_id)


@router.post("/threads", response_model=ThreadView, status_code=status.HTTP_201_CREATED)
async def create_thread(
    request: CreateThreadRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceService = Depends(get_workspace_service),
) -> ThreadView:
    async with session.begin():
        thread = await service.create_thread(
            session, principal, request.workspace_id, request.title
        )
    return ThreadView.model_validate(thread)


@router.get("/workspaces/{workspace_id}/threads", response_model=list[ThreadView])
async def list_threads(
    workspace_id: UUID,
    include_archived: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceService = Depends(get_workspace_service),
) -> list[ThreadView]:
    threads = await service.list_threads(session, principal, workspace_id, include_archived)
    return [ThreadView.model_validate(item) for item in threads]


@router.post("/threads/{thread_id}/archive", response_model=ThreadView)
async def archive_thread(
    thread_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceService = Depends(get_workspace_service),
) -> ThreadView:
    async with session.begin():
        thread = await service.archive_thread(session, principal, thread_id)
    return ThreadView.model_validate(thread)


@router.post("/threads/{thread_id}/resume", response_model=ThreadView)
async def resume_thread(
    thread_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceService = Depends(get_workspace_service),
) -> ThreadView:
    async with session.begin():
        thread = await service.resume_thread(session, principal, thread_id)
    return ThreadView.model_validate(thread)


@router.post(
    "/threads/{thread_id}/fork", response_model=ThreadView, status_code=status.HTTP_201_CREATED
)
async def fork_thread(
    thread_id: UUID,
    request: ForkThreadRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceService = Depends(get_workspace_service),
) -> ThreadView:
    async with session.begin():
        thread = await service.fork_thread(
            session, principal, thread_id, request.from_turn_id, request.title
        )
    return ThreadView.model_validate(thread)


@router.get("/threads/{thread_id}/events", response_model=list[EventView])
async def list_thread_events(
    thread_id: UUID,
    after_sequence: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceService = Depends(get_workspace_service),
) -> list[EventView]:
    events = await service.list_thread_events(
        session,
        principal,
        thread_id,
        after_sequence=after_sequence,
        limit=limit,
    )
    return [EventView.model_validate(event) for event in events]


@router.post(
    "/threads/{thread_id}/turns",
    response_model=TurnCreatedView,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_turn(
    thread_id: UUID,
    request: CreateTurnRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceService = Depends(get_workspace_service),
) -> TurnCreatedView:
    async with session.begin():
        turn, run = await service.create_turn(session, principal, thread_id, request)
    return TurnCreatedView(
        turn=TurnView.model_validate(turn),
        run=RunView.model_validate(run),
    )


@router.get("/threads/{thread_id}/turns", response_model=list[TurnView])
async def list_turns(
    thread_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceService = Depends(get_workspace_service),
) -> list[TurnView]:
    turns = await service.list_turns(session, principal, thread_id)
    return [TurnView.model_validate(item) for item in turns]


@router.get("/threads/{thread_id}/runs", response_model=list[RunView])
async def list_thread_runs(
    thread_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceService = Depends(get_workspace_service),
) -> list[RunView]:
    runs = await service.list_thread_runs(session, principal, thread_id)
    return [RunView.model_validate(item) for item in runs]


@router.get("/runs/{run_id}", response_model=RunView)
async def get_run(
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceService = Depends(get_workspace_service),
) -> RunView:
    return RunView.model_validate(await service.get_run(session, principal, run_id))


@router.post("/runs/{run_id}/cancel", response_model=RunView)
async def cancel_run(
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceService = Depends(get_workspace_service),
) -> RunView:
    async with session.begin():
        run = await service.cancel_run(session, principal, run_id)
    return RunView.model_validate(run)


@router.post("/runs/{run_id}/replay", response_model=RunView, status_code=status.HTTP_202_ACCEPTED)
async def replay_run(
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: WorkspaceService = Depends(get_workspace_service),
) -> RunView:
    async with session.begin():
        run = await service.replay_run(session, principal, run_id)
    return RunView.model_validate(run)
