import asyncio
from contextlib import suppress
from dataclasses import dataclass
from time import monotonic
from typing import Any, cast
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError as PydanticValidationError

from obsion.app_server.dispatcher import AppServerDispatcher
from obsion.app_server.protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    PROTOCOL_VERSION,
    WEBSOCKET_SUBPROTOCOL,
    JsonRpcRequest,
    ProtocolFailure,
    error_response,
    notification,
    parse_request,
    success_response,
)
from obsion.app_server.schemas import (
    EmptyParams,
    InitializeParams,
    RunSubscribeParams,
    RunUnsubscribeParams,
)
from obsion.application.app_server import AppServerApplication
from obsion.common.error_mapping import application_error_code
from obsion.common.errors import ConflictError, NotFoundError, ObsionError
from obsion.common.time import utc_now
from obsion.config import Settings
from obsion.domain.run_state import is_terminal
from obsion.security.identity import Principal
from obsion.telemetry import (
    app_server_connection_counter,
    app_server_event_counter,
    app_server_request_counter,
)

router = APIRouter(tags=["app-server"])
logger = structlog.get_logger(__name__)

APP_SERVER_METHODS = [
    "server.initialize",
    "server.ping",
    "workspace.list",
    "thread.list",
    "thread.create",
    "thread.archive",
    "thread.resume",
    "thread.fork",
    "thread.turns",
    "thread.runs",
    "thread.events",
    "turn.create",
    "run.get",
    "run.cancel",
    "run.replay",
    "run.events",
    "run.subscribe",
    "run.unsubscribe",
    "approval.list",
    "approval.decide",
    "artifact.list",
    "artifact.get",
]


@dataclass(slots=True)
class RunSubscription:
    id: str
    run_id: UUID
    cursor: int


