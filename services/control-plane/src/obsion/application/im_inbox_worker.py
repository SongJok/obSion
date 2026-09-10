"""Durable, tenant-scoped IM Inbox dispatcher.

The worker performs no vendor I/O. It only turns an already acknowledged,
administrator-mapped callback into a user-owned Harness Run.
"""

import asyncio
import os
import socket
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import structlog
from sqlalchemy import or_, select

from obsion.application.im_identity import ImIdentityService
from obsion.common.errors import ObsionError
from obsion.common.time import utc_now
from obsion.config import Settings
from obsion.db.models import ImInboxEvent
from obsion.db.session import Database
from obsion.domain.enums import ActorType, ImInboxStatus
from obsion.persistence.audit import AuditDraft

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class InboxClaim:
    event_id: UUID
    attempt: int


class ImInboxWorker:
    """Claims one Inbox record under a lease and dispatches it exactly once."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        service: ImIdentityService,
    ) -> None:
        self.database = database
        self.settings = settings
        self.service = service
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:im-inbox"
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="obsion-im-inbox-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                claimed = await self._claim()
            except Exception:
                logger.exception("im.inbox_claim_failed")
                await self._wait()
                continue
            if claimed is None:
                await self._wait()
                continue
            try:
                await self._dispatch(claimed)
            except Exception:
                # A failed rejection/release transaction must not kill the dispatcher.
                # The committed lease remains recoverable after the database returns.
                logger.exception("im.inbox_recovery_failed", inbox_event_id=str(claimed.event_id))
                await self._wait()

    async def _wait(self) -> None:
        try:
            await asyncio.wait_for(
                self._stop.wait(), timeout=self.settings.im_inbox_poll_interval_seconds
            )
        except TimeoutError:
            return

    async def _claim(self) -> InboxClaim | None:
        now = utc_now()
        async with self.database.sessions() as session, session.begin():
            event = await session.scalar(
                select(ImInboxEvent)
                .where(
                    ImInboxEvent.status.in_({ImInboxStatus.PENDING, ImInboxStatus.PROCESSING}),
                    or_(
                        ImInboxEvent.lease_expires_at.is_(None),
                        ImInboxEvent.lease_expires_at < now,
                    ),
                )
                .order_by(ImInboxEvent.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if event is None:
                return None
            event.status = ImInboxStatus.PROCESSING
            event.attempt_count += 1
            event.lease_owner = self.worker_id
            event.lease_expires_at = now + timedelta(seconds=self.settings.im_inbox_lease_seconds)
            return InboxClaim(event.id, event.attempt_count)

    async def _dispatch(self, claim: InboxClaim) -> None:
        if claim.attempt > self.settings.im_inbox_max_attempts:
            await self._reject(claim, "internal_error")
            return
        try:
            async with self.database.sessions() as session, session.begin():
                event = await session.scalar(
                    select(ImInboxEvent)
                    .where(
                        ImInboxEvent.id == claim.event_id,
                        ImInboxEvent.status == ImInboxStatus.PROCESSING,
                        ImInboxEvent.lease_owner == self.worker_id,
                        ImInboxEvent.attempt_count == claim.attempt,
                        ImInboxEvent.lease_expires_at > utc_now(),
                    )
                    .with_for_update()
                )
                if event is None:
                    return
                await self.service.dispatch_inbox_event(session, event)
        except ObsionError as exc:
            await self._reject(claim, exc.code)
        except Exception:
            logger.exception("im.inbox_dispatch_failed", inbox_event_id=str(claim.event_id))
            if claim.attempt >= self.settings.im_inbox_max_attempts:
                await self._reject(claim, "internal_error")
            else:
                await self._release(claim)
                await self._wait()

    async def _reject(self, claim: InboxClaim, code: str) -> None:
        async with self.database.sessions() as session, session.begin():
            event = await session.scalar(
                select(ImInboxEvent)
                .where(
                    ImInboxEvent.id == claim.event_id,
                    ImInboxEvent.lease_owner == self.worker_id,
                    ImInboxEvent.attempt_count == claim.attempt,
                    ImInboxEvent.lease_expires_at > utc_now(),
                )
                .with_for_update()
            )
            if event is None or event.status != ImInboxStatus.PROCESSING:
                return
            event.status = ImInboxStatus.REJECTED
            event.rejected_code = code
            event.lease_owner = None
            event.lease_expires_at = None
            event.processed_at = utc_now()
            await self.service.audit.write(
                session,
                AuditDraft(
                    organization_id=event.organization_id,
                    correlation_id=event.id,
                    actor_type=ActorType.SERVICE,
                    actor_id=event.accepted_by,
                    action="identity.im.inbox.reject",
                    resource_type="im_inbox_event",
                    resource_id=str(event.id),
                    outcome="DENIED",
                    metadata={"rejected_code": code, "attempt": claim.attempt},
                ),
            )

    async def _release(self, claim: InboxClaim) -> None:
        async with self.database.sessions() as session, session.begin():
            event = await session.scalar(
                select(ImInboxEvent)
                .where(
                    ImInboxEvent.id == claim.event_id,
                    ImInboxEvent.lease_owner == self.worker_id,
                    ImInboxEvent.attempt_count == claim.attempt,
                    ImInboxEvent.lease_expires_at > utc_now(),
                )
                .with_for_update()
            )
            if event is None or event.status != ImInboxStatus.PROCESSING:
                return
            event.status = ImInboxStatus.PENDING
            event.lease_owner = None
            event.lease_expires_at = None
