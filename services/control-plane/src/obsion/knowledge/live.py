"""Demand fresh upstream acquisition from the existing durable source worker.

No provider calls, credentials, private body cache or second execution loop live
here. Scheduling only advances an idle source's poll time; it never resets an
active scan, steals a lease, changes permissions or accepts external results.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Any
from uuid import UUID

from sqlalchemy import and_, exists, false, func, or_, select, update
from sqlalchemy.sql.elements import ColumnElement

from obsion.common.time import ensure_utc, utc_now
from obsion.db.models import Document, KnowledgeSyncItem, KnowledgeSyncSource
from obsion.db.session import Database
from obsion.security.auth import load_principal_by_id
from obsion.security.identity import Principal


class LiveKnowledgeUnavailable(ValueError):
    """Only fixed machine reasons are passed to the user-facing projection."""


@dataclass(frozen=True)
class SourceScanReceipt:
    source_id: UUID
    generation: int
    started_at: datetime
    completed_at: datetime


@dataclass(frozen=True)
class LiveKnowledgeProof:
    requested_at: datetime
    sources: tuple[SourceScanReceipt, ...]
    incomplete_documents: int

    def clause(self, principal: Principal) -> ColumnElement[bool]:
        if not self.sources:
            return false()
        return exists(
            select(KnowledgeSyncItem.id).where(
                KnowledgeSyncItem.organization_id == principal.organization_id,
                KnowledgeSyncItem.document_id == Document.id,
                KnowledgeSyncItem.status == "READY",
                KnowledgeSyncItem.checked_at >= self.requested_at,
                or_(
                    *[
                        and_(
                            KnowledgeSyncItem.source_id == source.source_id,
                            KnowledgeSyncItem.read_generation >= source.generation,
                        )
                        for source in self.sources
                    ]
                ),
            )
        ).correlate(Document)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": "READY",
            "scope": "freshly_acquired_managed_sources",
            "requested_at": self.requested_at.isoformat(),
            "incomplete_documents": self.incomplete_documents,
            "sources": [
                {
                    "source_id": str(source.source_id),
                    "generation": source.generation,
                    "scan_started_at": source.started_at.isoformat(),
                    "scan_completed_at": source.completed_at.isoformat(),
                }
                for source in self.sources
            ],
        }


async def await_live_sources(
    database: Database,
    principal: Principal,
    *,
    requested_at: datetime,
    wait_seconds: float,
) -> LiveKnowledgeProof:
    requested_at = ensure_utc(requested_at)
    deadline = monotonic() + wait_seconds
    last_failure = False
    source_ids: set[UUID] | None = None
    while True:
        async with database.sessions() as session, session.begin():
            current = await load_principal_by_id(session, principal.organization_id, principal.id)
            if not current.can("knowledge.read"):
                raise LiveKnowledgeUnavailable("source_access_unavailable")
            sources = list(
                await session.scalars(
                    select(KnowledgeSyncSource)
                    .where(
                        KnowledgeSyncSource.organization_id == current.organization_id,
                        KnowledgeSyncSource.user_id == current.id,
                        KnowledgeSyncSource.active.is_(True),
                    )
                    .order_by(KnowledgeSyncSource.id)
                    .limit(17)
                )
            )
            if not sources:
                raise LiveKnowledgeUnavailable("source_not_configured")
            if len(sources) > 16:
                raise LiveKnowledgeUnavailable("source_scope_exceeds_budget")
            observed_ids = {source.id for source in sources}
            if source_ids is None:
                source_ids = observed_ids
            elif source_ids != observed_ids:
                raise LiveKnowledgeUnavailable("source_scope_changed")
            receipts = tuple(
                SourceScanReceipt(
                    source.id,
                    source.completed_generation,
                    ensure_utc(source.completed_scan_started_at),
                    ensure_utc(source.last_success_at),
                )
                for source in sources
                if source.completed_scan_started_at is not None
                and source.last_success_at is not None
                and source.completed_generation is not None
                and 0 < source.completed_generation <= source.generation
                and requested_at
                <= ensure_utc(source.completed_scan_started_at)
                <= ensure_utc(source.last_success_at)
                <= utc_now()
            )
            if len(receipts) == len(sources):
                incomplete = await session.scalar(
                    select(func.count())
                    .select_from(KnowledgeSyncItem)
                    .where(
                        KnowledgeSyncItem.organization_id == current.organization_id,
                        KnowledgeSyncItem.source_id.in_([source.id for source in sources]),
                        KnowledgeSyncItem.kind.notin_({"folder", "directory"}),
                        KnowledgeSyncItem.status.notin_({"READY", "MISSING"}),
                    )
                )
                if incomplete:
                    # A directory scan can finish even when every body read was
                    # denied or partial. Only a current, leased body can make
                    # partial acquisition usable; old READY rows cannot do so.
                    readable = await session.scalar(
                        select(KnowledgeSyncItem.id)
                        .where(
                            KnowledgeSyncItem.organization_id == current.organization_id,
                            KnowledgeSyncItem.status == "READY",
                            KnowledgeSyncItem.document_id.is_not(None),
                            KnowledgeSyncItem.kind.notin_({"folder", "directory"}),
                            KnowledgeSyncItem.checked_at >= requested_at,
                            KnowledgeSyncItem.access_expires_at > utc_now(),
                            or_(
                                *[
                                    and_(
                                        KnowledgeSyncItem.source_id == receipt.source_id,
                                        KnowledgeSyncItem.read_generation >= receipt.generation,
                                    )
                                    for receipt in receipts
                                ]
                            ),
                        )
                        .limit(1)
                    )
                    if readable is None:
                        raise LiveKnowledgeUnavailable("source_content_unavailable")
                return LiveKnowledgeProof(requested_at, receipts, incomplete or 0)
            last_failure = any(source.last_error_code is not None for source in sources)
            if monotonic() >= deadline:
                break
            ready = {receipt.source_id for receipt in receipts}
            now = utc_now()
            await session.execute(
                update(KnowledgeSyncSource)
                .where(
                    KnowledgeSyncSource.organization_id == current.organization_id,
                    KnowledgeSyncSource.user_id == current.id,
                    KnowledgeSyncSource.id.in_([s.id for s in sources if s.id not in ready]),
                    KnowledgeSyncSource.active.is_(True),
                    KnowledgeSyncSource.next_poll_at > now,
                    or_(
                        KnowledgeSyncSource.lease_expires_at.is_(None),
                        KnowledgeSyncSource.lease_expires_at <= now,
                    ),
                )
                .values(next_poll_at=now)
                .execution_options(synchronize_session=False)
            )
        await asyncio.sleep(min(0.5, max(0, deadline - monotonic())))
    raise LiveKnowledgeUnavailable(
        "source_refresh_failed" if last_failure else "source_refresh_timeout"
    )
