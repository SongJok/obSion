import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import CreateMemoryRequest
from obsion.common.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from obsion.common.time import ensure_utc, utc_now
from obsion.db.models import Memory, Turn
from obsion.domain.enums import ActorType, MemoryScope, MemoryStatus
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.persistence.events import EventDraft, EventStore
from obsion.security.identity import Principal
from obsion.security.redaction import redact
from obsion.security.workspace_access import require_thread_access, require_workspace_access


class MemoryService:
    def __init__(self) -> None:
        self.events = EventStore()
        self.audit = AuditWriter()

    async def create_candidate(
        self,
        session: AsyncSession,
        principal: Principal,
        request: CreateMemoryRequest,
    ) -> Memory:
        if not principal.can("memory.write"):
            raise AuthorizationError("memory_write_denied", "Memory creation is not permitted")
        await self._require_owner(session, principal, request.scope, request.owner_ref, write=True)
        if request.expires_at is not None and ensure_utc(request.expires_at) <= utc_now():
            raise ValidationError("memory_expiry_invalid", "Memory expiry must be in the future")
        content = redact(request.content)
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
        dedupe_key = hashlib.sha256(canonical.encode()).hexdigest()
        existing = await session.scalar(
            select(Memory).where(
                Memory.organization_id == principal.organization_id,
                Memory.scope == request.scope,
                Memory.owner_ref == request.owner_ref,
                Memory.dedupe_key == dedupe_key,
            )
        )
        if existing is not None:
            return existing
        memory = Memory(
            organization_id=principal.organization_id,
            scope=request.scope,
            owner_ref=request.owner_ref,
            content=content,
            dedupe_key=dedupe_key,
            sensitivity=request.sensitivity,
            status=MemoryStatus.CANDIDATE,
            expires_at=request.expires_at,
        )
        session.add(memory)
        await session.flush()
        await self._record(session, principal, memory, "memory.candidate", "CANDIDATE")
        return memory

    async def list(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        scope: MemoryScope | None,
        owner_ref: str | None,
        status: MemoryStatus | None,
    ) -> list[Memory]:
        if not principal.can("memory.read"):
            raise AuthorizationError("memory_read_denied", "Memory access is not permitted")
        statement = select(Memory).where(Memory.organization_id == principal.organization_id)
        if scope is not None:
            statement = statement.where(Memory.scope == scope)
        if owner_ref is not None:
            await self._require_owner(session, principal, scope, owner_ref)
            statement = statement.where(Memory.owner_ref == owner_ref)
        memories = list(
            await session.scalars(statement.order_by(Memory.updated_at.desc()).limit(500))
        )
        if owner_ref is None and not principal.can("memory.admin"):
            authorized: list[Memory] = []
            for memory in memories:
                try:
                    await self._require_owner(session, principal, memory.scope, memory.owner_ref)
                except (AuthorizationError, NotFoundError):
                    continue
                authorized.append(memory)
            memories = authorized
        now = utc_now()
        for memory in memories:
            if (
                memory.expires_at is not None
                and ensure_utc(memory.expires_at) <= now
                and memory.status in {MemoryStatus.CANDIDATE, MemoryStatus.APPROVED}
            ):
                memory.status = MemoryStatus.EXPIRED
                await self._record(session, principal, memory, "memory.expired", "EXPIRED")
        if status is not None:
            memories = [memory for memory in memories if memory.status == status]
        return memories

    async def decide(
        self,
        session: AsyncSession,
        principal: Principal,
        memory_id: UUID,
        *,
        approve: bool,
        reason: str,
    ) -> Memory:
        if not principal.can("memory.approve"):
            raise AuthorizationError("memory_approval_denied", "Memory approval is not permitted")
        memory = await session.scalar(
            select(Memory)
            .where(
                Memory.id == memory_id,
                Memory.organization_id == principal.organization_id,
            )
            .with_for_update()
        )
        if memory is None:
            raise NotFoundError("Memory", memory_id)
        await self._require_owner(session, principal, memory.scope, memory.owner_ref, write=True)
        if memory.status != MemoryStatus.CANDIDATE:
            raise ConflictError(
                "memory_already_decided",
                "The memory candidate is no longer pending",
                status=memory.status,
            )
        if memory.expires_at is not None and ensure_utc(memory.expires_at) <= utc_now():
            memory.status = MemoryStatus.EXPIRED
            await self._record(session, principal, memory, "memory.expired", "EXPIRED")
            return memory
        memory.status = MemoryStatus.APPROVED if approve else MemoryStatus.REJECTED
        await self._record(
            session,
            principal,
            memory,
            "memory.approved" if approve else "memory.rejected",
            memory.status,
            {"reason": reason.strip()},
        )
        return memory

    async def _require_owner(
        self,
        session: AsyncSession,
        principal: Principal,
        scope: MemoryScope | None,
        owner_ref: str,
        *,
        write: bool = False,
    ) -> None:
        if scope is None:
            if not principal.can("memory.admin"):
                raise ValidationError(
                    "memory_scope_required", "Scope is required when filtering by owner"
                )
            return
        try:
            owner_id = UUID(owner_ref)
        except ValueError as exc:
            raise ValidationError("memory_owner_invalid", "Memory owner must be a UUID") from exc
        if scope == MemoryScope.USER_PREFERENCE:
            if owner_id != principal.id and not principal.can("memory.admin"):
                raise AuthorizationError(
                    "memory_owner_denied", "Another user's memory is not accessible"
                )
            return
        if scope == MemoryScope.WORKSPACE:
            await require_workspace_access(session, principal, owner_id, write=write)
            return
        elif scope == MemoryScope.SESSION:
            await require_thread_access(session, principal, owner_id, write=write)
            return
        else:
            turn = await session.scalar(
                select(Turn).where(
                    Turn.id == owner_id,
                    Turn.organization_id == principal.organization_id,
                )
            )
            if turn is not None:
                await require_thread_access(session, principal, turn.thread_id, write=write)
                return
            raise NotFoundError(f"{scope.value.title()} memory owner", owner_id)

    async def _record(
        self,
        session: AsyncSession,
        principal: Principal,
        memory: Memory,
        name: str,
        outcome: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        await self.events.append(
            session,
            EventDraft(
                name=name,
                aggregate_type="memory",
                aggregate_id=memory.id,
                organization_id=principal.organization_id,
                correlation_id=memory.id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                payload={"memory_id": str(memory.id), "scope": memory.scope, **(payload or {})},
            ),
        )
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=memory.id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action=name,
                resource_type="memory",
                resource_id=str(memory.id),
                outcome=str(outcome),
                metadata={"scope": memory.scope, **(payload or {})},
            ),
        )