class AppServerConnection:
    def __init__(
        self,
        websocket: WebSocket,
        settings: Settings,
        application: AppServerApplication,
    ) -> None:
        self.websocket = websocket
        self.settings = settings
        self.application = application
        self.dispatcher = AppServerDispatcher(application)
        self.send_lock = asyncio.Lock()
        self.subscriptions: dict[str, RunSubscription] = {}
        self.subscription_wakeup = asyncio.Event()
        self.principal: Principal | None = None
        self.poller: asyncio.Task[None] | None = None

    async def run(self) -> None:
        app_server_connection_counter.add(1, {"state": "accepted"})
        await self._send(
            notification(
                "server.ready",
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "subprotocol": WEBSOCKET_SUBPROTOCOL,
                    "initialize_timeout_seconds": (
                        self.settings.app_server_initialize_timeout_seconds
                    ),
                },
            )
        )
        try:
            await self._initialize()
            self.poller = asyncio.create_task(
                self._poll_subscriptions(),
                name="obsion-app-server-subscriptions",
            )
            while True:
                raw = await self._receive_text()
                await self._process(raw)
        except WebSocketDisconnect:
            pass
        finally:
            if self.poller is not None:
                self.poller.cancel()
                with suppress(asyncio.CancelledError):
                    await self.poller
            self.subscriptions.clear()
            app_server_connection_counter.add(1, {"state": "closed"})

    async def _initialize(self) -> None:
        try:
            raw = await asyncio.wait_for(
                self._receive_text(),
                timeout=self.settings.app_server_initialize_timeout_seconds,
            )
            request = parse_request(raw)
        except TimeoutError:
            await self.websocket.close(code=1008, reason="initialization timeout")
            raise WebSocketDisconnect(code=1008) from None
        except ProtocolFailure as exc:
            await self._send(error_response(exc.request_id, exc.code, exc.message, exc.data))
            await self.websocket.close(code=1008, reason="invalid initialization")
            raise WebSocketDisconnect(code=1008) from exc

        if not request.has_id or request.method != "server.initialize":
            await self._send(
                error_response(
                    request.request_id,
                    INVALID_REQUEST,
                    "The first request must be server.initialize with an id",
                )
            )
            await self.websocket.close(code=1008, reason="initialization required")
            raise WebSocketDisconnect(code=1008)
        assert request.request_id is not None
        try:
            params = InitializeParams.model_validate(request.params)
            if params.protocol_version != PROTOCOL_VERSION:
                raise ProtocolFailure(
                    INVALID_PARAMS,
                    "Unsupported protocol version",
                    request.request_id,
                    {"supported": [PROTOCOL_VERSION]},
                )
            header_token = self._header_bearer_token()
            if header_token and params.bearer_token and header_token != params.bearer_token:
                raise ProtocolFailure(
                    INVALID_PARAMS,
                    "Conflicting bearer credentials",
                    request.request_id,
                )
            bearer_token = header_token or params.bearer_token
            if bearer_token is not None:
                principal = await self.application.authenticate(bearer_token)
            else:
                principal = await self.application.authenticate_session(self._session_cookie())
        except PydanticValidationError as exc:
            await self._send(
                error_response(
                    request.request_id,
                    INVALID_PARAMS,
                    "Invalid initialization params",
                    {"issues": exc.errors(include_input=False, include_url=False)},
                )
            )
            await self.websocket.close(code=1008, reason="invalid initialization")
            raise WebSocketDisconnect(code=1008) from exc
        except ProtocolFailure as exc:
            await self._send(error_response(request.request_id, exc.code, exc.message, exc.data))
            await self.websocket.close(code=1008, reason="incompatible protocol")
            raise WebSocketDisconnect(code=1008) from exc
        except ObsionError as exc:
            correlation_id = uuid4()
            await self._send(self._domain_error_response(request.request_id, exc, correlation_id))
            await self.websocket.close(code=1008, reason="authentication failed")
            raise WebSocketDisconnect(code=1008) from exc

        self.principal = principal
        app_server_connection_counter.add(1, {"state": "initialized"})
        await self._send(
            success_response(
                request.request_id,
                {
                    "protocol_version": PROTOCOL_VERSION,
                    "client": {
                        "name": params.client_name,
                        "version": params.client_version,
                    },
                    "principal": {
                        "id": str(principal.id),
                        "organization_id": str(principal.organization_id),
                        "display_name": principal.display_name,
                        "roles": sorted(principal.roles),
                    },
                    "methods": APP_SERVER_METHODS,
                    "limits": {
                        "max_message_bytes": self.settings.app_server_max_message_bytes,
                        "max_subscriptions": self.settings.app_server_max_subscriptions,
                        "idempotency_retention_hours": (
                            self.settings.app_server_idempotency_retention_hours
                        ),
                    },
                },
            )
        )
        self._record_request("server.initialize", "success")

    async def _process(self, raw: str) -> None:
        try:
            request = parse_request(raw)
        except ProtocolFailure as exc:
            await self._send(error_response(exc.request_id, exc.code, exc.message, exc.data))
            return

        if not request.has_id:
            if request.method == "server.ping":
                await self._send(
                    notification("server.pong", {"server_time": utc_now().isoformat()})
                )
            else:
                await self._send(
                    notification(
                        "server.warning",
                        {
                            "code": "client_notification_ignored",
                            "method": request.method,
                        },
                    )
                )
            return

        assert request.request_id is not None
        correlation_id = uuid4()
        try:
            if request.method == "server.initialize":
                raise ConflictError(
                    "connection_already_initialized",
                    "The connection principal and protocol are already initialized",
                )
            if request.method == "server.ping":
                EmptyParams.model_validate(request.params)
                await self._send(
                    success_response(
                        request.request_id,
                        {"server_time": utc_now().isoformat()},
                    )
                )
                self._record_request(request.method, "success")
                return
            if request.method == "run.subscribe":
                await self._subscribe(request)
                self._record_request(request.method, "success")
                return
            if request.method == "run.unsubscribe":
                await self._unsubscribe(request)
                self._record_request(request.method, "success")
                return
            assert self.principal is not None
            response = await self.dispatcher.dispatch(
                request,
                self.principal,
                correlation_id,
            )
            if response is not None:
                await self._send(response)
                self._record_request(request.method, "error" if "error" in response else "success")
        except PydanticValidationError as exc:
            await self._send(
                error_response(
                    request.request_id,
                    INVALID_PARAMS,
                    "Invalid method params",
                    {"issues": exc.errors(include_input=False, include_url=False)},
                )
            )
            self._record_request(request.method, "invalid")
        except ObsionError as exc:
            await self._send(self._domain_error_response(request.request_id, exc, correlation_id))
            self._record_request(request.method, "error")
        except Exception:
            logger.exception(
                "app_server.request_failed",
                correlation_id=str(correlation_id),
                method=request.method,
            )
            await self._send(
                error_response(
                    request.request_id,
                    INTERNAL_ERROR,
                    "Internal error",
                    {"correlation_id": str(correlation_id)},
                )
            )
            self._record_request(request.method, "internal_error")

    async def _subscribe(self, request: JsonRpcRequest) -> None:
        assert request.request_id is not None
        assert self.principal is not None
        params = RunSubscribeParams.model_validate(request.params)
        if len(self.subscriptions) >= self.settings.app_server_max_subscriptions:
            raise ConflictError(
                "subscription_limit_reached",
                "The connection subscription limit has been reached",
                limit=self.settings.app_server_max_subscriptions,
            )
        run_status = await self.application.run_status(self.principal, params.run_id)
        subscription = RunSubscription(
            id=str(uuid4()),
            run_id=params.run_id,
            cursor=params.after_sequence,
        )
        await self._send(
            success_response(
                request.request_id,
                {
                    "subscription_id": subscription.id,
                    "run_id": str(subscription.run_id),
                    "after_sequence": subscription.cursor,
                    "run_status": run_status.value,
                },
            )
        )
        self.subscriptions[subscription.id] = subscription
        self.subscription_wakeup.set()

    async def _unsubscribe(self, request: JsonRpcRequest) -> None:
        assert request.request_id is not None
        params = RunUnsubscribeParams.model_validate(request.params)
        subscription = self.subscriptions.get(params.subscription_id)
        if subscription is None:
            raise NotFoundError("Run subscription", params.subscription_id)
        await self._send(
            success_response(
                request.request_id,
                {
                    "subscription_id": subscription.id,
                    "run_id": str(subscription.run_id),
                    "after_sequence": subscription.cursor,
                },
            )
        )
        self.subscriptions.pop(subscription.id, None)

    async def _poll_subscriptions(self) -> None:
        assert self.principal is not None
        last_heartbeat = monotonic()
        while True:
            with suppress(TimeoutError):
                await asyncio.wait_for(
                    self.subscription_wakeup.wait(),
                    timeout=self.settings.app_server_event_poll_interval_seconds,
                )
            self.subscription_wakeup.clear()

            for subscription in list(self.subscriptions.values()):
                if subscription.id not in self.subscriptions:
                    continue
                try:
                    batch = await self.application.run_event_batch(
                        self.principal,
                        subscription.run_id,
                        after_sequence=subscription.cursor,
                        limit=200,
                    )
                    for event in batch.events:
                        if subscription.id not in self.subscriptions:
                            break
                        run_sequence = event["run_sequence"]
                        assert isinstance(run_sequence, int)
                        await self._send(
                            notification(
                                str(event["name"]),
                                {
                                    "subscription_id": subscription.id,
                                    "event": event,
                                },
                            )
                        )
                        app_server_event_counter.add(1, {"outcome": "delivered"})
                        subscription.cursor = run_sequence
                    if (
                        subscription.id in self.subscriptions
                        and is_terminal(batch.status)
                        and len(batch.events) < 200
                    ):
                        await self._send(
                            notification(
                                "run.subscription.completed",
                                {
                                    "subscription_id": subscription.id,
                                    "run_id": str(subscription.run_id),
                                    "after_sequence": subscription.cursor,
                                    "run_status": batch.status.value,
                                },
                            )
                        )
                        self.subscriptions.pop(subscription.id, None)
                except ObsionError as exc:
                    correlation_id = uuid4()
                    await self._send(
                        notification(
                            "run.subscription.error",
                            {
                                "subscription_id": subscription.id,
                                "run_id": str(subscription.run_id),
                                "error": self._domain_error(exc, correlation_id),
                            },
                        )
                    )
                    self.subscriptions.pop(subscription.id, None)

            if monotonic() - last_heartbeat >= self.settings.event_stream_heartbeat_seconds:
                await self._send(
                    notification(
                        "server.heartbeat",
                        {
                            "server_time": utc_now().isoformat(),
                            "subscriptions": [
                                {
                                    "subscription_id": item.id,
                                    "run_id": str(item.run_id),
                                    "after_sequence": item.cursor,
                                }
                                for item in self.subscriptions.values()
                            ],
                        },
                    )
                )
                last_heartbeat = monotonic()

    async def _receive_text(self) -> str:
        message = await self.websocket.receive()
        if message["type"] == "websocket.disconnect":
            raise WebSocketDisconnect(code=int(message.get("code", 1000)))
        if message.get("bytes") is not None:
            await self.websocket.close(code=1003, reason="text frames required")
            raise WebSocketDisconnect(code=1003)
        raw = message.get("text")
        if not isinstance(raw, str):
            await self.websocket.close(code=1003, reason="text frames required")
            raise WebSocketDisconnect(code=1003)
        if len(raw.encode("utf-8")) > self.settings.app_server_max_message_bytes:
            await self.websocket.close(code=1009, reason="message too large")
            raise WebSocketDisconnect(code=1009)
        return raw

    async def _send(self, body: dict[str, Any]) -> None:
        async with self.send_lock:
            await self.websocket.send_json(body)

    def _header_bearer_token(self) -> str | None:
        authorization = self.websocket.headers.get("authorization")
        if not authorization:
            return None
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token.strip():
            return None
        return token.strip()

    def _session_cookie(self) -> str | None:
        return self.websocket.cookies.get(self.settings.auth_session_cookie_name)

    @staticmethod
    def _record_request(method: str, outcome: str) -> None:
        app_server_request_counter.add(
            1,
            {
                "method": method if method in APP_SERVER_METHODS else "unknown",
                "outcome": outcome,
            },
        )

    @staticmethod
    def _domain_error(exc: ObsionError, correlation_id: UUID) -> dict[str, Any]:
        return {
            "code": application_error_code(exc.status_code),
            "message": exc.message,
            "data": {
                "code": exc.code,
                "status": exc.status_code,
                "correlation_id": str(correlation_id),
                "details": exc.details,
            },
        }

    @classmethod
    def _domain_error_response(
        cls,
        request_id: str | int,
        exc: ObsionError,
        correlation_id: UUID,
    ) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": cls._domain_error(exc, correlation_id),
        }


def _origin_allowed(origin: str | None, allowed_origins: list[str]) -> bool:
    if origin is None:
        return True
    return "*" in allowed_origins or origin in allowed_origins


@router.websocket("/app-server")
async def app_server(websocket: WebSocket) -> None:
    settings = cast(Settings, websocket.app.state.settings)
    if not _origin_allowed(websocket.headers.get("origin"), settings.allowed_origins):
        await websocket.close(code=1008, reason="origin not allowed")
        return
    offered_protocols = cast(list[str], websocket.scope.get("subprotocols", []))
    if WEBSOCKET_SUBPROTOCOL not in offered_protocols:
        await websocket.close(code=1008, reason="required subprotocol not offered")
        return

    await websocket.accept(subprotocol=WEBSOCKET_SUBPROTOCOL)
    application = cast(AppServerApplication, websocket.app.state.app_server_application)
    await AppServerConnection(
        websocket,
        settings,
        application,
    ).run()
