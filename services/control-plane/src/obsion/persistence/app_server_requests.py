import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import ConflictError
from obsion.common.ids import new_id
from obsion.common.time import utc_now
from obsion.db.models import AppServerRequest
from obsion.security.identity import Principal


def params_fingerprint(params: dict[str, Any]) -> str:
    encoded = json.dumps(
        params,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    record: AppServerRequest
    replayed_response: dict[str, Any] | None


class AppServerRequestStore:
    async def claim(
        self,
        session: AsyncSession,
        principal: Principal,
        *,
        client_request_id: str,
        method: str,
        fingerprint: str,
        retention_hours: int,
    ) -> IdempotencyClaim:
        now = utc_now()
        # An expired key may be reused only after its protected retention window.
        # The database trigger permits this exact deletion and rejects early removal.
        await session.execute(
            delete(AppServerRequest).where(
                AppServerRequest.organization_id == principal.organization_id,
                AppServerRequest.principal_id == principal.id,
                AppServerRequest.client_request_id == client_request_id,
                AppServerRequest.expires_at <= now,
            )
        )
        record = AppServerRequest(
            id=new_id(),
            organization_id=principal.organization_id,
            principal_id=principal.id,
            client_request_id=client_request_id,
            method=method,
            params_fingerprint=fingerprint,
            response=None,
            created_at=now,
            completed_at=None,
            expires_at=now + timedelta(hours=retention_hours),
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
            return IdempotencyClaim(record=record, replayed_response=None)

        existing = await session.scalar(
            select(AppServerRequest)
            .where(
                AppServerRequest.organization_id == principal.organization_id,
                AppServerRequest.principal_id == principal.id,
                AppServerRequest.client_request_id == client_request_id,
            )
            .with_for_update()
        )
        if existing is None:
            raise ConflictError(
                "idempotency_claim_lost",
                "The idempotency claim could not be resolved",
            )
        if existing.method != method or existing.params_fingerprint != fingerprint:
            raise ConflictError(
                "idempotency_key_reused",
                "The client request ID is already bound to another operation",
                original_method=existing.method,
            )
        if existing.response is None:
            raise ConflictError(
                "idempotency_request_in_progress",
                "The original request is still in progress",
            )
        return IdempotencyClaim(record=existing, replayed_response=existing.response)

    async def complete(
        self,
        session: AsyncSession,
        record: AppServerRequest,
        response: dict[str, Any],
    ) -> None:
        if record.response is not None:
            raise ConflictError(
                "idempotency_already_completed",
                "The idempotency outcome is immutable",
            )
        record.response = response
        record.completed_at = utc_now()
        await session.flush()
