"""Fenced, durable scheduling primitives for the single Python sync worker."""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import ValidationError
from obsion.common.time import utc_now
from obsion.db.models import KnowledgeSyncItem, KnowledgeSyncSource

WORK_LEASE_SECONDS = 120


async def claim_source(session: AsyncSession) -> KnowledgeSyncSource | None:
    """Caller owns the transaction. An expired claim resumes its persisted cursor."""
    now = utc_now()
    due = (
        KnowledgeSyncSource.active.is_(True),
        KnowledgeSyncSource.next_poll_at <= now,
        or_(
            KnowledgeSyncSource.lease_expires_at.is_(None),
            KnowledgeSyncSource.lease_expires_at <= now,
        ),
    )
    source = await session.scalar(
        select(KnowledgeSyncSource)
        .where(*due)
        .order_by(KnowledgeSyncSource.next_poll_at, KnowledgeSyncSource.id)
        .with_for_update(skip_locked=True)
        .limit(1)
        .execution_options(populate_existing=True)
    )
    if source is None:
        return None
    # The conditional UPDATE is also the fence on SQLite, where FOR UPDATE is
    # ignored. PostgreSQL additionally permits independent workers via SKIP LOCKED.
    claimed: KnowledgeSyncSource | None = await session.scalar(
        update(KnowledgeSyncSource)
        .where(KnowledgeSyncSource.id == source.id, *due)
        .values(
            lease_token=uuid4(),
            lease_expires_at=now + timedelta(seconds=WORK_LEASE_SECONDS),
            generation=source.generation + (0 if source.scan_state else 1),
        )
        .returning(KnowledgeSyncSource)
        .execution_options(populate_existing=True, synchronize_session=False)
    )
    return claimed


async def checkpoint_source(
    session: AsyncSession,
    *,
    source_id: UUID,
    lease_token: UUID,
    generation: int,
    scan_state: dict[str, Any],
    complete: bool = False,
) -> bool:
    """Commit one bounded unit; a stale worker cannot publish or delete anything.

    Completion is accepted only after the trusted discovery worker has exhausted
    its explicit queue and cursor. Body failures remain per-item states.
    """
    if complete and (
        scan_state.get("stage") != "COMPLETE"
        or scan_state.get("queue") != []
        or scan_state.get("cursor") is not None
        or scan_state.get("enumeration_complete") is not True
    ):
        raise ValidationError(
            "dingtalk_docs_operation_invalid", "A partial scan cannot mark missing documents"
        )
    now = utc_now()
    values: dict[str, Any] = {
        "scan_state": {} if complete else dict(scan_state),
        "lease_token": None,
        "lease_expires_at": None,
        "next_poll_at": now + timedelta(seconds=60) if complete else now,
    }
    if complete:
        values["last_success_at"] = now
    source = await session.scalar(
        update(KnowledgeSyncSource)
        .where(
            KnowledgeSyncSource.id == source_id,
            KnowledgeSyncSource.active.is_(True),
            KnowledgeSyncSource.lease_token == lease_token,
            KnowledgeSyncSource.generation == generation,
            KnowledgeSyncSource.lease_expires_at > now,
        )
        .values(**values)
        .returning(KnowledgeSyncSource)
        .execution_options(populate_existing=True, synchronize_session=False)
    )
    if source is None:
        return False
    if complete:
        await session.execute(
            update(KnowledgeSyncItem)
            .where(
                KnowledgeSyncItem.source_id == source.id,
                KnowledgeSyncItem.organization_id == source.organization_id,
                KnowledgeSyncItem.seen_generation != generation,
            )
            .values(status="MISSING", access_expires_at=None)
        )
    await session.flush()
    return True
