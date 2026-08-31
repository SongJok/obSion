from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import (
    CreateMemoryRequest,
    MemoryDecisionRequest,
    MemoryView,
    UpdateMemoryRequest,
)
from obsion.application.memory import MemoryService
from obsion.common.errors import AuthorizationError
from obsion.config import Settings
from obsion.domain.enums import MemoryScope, MemoryStatus
from obsion.security.auth import get_app_settings, get_principal, get_session
from obsion.security.identity import Principal

router = APIRouter(tags=["memory"])


def get_memory_service(settings: Settings = Depends(get_app_settings)) -> MemoryService:
    return MemoryService(settings)


@router.post("/memories", response_model=MemoryView, status_code=status.HTTP_201_CREATED)
async def create_memory_candidate(
    request: CreateMemoryRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: MemoryService = Depends(get_memory_service),
) -> MemoryView:
    async with session.begin():
        result = await service.create_candidate(session, principal, request)
    if result.denied or result.memory is None:
        raise AuthorizationError(
            "memory_policy_denied",
            "Policy denied persistence of this memory candidate",
            reason_codes=result.decision.reason_codes if result.decision else (),
        )
    return MemoryView.model_validate(result.memory)


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
        memories = await service.list_memories(
            session,
            principal,
            scope=scope,
            owner_ref=owner_ref,
            status=memory_status,
        )
    return [MemoryView.model_validate(memory) for memory in memories]


@router.get("/memories/{memory_id}", response_model=MemoryView)
async def get_memory(
    memory_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: MemoryService = Depends(get_memory_service),
) -> MemoryView:
    async with session.begin():
        memory = await service.get_memory(session, principal, memory_id)
    return MemoryView.model_validate(memory)


@router.patch("/memories/{memory_id}", response_model=MemoryView)
async def update_memory(
    memory_id: UUID,
    request: UpdateMemoryRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: MemoryService = Depends(get_memory_service),
) -> MemoryView:
    async with session.begin():
        result = await service.update_memory(session, principal, memory_id, request)
    if result.denied or result.memory is None:
        raise AuthorizationError(
            "memory_policy_denied",
            "Policy denied this memory update",
            reason_codes=result.decision.reason_codes if result.decision else (),
        )
    return MemoryView.model_validate(result.memory)


@router.delete("/memories/{memory_id}", response_model=MemoryView)
async def revoke_memory(
    memory_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: MemoryService = Depends(get_memory_service),
) -> MemoryView:
    async with session.begin():
        result = await service.revoke_memory(session, principal, memory_id)
    if result.denied or result.memory is None:
        raise AuthorizationError(
            "memory_policy_denied",
            "Policy denied revocation of this memory",
            reason_codes=result.decision.reason_codes if result.decision else (),
        )
    return MemoryView.model_validate(result.memory)


@router.post("/memories/{memory_id}/approve", response_model=MemoryView)
async def approve_memory(
    memory_id: UUID,
    request: MemoryDecisionRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: MemoryService = Depends(get_memory_service),
) -> MemoryView:
    async with session.begin():
        result = await service.decide(
            session, principal, memory_id, approve=True, reason=request.reason
        )
    if result.denied:
        raise AuthorizationError(
            "memory_policy_denied",
            "Policy denied this memory decision",
            reason_codes=result.decision.reason_codes if result.decision else (),
        )
    if result.memory is None:  # pragma: no cover - service invariant
        raise RuntimeError("memory decision completed without a memory")
    return MemoryView.model_validate(result.memory)


@router.post("/memories/{memory_id}/reject", response_model=MemoryView)
async def reject_memory(
    memory_id: UUID,
    request: MemoryDecisionRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: MemoryService = Depends(get_memory_service),
) -> MemoryView:
    async with session.begin():
        result = await service.decide(
            session, principal, memory_id, approve=False, reason=request.reason
        )
    if result.denied:
        raise AuthorizationError(
            "memory_policy_denied",
            "Policy denied this memory decision",
            reason_codes=result.decision.reason_codes if result.decision else (),
        )
    if result.memory is None:  # pragma: no cover - service invariant
        raise RuntimeError("memory decision completed without a memory")
    return MemoryView.model_validate(result.memory)
