"""无 Run Operator Capability 的持久化幂等与未知结果账本。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import ConflictError
from obsion.common.ids import new_id
from obsion.common.time import ensure_utc, utc_now
from obsion.db.models import OperatorCapabilityInvocation
from obsion.domain.enums import OperatorInvocationStatus
from obsion.security.identity import Principal


def operator_request_fingerprint(
    *,
    capability_name: str,
    payload: dict[str, Any],
    resource: dict[str, Any],
    environment: str,
    context: dict[str, Any],
) -> str:
    encoded = json.dumps(
        {
            "capability": capability_name,
            "payload": payload,
            "resource": resource,
            "environment": environment,
            "context": context,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class OperatorInvocationClaim:
    record: OperatorCapabilityInvocation
    state: Literal["NEW", "REPLAY", "IN_PROGRESS", "UNKNOWN"]
    replayed_result: dict[str, Any] | None = None


class OperatorInvocationStore:
    async def claim(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        request_id: UUID,
        capability_name: str,
        capability_version_id: UUID,
        connector_id: UUID,
        policy_decision_id: UUID,
        fingerprint: str,
        lease_seconds: int,
        retention_hours: int,
    ) -> OperatorInvocationClaim:
        now = utc_now()
        await session.execute(
            delete(OperatorCapabilityInvocation).where(
                OperatorCapabilityInvocation.organization_id == principal.organization_id,
                OperatorCapabilityInvocation.principal_id == principal.id,
                OperatorCapabilityInvocation.request_id == request_id,
                OperatorCapabilityInvocation.expires_at <= now,
                OperatorCapabilityInvocation.status.in_(
                    (
                        OperatorInvocationStatus.COMPLETED,
                        OperatorInvocationStatus.FAILED,
                        OperatorInvocationStatus.UNKNOWN,
                    )
                ),
            )
        )
        record = OperatorCapabilityInvocation(
            id=new_id(),
            organization_id=principal.organization_id,
            principal_id=principal.id,
            request_id=request_id,
            capability_name=capability_name,
            capability_version_id=capability_version_id,
            connector_id=connector_id,
            policy_decision_id=policy_decision_id,
            input_fingerprint=fingerprint,
            status=OperatorInvocationStatus.IN_PROGRESS,
            result=None,
            error_code=None,
            error_message=None,
            lease_expires_at=now + timedelta(seconds=max(1, lease_seconds)),
            created_at=now,
            completed_at=None,
            expires_at=now + timedelta(hours=max(1, retention_hours)),
        )
        inserted = False
        try:
            async with session.begin_nested():
                session.add(record)
                await session.flush()
            inserted = True
        except IntegrityError:
            pass
        if inserted:
            return OperatorInvocationClaim(record=record, state="NEW")

        existing = await session.scalar(
            select(OperatorCapabilityInvocation)
            .where(
                OperatorCapabilityInvocation.organization_id == principal.organization_id,
                OperatorCapabilityInvocation.principal_id == principal.id,
                OperatorCapabilityInvocation.request_id == request_id,
            )
            .with_for_update()
        )
        if existing is None:
            raise ConflictError(
                "idempotency_claim_lost",
                "The operator invocation idempotency claim could not be resolved",
            )
        if existing.capability_name != capability_name or existing.input_fingerprint != fingerprint:
            raise ConflictError(
                "idempotency_key_reused",
                "The request ID is already bound to another operator Capability input",
                original_capability=existing.capability_name,
            )
        if existing.status in {
            OperatorInvocationStatus.COMPLETED,
            OperatorInvocationStatus.FAILED,
        }:
            if existing.result is None:
                raise ConflictError(
                    "idempotency_claim_lost",
                    "The terminal operator invocation result is unavailable",
                )
            return OperatorInvocationClaim(
                record=existing,
                state="REPLAY",
                replayed_result=existing.result,
            )
        if existing.status == OperatorInvocationStatus.UNKNOWN:
            return OperatorInvocationClaim(record=existing, state="UNKNOWN")
        if ensure_utc(existing.lease_expires_at) <= now:
            existing.status = OperatorInvocationStatus.UNKNOWN
            existing.error_code = "operator_invocation_outcome_unknown"
            existing.error_message = (
                "The previous operator Capability attempt lost its completion boundary"
            )
            existing.completed_at = now
            await session.flush()
            return OperatorInvocationClaim(record=existing, state="UNKNOWN")
        return OperatorInvocationClaim(record=existing, state="IN_PROGRESS")

    async def complete(
        self,
        session: AsyncSession,
        invocation_id: UUID,
        *,
        result: dict[str, Any],
        succeeded: bool,
    ) -> OperatorCapabilityInvocation:
        record = await session.scalar(
            select(OperatorCapabilityInvocation)
            .where(OperatorCapabilityInvocation.id == invocation_id)
            .with_for_update()
        )
        if record is None:
            raise ConflictError(
                "idempotency_claim_lost",
                "The operator invocation idempotency claim could not be resolved",
            )
        if record.status != OperatorInvocationStatus.IN_PROGRESS:
            raise ConflictError(
                "idempotency_already_completed",
                "The operator invocation outcome is immutable",
            )
        record.status = (
            OperatorInvocationStatus.COMPLETED if succeeded else OperatorInvocationStatus.FAILED
        )
        record.result = result
        record.completed_at = utc_now()
        return record

    async def mark_unknown(
        self,
        session: AsyncSession,
        invocation_id: UUID,
    ) -> None:
        record = await session.scalar(
            select(OperatorCapabilityInvocation)
            .where(OperatorCapabilityInvocation.id == invocation_id)
            .with_for_update()
        )
        if record is None or record.status != OperatorInvocationStatus.IN_PROGRESS:
            return
        record.status = OperatorInvocationStatus.UNKNOWN
        record.error_code = "operator_invocation_outcome_unknown"
        record.error_message = "The operator Capability completion boundary was interrupted"
        record.completed_at = utc_now()
        await session.flush()
