from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import CreateMemoryRequest, MemoryDecisionRequest, MemoryView
from obsion.application.memory import MemoryService
from obsion.domain.enums import MemoryScope, MemoryStatus
from obsion.security.auth import get_principal, get_session
from obsion.security.identity import Principal

router = APIRouter(tags=["memory"])


def get_memory_service() -> MemoryService:
    return MemoryService()


@router.post("/memories", response_model=MemoryView, status_code=status.HTTP_201_CREATED)
async def create_memory_candidate(
    request: CreateMemoryRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: MemoryService = Depends(get_memory_service),
) -> MemoryView:
    async with session.begin():
        memory = await service.create_candidate(session, principal, request)
    return MemoryView.model_validate(memory)


@router.get("/memories", response_model=list[MemoryView])
async def list_memories(
    scope: MemoryScope | None = None,
    owner_ref: str | None = Query(default=None, max_length=300),
    memory_status: MemoryStatus | None = Query(default=None, alias="status"),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: MemoryService = Depends(get_memory_service),
) -> list[MemoryView]:
    async with session.begin():
        memories = await service.list(
            session,
            principal,
            scope=scope,
            owner_ref=owner_ref,
            status=memory_status,
        )
    return [MemoryView.model_validate(memory) for memory in memories]


@router.post("/memories/{memory_id}/approve", response_model=MemoryView)
async def approve_memory(
    memory_id: UUID,
    request: MemoryDecisionRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: MemoryService = Depends(get_memory_service),
) -> MemoryView:
    async with session.begin():
        memory = await service.decide(
            session, principal, memory_id, approve=True, reason=request.reason
        )
    return MemoryView.model_validate(memory)


@router.post("/memories/{memory_id}/reject", response_model=MemoryView)
async def reject_memory(
    memory_id: UUID,
    request: MemoryDecisionRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: MemoryService = Depends(get_memory_service),
) -> MemoryView:
    async with session.begin():
        memory = await service.decide(
            session, principal, memory_id, approve=False, reason=request.reason
        )
    return MemoryView.model_validate(memory)
