import asyncio
import os
import socket
from datetime import timedelta
from uuid import UUID

import structlog
from sqlalchemy import or_, select

from obsion.common.time import ensure_utc, utc_now
from obsion.config import Settings
from obsion.db.models import Run
from obsion.db.session import Database
from obsion.domain.enums import ActorType, RunStatus
from obsion.domain.run_state import validate_run_transition
from obsion.harness.runtime import HarnessRuntime
from obsion.persistence.events import EventDraft, EventStore

logger = structlog.get_logger(__name__)


class RunWorker:
    def __init__(self, database: Database, settings: Settings, runtime: HarnessRuntime) -> None:
        self.database = database
        self.settings = settings
        self.runtime = runtime
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
        while not self._stop.is_set():
            claimed = await self._claim()
            if claimed is None:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=0.5)
                except TimeoutError:
                    continue
                continue
            organization_id, run_id = claimed
            await self._semaphore.acquire()
            task = asyncio.create_task(
                self._execute(organization_id, run_id), name=f"obsion-run-{run_id}"
            )
            self._active.add(task)
            task.add_done_callback(self._active.discard)

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
        except Exception:
            logger.exception("run.worker_failed", run_id=str(run_id))
        finally:
            self._semaphore.release()
