"""Explicit development host entrypoint; no API lifespan or automatic migrations.

Run with ``python -m obsion.knowledge.sync_host`` after registering capabilities,
Policy, connector and source. The normal API does not gain access to host DWS.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from obsion.artifacts.store import MinioObjectStore
from obsion.capabilities.dingtalk_managed import DingTalkManagedSdkExecutor, HostDwsReadRunner
from obsion.capabilities.gateway import CapabilityGateway
from obsion.config import Environment, Settings
from obsion.db.session import Database
from obsion.knowledge.service import KnowledgeService
from obsion.knowledge.sync import KnowledgeSyncService
from obsion.knowledge.sync_worker import KnowledgeSyncWorker

logger = logging.getLogger(__name__)


async def run(*, once: bool = False) -> None:
    settings = Settings()
    if settings.environment != Environment.DEVELOPMENT:
        raise ValueError("The host-managed source worker requires development mode")
    if not settings.database_url.startswith("postgresql+asyncpg://"):
        raise ValueError("The source worker requires PostgreSQL")
    if settings.object_store_backend == "memory":
        raise ValueError("The source worker requires durable object storage")
    database = Database(settings)
    gateway = CapabilityGateway({"SDK": DingTalkManagedSdkExecutor(HostDwsReadRunner())})
    worker = KnowledgeSyncWorker(
        database,
        gateway,
        KnowledgeSyncService(KnowledgeService(settings, MinioObjectStore(settings))),
    )
    try:
        while True:
            try:
                worked = await worker.tick()
            except Exception as exc:
                # Unexpected failures must not dump upstream bodies, secrets, SQL
                # parameter values or cursors. The durable lease expires for retry.
                logger.error("Source worker interrupted: %s", type(exc).__name__)
                if once:
                    raise RuntimeError("Source worker interrupted; inspect source status") from None
                await asyncio.sleep(5)
                continue
            if once:
                return
            await asyncio.sleep(0 if worked else 2)
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the governed DingTalk source worker")
    parser.add_argument("--once", action="store_true", help="Process at most one bounded unit")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(once=arguments.once))


if __name__ == "__main__":
    main()
