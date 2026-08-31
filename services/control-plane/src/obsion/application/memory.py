import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import CreateMemoryRequest, UpdateMemoryRequest
from obsion.common.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from obsion.common.time import ensure_utc, utc_now
from obsion.config import Settings
from obsion.db.models import Memory, Run, RunMemorySnapshot, Thread, Turn
from obsion.domain.enums import (
    ActorType,
    Classification,
    DecisionEffect,
    MemoryScope,
    MemoryStatus,
    RiskLevel,
)
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.persistence.events import EventDraft, EventStore
from obsion.security.identity import Principal
from obsion.security.policy import Decision, PolicyEngine, ResourcePolicyInput
from obsion.security.redaction import redact
from obsion.security.workspace_access import require_thread_access, require_workspace_access
from obsion.telemetry import memory_context_counter

_CLASSIFICATION_ORDER = {
    Classification.PUBLIC: 0,
    Classification.INTERNAL: 1,
    Classification.CONFIDENTIAL: 2,
    Classification.RESTRICTED: 3,
}
_CLASSIFICATION_RISK = {
    Classification.PUBLIC: RiskLevel.L0,
    Classification.INTERNAL: RiskLevel.L1,
    Classification.CONFIDENTIAL: RiskLevel.L2,
    Classification.RESTRICTED: RiskLevel.L3,
}
_SCOPE_PRIORITY = {
    MemoryScope.TURN: 400,
    MemoryScope.SESSION: 300,
    MemoryScope.WORKSPACE: 200,
    MemoryScope.USER_PREFERENCE: 100,
}


@dataclass(frozen=True, slots=True)
class MemoryWriteResult:
    memory: Memory | None
    decision: Decision | None

    @property
    def denied(self) -> bool:
        return self.decision is not None and self.decision.effect == DecisionEffect.DENY


