import asyncio
import os
import socket
from datetime import timedelta
from time import monotonic
from uuid import UUID

import structlog
from sqlalchemy import or_, select

from obsion.application.im_delivery import ImDeliveryService
from obsion.common.errors import AuthorizationError, ConflictError, NotFoundError
from obsion.common.time import ensure_utc, utc_now
from obsion.config import Settings
from obsion.db.models import AuditRecord, ImDelivery, ImInboxEvent, Run
from obsion.db.session import Database
from obsion.domain.enums import ActorType, ImInboxStatus, RunStatus
from obsion.domain.run_state import validate_run_transition
from obsion.harness.runtime import HarnessRuntime
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.persistence.events import EventDraft, EventStore
from obsion.security.auth import load_principal_by_id

logger = structlog.get_logger(__name__)


_AUTO_DELIVERY_REJECT = "experience.im.delivery.auto_prepare.reject"


class RunWorker:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        runtime: HarnessRuntime,
        im_delivery_service: ImDeliveryService | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.runtime = runtime
        self.im_delivery_service = im_delivery_service
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._semaphore = asyncio.Semaphore(settings.run_worker_concurrency)
        self._active: set[asyncio.Task[None]] = set()
        self.events = EventStore()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="obsion-run-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
        if self._active:
            await asyncio.gather(*self._active, return_exceptions=True)

    async def _loop(self) -> None:
        next_recovery = 0.0
        while not self._stop.is_set():
            try:
                if monotonic() >= next_recovery:
                    await self._reconcile_pending_im_deliveries()
                    next_recovery = monotonic() + 5.0
                claimed = await self._claim()
            except Exception:
                logger.exception("run.claim_failed")
                await self._wait()
                continue
            if claimed is None:
                await self._wait()
                continue
            organization_id, run_id = claimed
            await self._semaphore.acquire()
            task = asyncio.create_task(
                self._execute(organization_id, run_id), name=f"obsion-run-{run_id}"
            )
            self._active.add(task)
            task.add_done_callback(self._active.discard)

    async def _wait(self) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=0.5)
        except TimeoutError:
            return

    async def _claim(self) -> tuple[UUID, UUID] | None:
        now = utc_now()
        async with self.database.sessions() as session, session.begin():
            run = await session.scalar(
                select(Run)
                .where(
                    Run.status.in_({RunStatus.PENDING, RunStatus.RUNNING}),
                    Run.cancellation_requested_at.is_(None),
                    or_(Run.lease_expires_at.is_(None), Run.lease_expires_at < now),
                )
                .order_by(Run.created_at)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if run is None:
                return None
            if run.deadline_at is not None and ensure_utc(run.deadline_at) <= now:
                validate_run_transition(run.status, RunStatus.FAILED)
                run.status = RunStatus.FAILED
                run.error_code = "run_timeout"
                run.error_message = "The run deadline expired before execution completed"
                run.completed_at = now
                await self.events.append(
                    session,
                    EventDraft(
                        name="run.failed",
                        aggregate_type="run",
                        aggregate_id=run.id,
                        organization_id=run.organization_id,
                        correlation_id=run.id,
                        actor_type=ActorType.SYSTEM,
                        actor_id=None,
                        run_id=run.id,
                        payload={"error_code": run.error_code},
                    ),
                )
                return None
            event_name = "run.started" if run.status == RunStatus.PENDING else "run.resumed"
            if run.status == RunStatus.PENDING:
                validate_run_transition(run.status, RunStatus.RUNNING)
                run.status = RunStatus.RUNNING
                run.started_at = now
            run.lease_owner = self.worker_id
            run.lease_expires_at = now + timedelta(seconds=run.timeout_seconds + 30)
            await self.events.append(
                session,
                EventDraft(
                    name=event_name,
                    aggregate_type="run",
                    aggregate_id=run.id,
                    organization_id=run.organization_id,
                    correlation_id=run.id,
                    actor_type=ActorType.SYSTEM,
                    actor_id=None,
                    run_id=run.id,
                    payload={"worker": self.worker_id},
                ),
            )
            return run.organization_id, run.id

    async def _execute(self, organization_id: UUID, run_id: UUID) -> None:
        try:
            await self.runtime.execute(organization_id, run_id)
            await self._schedule_im_delivery(organization_id, run_id)
        except Exception:
            logger.exception("run.worker_failed", run_id=str(run_id))
        finally:
            self._semaphore.release()

    async def _schedule_im_delivery(self, organization_id: UUID, run_id: UUID) -> None:
        """Materialize a pending IM delivery after the completed Run commits."""
        if self.im_delivery_service is None:
            return
        async with self.database.sessions() as session, session.begin():
            row = (
                await session.execute(
                    select(Run, ImInboxEvent)
                    .join(ImInboxEvent, ImInboxEvent.run_id == Run.id)
                    .where(
                        Run.id == run_id,
                        Run.organization_id == organization_id,
                        ImInboxEvent.organization_id == organization_id,
                        ImInboxEvent.status == ImInboxStatus.ACCEPTED,
                    )
                    .with_for_update(of=Run)
                )
            ).one_or_none()
            if row is None:
                return
            run, event = row._tuple()
            if run.status != RunStatus.COMPLETED:
                return
            if await session.scalar(select(ImDelivery.id).where(ImDelivery.run_id == run.id)):
                return
            if await session.scalar(
                select(AuditRecord.id).where(
                    AuditRecord.organization_id == organization_id,
                    AuditRecord.correlation_id == run.id,
                    AuditRecord.action == _AUTO_DELIVERY_REJECT,
                )
            ):
                return
            try:
                principal = await load_principal_by_id(session, organization_id, event.accepted_by)
                await self.im_delivery_service.prepare(session, principal, run.id)
            except (AuthorizationError, ConflictError, NotFoundError) as exc:
                # Commit authorization evidence instead of rolling it back with the denial.
                await AuditWriter().write(
                    session,
                    AuditDraft(
                        organization_id=organization_id,
                        correlation_id=run.id,
                        actor_type=ActorType.SERVICE,
                        actor_id=event.accepted_by,
                        action=_AUTO_DELIVERY_REJECT,
                        resource_type="run",
                        resource_id=str(run.id),
                        outcome="DENIED",
                        metadata={"reason_code": exc.code},
                    ),
                )

    async def _reconcile_pending_im_deliveries(self) -> None:
        """Recover a completed IM Run if the process stopped after Run commit."""
        if self.im_delivery_service is None:
            return
        async with self.database.sessions() as session:
            candidates = list(
                (
                    await session.execute(
                        select(Run.organization_id, Run.id)
                        .join(ImInboxEvent, ImInboxEvent.run_id == Run.id)
                        .where(
                            Run.status == RunStatus.COMPLETED,
                            Run.organization_id == ImInboxEvent.organization_id,
                            ImInboxEvent.status == ImInboxStatus.ACCEPTED,
                            ~select(ImDelivery.id)
                            .where(
                                ImDelivery.organization_id == Run.organization_id,
                                ImDelivery.run_id == Run.id,
                            )
                            .exists(),
                            ~select(AuditRecord.id)
                            .where(
                                AuditRecord.organization_id == Run.organization_id,
                                AuditRecord.correlation_id == Run.id,
                                AuditRecord.action == _AUTO_DELIVERY_REJECT,
                            )
                            .exists(),
                        )
                        .order_by(Run.completed_at, Run.id)
                        .limit(25)
                    )
                ).all()
            )
        for organization_id, run_id in candidates:
            try:
                await self._schedule_im_delivery(organization_id, run_id)
            except Exception:
                logger.exception("run.im_delivery_recovery_failed", run_id=str(run_id))
