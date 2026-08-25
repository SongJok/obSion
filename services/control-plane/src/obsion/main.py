from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from obsion.actions.gateway import ActionGateway
from obsion.actions.worker import ActionWorker
from obsion.api import (
    actions,
    admin,
    approvals,
    artifacts,
    automation,
    capabilities,
    data,
    evaluations,
    events,
    health,
    knowledge,
    memory,
    run_inspection,
    workspaces,
)
from obsion.api.schemas import ErrorBody
from obsion.application.workspaces import WorkspaceService
from obsion.artifacts.store import InMemoryObjectStore, MinioObjectStore
from obsion.automation.worker import AutomationWorker
from obsion.bootstrap import bootstrap_development_identity
from obsion.capabilities.connectors import (
    HttpJsonExecutor,
    InternalExecutor,
    PostgresReadOnlyExecutor,
)
from obsion.capabilities.gateway import CapabilityGateway
from obsion.capabilities.rate_limit import (
    CapabilityRateLimiter,
    InMemoryFixedWindowRateLimiter,
    RedisFixedWindowRateLimiter,
)
from obsion.common.errors import ObsionError
from obsion.config import Environment, Settings, get_settings
from obsion.db.session import Database
from obsion.domain.enums import CapabilityTransport
from obsion.harness.runtime import HarnessRuntime
from obsion.harness.worker import RunWorker
from obsion.knowledge.handler import create_knowledge_search_handler
from obsion.model_gateway.gateway import ModelGateway
from obsion.telemetry import configure_telemetry, flush_telemetry, instrument_database

logger = structlog.get_logger(__name__)


def _configure_logging(settings: Settings) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ]
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    _configure_logging(resolved_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database = Database(resolved_settings)
        instrument_database(database)
        app.state.database = database
        app.state.settings = resolved_settings
        app.state.workspace_service = WorkspaceService(resolved_settings)
        app.state.object_store = (
            InMemoryObjectStore()
            if resolved_settings.environment == Environment.TEST
            else MinioObjectStore(resolved_settings)
        )
        async with database.sessions() as session, session.begin():
            await bootstrap_development_identity(session, resolved_settings)
        internal_executor = InternalExecutor()
        internal_executor.register(
            "knowledge-index",
            create_knowledge_search_handler(database, resolved_settings, app.state.object_store),
        )
        rate_limiter: CapabilityRateLimiter
        if resolved_settings.environment == Environment.TEST:
            rate_limiter = InMemoryFixedWindowRateLimiter(
                resolved_settings.capability_rate_limit_per_minute
            )
        else:
            rate_limiter = RedisFixedWindowRateLimiter(
                resolved_settings.redis_url,
                resolved_settings.capability_rate_limit_per_minute,
                fail_closed=resolved_settings.rate_limit_fail_closed,
            )
        capability_gateway = CapabilityGateway(
            {
                CapabilityTransport.INTERNAL.value: internal_executor,
                CapabilityTransport.HTTP.value: HttpJsonExecutor(resolved_settings),
                CapabilityTransport.SQL_PROXY.value: PostgresReadOnlyExecutor(resolved_settings),
            },
            rate_limiter=rate_limiter,
        )
        app.state.capability_gateway = capability_gateway
        action_gateway = ActionGateway(rate_limiter=rate_limiter)
        app.state.action_gateway = action_gateway
        runtime = HarnessRuntime(
            database,
            resolved_settings,
            capability_gateway,
            ModelGateway(resolved_settings),
            app.state.object_store,
        )
        worker = RunWorker(database, resolved_settings, runtime)
        app.state.run_worker = worker
        worker.start()
        automation_worker = AutomationWorker(database, resolved_settings)
        app.state.automation_worker = automation_worker
        automation_worker.start()
        action_worker = ActionWorker(database, resolved_settings, action_gateway)
        app.state.action_worker = action_worker
        action_worker.start()
        logger.info("obsion.started", environment=resolved_settings.environment)
        try:
            yield
        finally:
            await action_worker.stop()
            await automation_worker.stop()
            await worker.stop()
            await rate_limiter.aclose()
            await database.dispose()
            flush_telemetry()
            logger.info("obsion.stopped")

    app = FastAPI(
        title="Obsion API",
        summary="Enterprise Agent Runtime and Intelligence Workspace",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if resolved_settings.environment != "production" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID", "X-Request-ID"],
    )
    configure_telemetry(app, resolved_settings)

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        correlation_id = request.headers.get("X-Request-ID") or str(uuid4())
        request.state.correlation_id = correlation_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = correlation_id
        return response

    @app.exception_handler(ObsionError)
    async def obsion_error_handler(request: Request, exc: ObsionError) -> JSONResponse:
        correlation_id = getattr(request.state, "correlation_id", str(uuid4()))
        logger.info(
            "request.rejected",
            code=exc.code,
            correlation_id=correlation_id,
            path=request.url.path,
        )
        body = ErrorBody(
            code=exc.code,
            message=exc.message,
            correlation_id=correlation_id,
            details=exc.details,
        )
        return JSONResponse(status_code=exc.status_code, content=body.model_dump(mode="json"))

    app.include_router(health.router)
    app.include_router(workspaces.router, prefix="/api/v1")
    app.include_router(events.router, prefix="/api/v1")
    app.include_router(approvals.router, prefix="/api/v1")
    app.include_router(artifacts.router, prefix="/api/v1")
    app.include_router(automation.router, prefix="/api/v1")
    app.include_router(actions.router, prefix="/api/v1")
    app.include_router(capabilities.router, prefix="/api/v1")
    app.include_router(knowledge.router, prefix="/api/v1")
    app.include_router(memory.router, prefix="/api/v1")
    app.include_router(data.router, prefix="/api/v1")
    app.include_router(evaluations.router, prefix="/api/v1")
    app.include_router(run_inspection.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api/v1")
    return app
