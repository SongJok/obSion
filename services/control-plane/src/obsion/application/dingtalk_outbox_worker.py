"""Background worker for the durable DingTalk robot Outbox."""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
from collections.abc import Callable

import structlog

from obsion.application.dingtalk_outbox import DingTalkOutboxService
from obsion.db.session import Database

logger = structlog.get_logger(__name__)


class DingTalkOutboxWorker:
    def __init__(
        self,
        database: Database,
        service: DingTalkOutboxService,
        *,
        poll_interval_seconds: float,
        on_round: Callable[[dict[str, int]], None] | None = None,
    ) -> None:
        self.database = database
        self.service = service
        self.poll_interval_seconds = poll_interval_seconds
        self.on_round = on_round
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}"
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="obsion-dingtalk-outbox")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
        self._task = None

    async def _loop(self) -> None:
        while not self._stop.is_set():
            stats = await self.run_once()
            if self.on_round is not None:
                try:
                    self.on_round(stats)
                except Exception:
                    logger.exception("dingtalk.outbox.observer_failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval_seconds)

    async def run_once(self) -> dict[str, int]:
        stats = {
            "queued": 0,
            "dispatched": 0,
            "reconciled": 0,
            "unknown": 0,
            "blocked": 0,
        }
        async with self.database.sessions() as session, session.begin():
            stats["unknown"] = await self.service.expire_claims(session)
        async with self.database.sessions() as session, session.begin():
            outbox = await self.service.enqueue_next(session)
            if outbox is not None:
                stats["queued"] += 1
        async with self.database.sessions() as session, session.begin():
            claim = await self.service.claim_next(session)
        if claim is not None:
            stats["dispatched"] = 1
            async with self.database.sessions() as session, session.begin():
                item = await self.service.dispatch(session, claim)
                if item is not None and str(item.status) == "BLOCKED":
                    stats["blocked"] = 1
        async with self.database.sessions() as session, session.begin():
            reconciliation_claim = await self.service.claim_reconciliation(session)
        if reconciliation_claim is not None:
            async with self.database.sessions() as session, session.begin():
                item = await self.service.reconcile(session, reconciliation_claim)
                if item is not None:
                    stats["reconciled"] = 1
        return stats
