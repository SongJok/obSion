from uuid import UUID

from sqlalchemy import exists, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from obsion.common.errors import AuthorizationError, NotFoundError
from obsion.db.models import Run, Thread, Turn, Workspace, WorkspaceMember
from obsion.domain.enums import Visibility
from obsion.security.identity import Principal


def workspace_access_clause(principal: Principal, *, write: bool = False) -> ColumnElement[bool]:
    elevated_permission = "workspace.manage.all" if write else "workspace.read.all"
    if principal.can(elevated_permission):
        return true()
    membership = exists(
        select(WorkspaceMember.workspace_id).where(
            WorkspaceMember.workspace_id == Workspace.id,
            WorkspaceMember.organization_id == principal.organization_id,
            WorkspaceMember.user_id == principal.id,
            *((WorkspaceMember.can_write.is_(True),) if write else ()),
        )
    )
    if write:
        return or_(Workspace.owner_id == principal.id, membership)
    return or_(
        Workspace.owner_id == principal.id,
        Workspace.visibility == Visibility.ORGANIZATION,
        membership,
    )


async def require_workspace_access(
    session: AsyncSession,
    principal: Principal,
    workspace_id: UUID,
    *,
    write: bool = False,
) -> Workspace:
    workspace = await session.scalar(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.organization_id == principal.organization_id,
            workspace_access_clause(principal, write=write),
        )
    )
    if workspace is None:
        if write:
            exists_in_organization = await session.scalar(
                select(Workspace.id).where(
                    Workspace.id == workspace_id,
                    Workspace.organization_id == principal.organization_id,
                )
            )
            if exists_in_organization is not None:
                raise AuthorizationError(
                    "workspace_write_denied", "Workspace changes are not permitted"
                )
        raise NotFoundError("Workspace", workspace_id)
    return workspace


async def require_thread_access(
    session: AsyncSession,
    principal: Principal,
    thread_id: UUID,
    *,
    write: bool = False,
    for_update: bool = False,
) -> Thread:
    statement = (
        select(Thread)
        .join(Workspace, Workspace.id == Thread.workspace_id)
        .where(
            Thread.id == thread_id,
            Thread.organization_id == principal.organization_id,
            Workspace.organization_id == principal.organization_id,
            workspace_access_clause(principal, write=write),
        )
    )
    if for_update:
        statement = statement.with_for_update()
    thread = await session.scalar(statement)
    if thread is None:
        raise NotFoundError("Thread", thread_id)
    return thread


async def require_run_access(
    session: AsyncSession,
    principal: Principal,
    run_id: UUID,
    *,
    write: bool = False,
    for_update: bool = False,
) -> Run:
    statement = (
        select(Run)
        .join(Turn, Turn.id == Run.turn_id)
        .join(Thread, Thread.id == Turn.thread_id)
        .join(Workspace, Workspace.id == Thread.workspace_id)
        .where(
            Run.id == run_id,
            Run.organization_id == principal.organization_id,
            Workspace.organization_id == principal.organization_id,
            workspace_access_clause(principal, write=write),
        )
    )
    if for_update:
        statement = statement.with_for_update()
    run = await session.scalar(statement)
    if run is None:
        raise NotFoundError("Run", run_id)
    return run