class MemoryService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.events = EventStore()
        self.audit = AuditWriter()
        self.policy = PolicyEngine()

    async def create_candidate(
        self,
        session: AsyncSession,
        principal: Principal,
        request: CreateMemoryRequest,
    ) -> MemoryWriteResult:
        if not principal.can("memory.write"):
            raise AuthorizationError("memory_write_denied", "Memory creation is not permitted")
        owner_classification = await self._require_owner(
            session, principal, request.scope, request.owner_ref, write=True
        )
        now = utc_now()
        expires_at = (
            ensure_utc(request.expires_at)
            if request.expires_at is not None
            else now + timedelta(days=self._default_ttl_days(request.scope))
        )
        if expires_at <= now:
            raise ValidationError("memory_expiry_invalid", "Memory expiry must be in the future")
        if expires_at > now + timedelta(days=self.settings.memory_max_ttl_days):
            raise ValidationError(
                "memory_expiry_exceeds_policy",
                "Memory expiry exceeds the configured retention boundary",
                max_ttl_days=self.settings.memory_max_ttl_days,
            )

        content = cast(dict[str, Any], redact(request.content))
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
        if len(canonical) > self.settings.memory_max_context_chars:
            raise ValidationError(
                "memory_content_too_large",
                "A memory item exceeds the governed context size limit",
                max_chars=self.settings.memory_max_context_chars,
            )
        dedupe_key = hashlib.sha256(canonical.encode()).hexdigest()
        sensitivity = self._highest_classification(request.sensitivity, owner_classification)
        existing = await session.scalar(
            select(Memory).where(
                Memory.organization_id == principal.organization_id,
                Memory.scope == request.scope,
                Memory.owner_ref == request.owner_ref,
                Memory.dedupe_key == dedupe_key,
            )
        )
        if existing is not None:
            if _CLASSIFICATION_ORDER[existing.sensitivity] < _CLASSIFICATION_ORDER[sensitivity]:
                raise ConflictError(
                    "memory_duplicate_classification_conflict",
                    "Matching memory exists at a lower sensitivity classification",
                    memory_id=str(existing.id),
                    existing_sensitivity=existing.sensitivity,
                    requested_sensitivity=sensitivity,
                )
            return MemoryWriteResult(existing, None)

        decision = await self.policy.evaluate_resource(
            session,
            ResourcePolicyInput(
                principal=principal,
                action="memory.write",
                resource={
                    "scope": request.scope,
                    "owner_ref": request.owner_ref,
                    "classification": sensitivity,
                    "content_fingerprint": dedupe_key,
                },
                context={
                    "environment": self.settings.environment,
                    "expires_at": expires_at.isoformat(),
                },
                risk_level=_CLASSIFICATION_RISK[sensitivity],
                resource_type="memory",
            ),
        )
        if decision.effect == DecisionEffect.DENY:
            await self.audit.write(
                session,
                AuditDraft(
                    organization_id=principal.organization_id,
                    correlation_id=decision.id,
                    actor_type=ActorType.USER,
                    actor_id=principal.id,
                    action="memory.write",
                    resource_type="memory",
                    resource_id=request.owner_ref,
                    outcome="DENIED",
                    risk_level=_CLASSIFICATION_RISK[sensitivity],
                    policy_decision_id=decision.id,
                    metadata={
                        "scope": request.scope,
                        "classification": sensitivity,
                        "reason_codes": decision.reason_codes,
                    },
                ),
            )
            return MemoryWriteResult(None, decision)

        memory = Memory(
            organization_id=principal.organization_id,
            scope=request.scope,
            owner_ref=request.owner_ref,
            content=content,
            dedupe_key=dedupe_key,
            sensitivity=sensitivity,
            status=MemoryStatus.CANDIDATE,
            policy_decision_id=decision.id,
            expires_at=expires_at,
        )
        session.add(memory)
        await session.flush()
        await self._record(
            session,
            principal,
            memory,
            "memory.candidate",
            "CANDIDATE",
            {
                "policy_effect": decision.effect,
                "policy_reason_codes": decision.reason_codes,
            },
            policy_decision_id=decision.id,
        )
        return MemoryWriteResult(memory, decision)

    async def list_memories(
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

    async def get_memory(
        self,
        session: AsyncSession,
        principal: Principal,
        memory_id: UUID,
    ) -> Memory:
        if not principal.can("memory.read"):
            raise AuthorizationError("memory_read_denied", "Memory access is not permitted")
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
        await self._require_owner(session, principal, memory.scope, memory.owner_ref)
        now = utc_now()
        if (
            memory.expires_at is not None
            and ensure_utc(memory.expires_at) <= now
            and memory.status in {MemoryStatus.CANDIDATE, MemoryStatus.APPROVED}
        ):
            memory.status = MemoryStatus.EXPIRED
            await self._record(session, principal, memory, "memory.expired", "EXPIRED")
        return memory

    async def update_memory(
        self,
        session: AsyncSession,
        principal: Principal,
        memory_id: UUID,
        request: UpdateMemoryRequest,
    ) -> MemoryWriteResult:
        if not principal.can("memory.write"):
            raise AuthorizationError("memory_write_denied", "Memory updates are not permitted")
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
        now = utc_now()
        if (
            memory.expires_at is not None
            and ensure_utc(memory.expires_at) <= now
            and memory.status in {MemoryStatus.CANDIDATE, MemoryStatus.APPROVED}
        ):
            memory.status = MemoryStatus.EXPIRED
            await self._record(session, principal, memory, "memory.expired", "EXPIRED")
        if memory.status not in {MemoryStatus.CANDIDATE, MemoryStatus.APPROVED}:
            raise ConflictError(
                "memory_already_decided",
                "This memory can no longer be edited",
                status=memory.status,
            )
        expires_at = (
            ensure_utc(request.expires_at)
            if request.expires_at is not None
            else (ensure_utc(memory.expires_at) if memory.expires_at is not None else None)
        )
        if expires_at is not None and expires_at <= now:
            raise ValidationError("memory_expiry_invalid", "Memory expiry must be in the future")
        if expires_at is not None and expires_at > now + timedelta(
            days=self.settings.memory_max_ttl_days
        ):
            raise ValidationError(
                "memory_expiry_exceeds_policy",
                "Memory expiry exceeds the configured retention boundary",
                max_ttl_days=self.settings.memory_max_ttl_days,
            )
        content = cast(dict[str, Any], redact(request.content))
        canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
        if len(canonical) > self.settings.memory_max_context_chars:
            raise ValidationError(
                "memory_content_too_large",
                "A memory item exceeds the governed context size limit",
                max_chars=self.settings.memory_max_context_chars,
            )
        dedupe_key = hashlib.sha256(canonical.encode()).hexdigest()
        sensitivity = self._highest_classification(
            request.sensitivity or memory.sensitivity,
            await self._require_owner(
                session, principal, memory.scope, memory.owner_ref, write=True
            ),
        )
        existing = await session.scalar(
            select(Memory).where(
                Memory.organization_id == principal.organization_id,
                Memory.scope == memory.scope,
                Memory.owner_ref == memory.owner_ref,
                Memory.dedupe_key == dedupe_key,
                Memory.id != memory.id,
            )
        )
        if existing is not None:
            raise ConflictError(
                "memory_duplicate_classification_conflict",
                "Matching memory exists for this owner and content",
                memory_id=str(existing.id),
                existing_sensitivity=existing.sensitivity,
                requested_sensitivity=sensitivity,
            )
        decision = await self.policy.evaluate_resource(
            session,
            ResourcePolicyInput(
                principal=principal,
                action="memory.write",
                resource={
                    "memory_id": str(memory.id),
                    "scope": memory.scope,
                    "owner_ref": memory.owner_ref,
                    "classification": sensitivity,
                    "content_fingerprint": dedupe_key,
                },
                context={
                    "environment": self.settings.environment,
                    "expires_at": expires_at.isoformat() if expires_at is not None else None,
                },
                risk_level=_CLASSIFICATION_RISK[sensitivity],
                resource_type="memory",
            ),
        )
        if decision.effect == DecisionEffect.DENY:
            await self.audit.write(
                session,
                AuditDraft(
                    organization_id=principal.organization_id,
                    correlation_id=memory.id,
                    actor_type=ActorType.USER,
                    actor_id=principal.id,
                    action="memory.write",
                    resource_type="memory",
                    resource_id=str(memory.id),
                    outcome="DENIED",
                    risk_level=_CLASSIFICATION_RISK[sensitivity],
                    policy_decision_id=decision.id,
                    metadata={
                        "scope": memory.scope,
                        "classification": sensitivity,
                        "reason_codes": decision.reason_codes,
                    },
                ),
            )
            return MemoryWriteResult(memory, decision)

        memory.content = content
        memory.dedupe_key = dedupe_key
        memory.sensitivity = sensitivity
        memory.expires_at = expires_at
        memory.status = MemoryStatus.CANDIDATE
        memory.policy_decision_id = decision.id
        await self._record(
            session,
            principal,
            memory,
            "memory.candidate",
            "CANDIDATE",
            {
                "policy_effect": decision.effect,
                "policy_reason_codes": decision.reason_codes,
            },
            policy_decision_id=decision.id,
        )
        return MemoryWriteResult(memory, decision)

    async def revoke_memory(
        self,
        session: AsyncSession,
        principal: Principal,
        memory_id: UUID,
    ) -> MemoryWriteResult:
        if not principal.can("memory.write"):
            raise AuthorizationError("memory_write_denied", "Memory deletion is not permitted")
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
        now = utc_now()
        if (
            memory.expires_at is not None
            and ensure_utc(memory.expires_at) <= now
            and memory.status in {MemoryStatus.CANDIDATE, MemoryStatus.APPROVED}
        ):
            memory.status = MemoryStatus.EXPIRED
            await self._record(session, principal, memory, "memory.expired", "EXPIRED")
        if memory.status not in {MemoryStatus.CANDIDATE, MemoryStatus.APPROVED}:
            raise ConflictError(
                "memory_already_decided",
                "This memory can no longer be revoked",
                status=memory.status,
            )
        decision = await self.policy.evaluate_resource(
            session,
            ResourcePolicyInput(
                principal=principal,
                action="memory.write",
                resource={
                    "memory_id": str(memory.id),
                    "scope": memory.scope,
                    "owner_ref": memory.owner_ref,
                    "classification": memory.sensitivity,
                },
                context={
                    "environment": self.settings.environment,
                    "decision": "REVOKE",
                },
                risk_level=_CLASSIFICATION_RISK[memory.sensitivity],
                resource_type="memory",
            ),
        )
        if decision.effect == DecisionEffect.DENY:
            await self.audit.write(
                session,
                AuditDraft(
                    organization_id=principal.organization_id,
                    correlation_id=memory.id,
                    actor_type=ActorType.USER,
                    actor_id=principal.id,
                    action="memory.write",
                    resource_type="memory",
                    resource_id=str(memory.id),
                    outcome="DENIED",
                    risk_level=_CLASSIFICATION_RISK[memory.sensitivity],
                    policy_decision_id=decision.id,
                    metadata={"scope": memory.scope, "reason_codes": decision.reason_codes},
                ),
            )
            return MemoryWriteResult(memory, decision)

        memory.status = MemoryStatus.REVOKED
        await self._record(
            session,
            principal,
            memory,
            "memory.revoked",
            "REVOKED",
            {"reason": "manual_delete"},
            policy_decision_id=decision.id,
        )
        return MemoryWriteResult(memory, decision)

    async def decide(
        self,
        session: AsyncSession,
        principal: Principal,
        memory_id: UUID,
        *,
        approve: bool,
        reason: str,
    ) -> MemoryWriteResult:
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
            return MemoryWriteResult(memory, None)

        decision = await self.policy.evaluate_resource(
            session,
            ResourcePolicyInput(
                principal=principal,
                action="memory.approve",
                resource={
                    "memory_id": str(memory.id),
                    "scope": memory.scope,
                    "owner_ref": memory.owner_ref,
                    "classification": memory.sensitivity,
                },
                context={
                    "environment": self.settings.environment,
                    "decision": "APPROVE" if approve else "REJECT",
                    "human_review": True,
                },
                risk_level=_CLASSIFICATION_RISK[memory.sensitivity],
                resource_type="memory",
            ),
        )
        if decision.effect == DecisionEffect.DENY:
            await self.audit.write(
                session,
                AuditDraft(
                    organization_id=principal.organization_id,
                    correlation_id=memory.id,
                    actor_type=ActorType.USER,
                    actor_id=principal.id,
                    action="memory.approve",
                    resource_type="memory",
                    resource_id=str(memory.id),
                    outcome="DENIED",
                    risk_level=_CLASSIFICATION_RISK[memory.sensitivity],
                    policy_decision_id=decision.id,
                    metadata={"reason_codes": decision.reason_codes},
                ),
            )
            return MemoryWriteResult(memory, decision)

        memory.status = MemoryStatus.APPROVED if approve else MemoryStatus.REJECTED
        await self._record(
            session,
            principal,
            memory,
            "memory.approved" if approve else "memory.rejected",
            memory.status,
            {"reason": reason.strip()},
            policy_decision_id=decision.id,
        )
        return MemoryWriteResult(memory, decision)

    async def capture_run_context(
        self,
        session: AsyncSession,
        principal: Principal,
        run: Run,
        turn: Turn,
        thread: Thread,
    ) -> list[RunMemorySnapshot]:
        """Pin authorized, approved memory before a Run crosses a model boundary."""
        existing = list(
            await session.scalars(
                select(RunMemorySnapshot)
                .where(
                    RunMemorySnapshot.organization_id == principal.organization_id,
                    RunMemorySnapshot.run_id == run.id,
                )
                .order_by(RunMemorySnapshot.ordinal)
            )
        )
        if existing or not principal.can("memory.read"):
            return existing

        owners = {
            MemoryScope.TURN: str(turn.id),
            MemoryScope.SESSION: str(thread.id),
            MemoryScope.WORKSPACE: str(thread.workspace_id),
            MemoryScope.USER_PREFERENCE: str(principal.id),
        }
        now = utc_now()
        owner_clauses = [
            and_(Memory.scope == scope, Memory.owner_ref == owner_ref)
            for scope, owner_ref in owners.items()
        ]
        candidates = list(
            await session.scalars(
                select(Memory).where(
                    Memory.organization_id == principal.organization_id,
                    Memory.status == MemoryStatus.APPROVED,
                    Memory.policy_decision_id.is_not(None),
                    or_(Memory.expires_at.is_(None), Memory.expires_at > now),
                    or_(*owner_clauses),
                )
            )
        )
        candidates.sort(
            key=lambda item: (
                -_SCOPE_PRIORITY[item.scope],
                -ensure_utc(item.updated_at).timestamp(),
                str(item.id),
            )
        )
        selected: list[Memory] = []
        fingerprints: set[str] = set()
        used_chars = 0
        for memory in candidates:
            if len(selected) >= self.settings.memory_max_context_items:
                break
            if memory.dedupe_key in fingerprints:
                continue
            size = len(json.dumps(memory.content, ensure_ascii=False, default=str))
            if used_chars + size > self.settings.memory_max_context_chars:
                continue
            selected.append(memory)
            fingerprints.add(memory.dedupe_key)
            used_chars += size

        snapshots = [
            RunMemorySnapshot(
                organization_id=principal.organization_id,
                run_id=run.id,
                memory_id=memory.id,
                principal_id=principal.id,
                ordinal=index,
                scope=memory.scope,
                owner_ref=memory.owner_ref,
                content=cast(dict[str, Any], redact(memory.content)),
                content_fingerprint=memory.dedupe_key,
                sensitivity=memory.sensitivity,
                policy_decision_id=cast(UUID, memory.policy_decision_id),
                memory_updated_at=memory.updated_at,
                captured_at=now,
            )
            for index, memory in enumerate(selected, start=1)
        ]
        session.add_all(snapshots)
        await session.flush()
        if snapshots:
            memory_context_counter.add(
                len(snapshots),
                {"operation": "captured", "highest_scope": snapshots[0].scope.value},
            )
        return snapshots

    async def _require_owner(
        self,
        session: AsyncSession,
        principal: Principal,
        scope: MemoryScope | None,
        owner_ref: str,
        *,
        write: bool = False,
    ) -> Classification:
        if scope is None:
            if not principal.can("memory.admin"):
                raise ValidationError(
                    "memory_scope_required", "Scope is required when filtering by owner"
                )
            return Classification.INTERNAL
        try:
            owner_id = UUID(owner_ref)
        except ValueError as exc:
            raise ValidationError("memory_owner_invalid", "Memory owner must be a UUID") from exc
        if scope == MemoryScope.USER_PREFERENCE:
            if owner_id != principal.id and not principal.can("memory.admin"):
                raise AuthorizationError(
                    "memory_owner_denied", "Another user's memory is not accessible"
                )
            return Classification.INTERNAL
        if scope == MemoryScope.WORKSPACE:
            workspace = await require_workspace_access(session, principal, owner_id, write=write)
            return workspace.classification
        if scope == MemoryScope.SESSION:
            thread = await require_thread_access(session, principal, owner_id, write=write)
            workspace = await require_workspace_access(
                session, principal, thread.workspace_id, write=write
            )
            return workspace.classification

        turn = await session.scalar(
            select(Turn).where(
                Turn.id == owner_id,
                Turn.organization_id == principal.organization_id,
            )
        )
        if turn is None:
            raise NotFoundError(f"{scope.value.title()} memory owner", owner_id)
        thread = await require_thread_access(session, principal, turn.thread_id, write=write)
        workspace = await require_workspace_access(
            session, principal, thread.workspace_id, write=write
        )
        return workspace.classification

    async def _record(
        self,
        session: AsyncSession,
        principal: Principal,
        memory: Memory,
        name: str,
        outcome: str,
        payload: dict[str, Any] | None = None,
        *,
        policy_decision_id: UUID | None = None,
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
                risk_level=_CLASSIFICATION_RISK[memory.sensitivity],
                policy_decision_id=policy_decision_id,
                metadata={"scope": memory.scope, **(payload or {})},
            ),
        )

    def _default_ttl_days(self, scope: MemoryScope) -> int:
        if scope == MemoryScope.TURN:
            return min(7, self.settings.memory_default_ttl_days)
        if scope == MemoryScope.SESSION:
            return min(30, self.settings.memory_default_ttl_days)
        return self.settings.memory_default_ttl_days

    @staticmethod
    def _highest_classification(
        requested: Classification,
        owner: Classification,
    ) -> Classification:
        return max((requested, owner), key=_CLASSIFICATION_ORDER.__getitem__)
