import asyncio
import json
from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import EventView
from obsion.config import Settings
from obsion.db.models import Run
from obsion.domain.run_state import is_terminal
from obsion.persistence.events import EventStore
from obsion.security.auth import get_app_settings, get_principal, get_session
from obsion.security.identity import Principal
from obsion.security.workspace_access import require_run_access, require_workspace_access

router = APIRouter(tags=["events"])


@router.get("/runs/{run_id}/events", response_model=list[EventView])
async def list_run_events(
    run_id: UUID,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=500, ge=1, le=2000),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[EventView]:
    await require_run_access(session, principal, run_id)
    events = await EventStore().list_run(
        session, principal.organization_id, run_id, after_sequence=after, limit=limit
    )
    return [EventView.model_validate(event) for event in events]


@router.get("/workspaces/{workspace_id}/timeline", response_model=list[EventView])
async def list_workspace_timeline(
    workspace_id: UUID,
    limit: int = Query(default=500, ge=1, le=2000),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[EventView]:
    await require_workspace_access(session, principal, workspace_id)
    events = await EventStore().list_workspace(
        session,
        principal.organization_id,
        workspace_id,
        limit=limit,
    )
    return [EventView.model_validate(event) for event in events]


@router.get("/runs/{run_id}/events/stream")
async def stream_run_events(
    request: Request,
    run_id: UUID,
    after: int = Query(default=0, ge=0),
    last_event_id: int | None = Header(default=None, alias="Last-Event-ID", ge=0),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    settings: Settings = Depends(get_app_settings),
) -> StreamingResponse:
    await require_run_access(session, principal, run_id)
    database = request.app.state.database

    async def generate() -> AsyncIterator[str]:
        cursor = max(after, last_event_id or 0)
        idle_seconds = 0.0
        poll_seconds = 0.5
        while True:
            if await request.is_disconnected():
                return
            async with database.sessions() as stream_session:
                await require_run_access(stream_session, principal, run_id)
                events = await EventStore().list_run(
                    stream_session,
                    principal.organization_id,
                    run_id,
                    after_sequence=cursor,
                    limit=200,
                )
            if events:
                idle_seconds = 0.0
                terminal = False
                for event in events:
                    assert event.run_sequence is not None
                    cursor = max(cursor, event.run_sequence)
                    body = EventView.model_validate(event).model_dump(mode="json")
                    yield f"id: {cursor}\nevent: {event.name}\ndata: {json.dumps(body)}\n\n"
                    if event.name in {"run.completed", "run.failed", "run.cancelled"}:
                        terminal = True
                if terminal:
                    return
            else:
                async with database.sessions() as status_session:
                    status = await status_session.scalar(
                        select(Run.status).where(
                            Run.id == run_id,
                            Run.organization_id == principal.organization_id,
                        )
                    )
                if status is not None and is_terminal(status):
                    return
                idle_seconds += poll_seconds
                if idle_seconds >= settings.event_stream_heartbeat_seconds:
                    yield ": heartbeat\n\n"
                    idle_seconds = 0.0
            await asyncio.sleep(poll_seconds)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
