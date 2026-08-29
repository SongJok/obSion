import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import structlog
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from obsion.actions.gateway import ActionGateway
from obsion.actions.worker import ActionWorker
from obsion.api import (
    actions,
    admin,
    approvals,
    artifacts,
    auth,
    automation,
    capabilities,
    collaboration,
    data,
    evaluations,
    events,
    feedback,
    health,
    knowledge,
    memory,
    run_inspection,
    workspaces,
)
from obsion.api.schemas import ErrorBody
from obsion.app_server import websocket as app_server
from obsion.application.app_server import AppServerApplication
from obsion.application.workspaces import WorkspaceService
from obsion.artifacts.service import ArtifactService
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
from obsion.security.auth import get_principal
from obsion.security.redaction import redact
from obsion.telemetry import configure_telemetry, flush_telemetry, instrument_database

logger = structlog.get_logger(__name__)

_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PRESERVED_ERROR_HEADERS = frozenset({"allow", "retry-after", "www-authenticate"})
_ERROR_RESPONSE_HEADERS: dict[str, dict[str, Any]] = {
    "X-Request-ID": {
        "description": "Stable correlation identifier for this request.",
        "schema": {"type": "string"},
    }
}


def _configure_logging(settings: Settings) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ]
    )


def _request_id_header(request: Request) -> str | None:
    value = request.headers.get("X-Request-ID")
    return value if value is not None and _REQUEST_ID.fullmatch(value) else None


def _correlation_id(request: Request) -> str:
    value = getattr(request.state, "correlation_id", None)
    if not isinstance(value, str) or not _REQUEST_ID.fullmatch(value):
        value = _request_id_header(request) or str(uuid4())
        request.state.correlation_id = value
    return value


def _safe_error_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    if headers is None:
        return {}
    return {key: value for key, value in headers.items() if key.lower() in _PRESERVED_ERROR_HEADERS}


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    details: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    correlation_id = _correlation_id(request)
    safe_details = redact(dict(details or {}))
    assert isinstance(safe_details, dict)
    body = ErrorBody(
        code=code,
        message=message,
        correlation_id=correlation_id,
        details=safe_details,
    )
    response = JSONResponse(
        status_code=status_code,
        content=body.model_dump(mode="json"),
        headers=_safe_error_headers(headers),
    )
    response.headers["X-Request-ID"] = correlation_id
    return response


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
        app_server_artifacts = ArtifactService(
            app.state.object_store,
            max_upload_bytes=resolved_settings.artifact_max_upload_bytes,
        )
        app.state.app_server_application = AppServerApplication(
            database,
            resolved_settings,
            app.state.workspace_service,
            app_server_artifacts,
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
        responses={
            404: {
                "model": ErrorBody,
                "description": "The requested resource was not found.",
                "headers": _ERROR_RESPONSE_HEADERS,
            },
            405: {
                "model": ErrorBody,
                "description": "The HTTP method is not allowed for the matched route.",
                "headers": {
                    **_ERROR_RESPONSE_HEADERS,
                    "Allow": {
                        "description": "HTTP methods supported by the matched route.",
                        "schema": {"type": "string"},
                    },
                },
            },
            422: {
                "model": ErrorBody,
                "description": "The request does not satisfy the API input contract.",
                "headers": _ERROR_RESPONSE_HEADERS,
            },
            500: {
                "model": ErrorBody,
                "description": "The server encountered an unexpected internal failure.",
                "headers": _ERROR_RESPONSE_HEADERS,
            },
        },
    )
    configure_telemetry(app, resolved_settings)

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        correlation_id = _correlation_id(request)
        try:
            response = await call_next(request)
        except Exception as exc:
            logger.error(
                "request.internal_error",
                correlation_id=correlation_id,
                exception_type=type(exc).__name__,
            )
            response = _error_response(
                request,
                status_code=500,
                code="internal_error",
                message="An internal error occurred",
            )
        response.headers["X-Request-ID"] = correlation_id
        return response

    @app.exception_handler(ObsionError)
    async def obsion_error_handler(request: Request, exc: ObsionError) -> JSONResponse:
        correlation_id = _correlation_id(request)
        logger.info(
            "request.rejected",
            code=exc.code,
            correlation_id=correlation_id,
        )
        return _error_response(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            details=exc.details,
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        del exc
        logger.info(
            "request.validation_failed",
            correlation_id=_correlation_id(request),
        )
        return _error_response(
            request,
            status_code=422,
            code="request_validation_failed",
            message="Request validation failed",
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        if exc.status_code == 404:
            code = "resource_not_found"
            message = "The requested resource was not found"
        elif exc.status_code == 405:
            code = "method_not_allowed"
            message = "The requested method is not allowed"
        else:
            code = "internal_error"
            message = "The request could not be completed"
            logger.warning(
                "request.unmodeled_http_error",
                status_code=exc.status_code,
                correlation_id=_correlation_id(request),
            )
        return _error_response(
            request,
            status_code=exc.status_code if exc.status_code in {404, 405} else 500,
            code=code,
            message=message,
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "request.internal_error",
            correlation_id=_correlation_id(request),
            exception_type=type(exc).__name__,
        )
        return _error_response(
            request,
            status_code=500,
            code="internal_error",
            message="An internal error occurred",
        )

    protected_api = APIRouter(prefix="/api/v1", dependencies=[Depends(get_principal)])
    protected_api.include_router(auth.router)
    protected_api.include_router(workspaces.router)
    protected_api.include_router(events.router)
    protected_api.include_router(feedback.router)
    protected_api.include_router(approvals.router)
    protected_api.include_router(artifacts.router)
    protected_api.include_router(automation.router)
    protected_api.include_router(actions.router)
    protected_api.include_router(capabilities.router)
    protected_api.include_router(collaboration.router)
    protected_api.include_router(knowledge.router)
    protected_api.include_router(memory.router)
    protected_api.include_router(data.router)
    protected_api.include_router(evaluations.router)
    protected_api.include_router(run_inspection.router)
    protected_api.include_router(admin.router)

    app.include_router(health.router)
    # Browser login/logout exchange bearer credentials for a revocable HttpOnly
    # session. Every resource route, including session inspection, remains behind
    # the shared Principal dependency below.
    app.include_router(auth.public_router, prefix="/api/v1")
    # The App Server authenticates its stateful connection during server.initialize.
    app.include_router(app_server.router, prefix="/api/v1")
    app.include_router(protected_api)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )
    return app
