from typing import cast

import pytest

from obsion.automation.worker import AutomationWorker
from obsion.config import Environment, Settings
from obsion.db.session import Database


@pytest.mark.asyncio
async def test_execution_claim_failure_releases_capacity_and_keeps_loop_controlled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = AutomationWorker(
        cast(Database, object()),
        Settings(
            environment=Environment.TEST,
            automation_worker_concurrency=1,
            automation_poll_interval_seconds=0.1,
        ),
    )

    async def fail_claim() -> None:
        worker._stop.set()
        raise RuntimeError("transient database failure")

    monkeypatch.setattr(worker, "_claim_execution", fail_claim)
    await worker._execution_loop()

    await worker._semaphore.acquire()
    worker._semaphore.release()
