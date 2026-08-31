import asyncio
import importlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from typing import Any, Protocol, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

APP_SERVER_PROTOCOL_VERSION = "2026-08-26"
APP_SERVER_SUBPROTOCOL = "obsion.jsonrpc.v1"


def new_client_request_id(prefix: str = "cli") -> str:
    """Return an App Server mutation idempotency key.

    The server accepts ``[A-Za-z0-9][A-Za-z0-9._:-]*``. Callers must generate a
    new key per logical mutation and reuse it only for retries of that mutation.
    """

    return f"{prefix}-{uuid4()}"


def app_server_url_from_api_url(api_url: str) -> str:
    """Derive the WebSocket App Server URL from an HTTP API base."""

    parsed = urlsplit(api_url.strip())
    scheme = "wss" if parsed.scheme == "https" else "ws"
    path = parsed.path.rstrip("/")
    if path.endswith("/app-server"):
        websocket_path = path
    elif path.endswith("/api/v1"):
        websocket_path = f"{path}/app-server"
    elif path in {"", "/"}:
        websocket_path = "/api/v1/app-server"
    else:
        websocket_path = f"{path}/api/v1/app-server"
    return urlunsplit((scheme, parsed.netloc, websocket_path, "", ""))


class AppServerTransport(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...

    async def close(self) -> None: ...


TransportFactory = Callable[[str, list[str], dict[str, str]], Awaitable[AppServerTransport]]
Notification = dict[str, Any]


class ObsionAppServerError(Exception):
    def __init__(
        self,
        rpc_code: int,
        message: str,
        *,
        code: str | None = None,
        status: int | None = None,
        correlation_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.rpc_code = rpc_code
        self.code = code
        self.status = status
        self.correlation_id = correlation_id
        self.details = details or {}


class AsyncObsionAppServerClient:
    def __init__(
        self,
        url: str,
        *,
        token: str | None = None,
        client_name: str = "obsion-sdk-python",
        client_version: str = "0.1.0",
        transport_factory: TransportFactory | None = None,
        notification_queue_size: int = 1000,
    ) -> None:
        self.url = url
        self.token = token
        self.client_name = client_name
        self.client_version = client_version
        self._transport_factory = transport_factory or _default_transport_factory
        self._transport: AppServerTransport | None = None
        self._reader: asyncio.Task[None] | None = None
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._notifications: asyncio.Queue[Notification] = asyncio.Queue(
            maxsize=notification_queue_size
        )
        self._next_id = 1

    async def __aenter__(self) -> "AsyncObsionAppServerClient":
        await self.connect()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def connect(self) -> dict[str, Any]:
        if self._transport is not None:
            raise RuntimeError("The App Server client is already connected")
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        transport = await self._transport_factory(
            self.url,
            [APP_SERVER_SUBPROTOCOL],
            headers,
        )
        self._transport = transport
        ready = await self._receive_object(transport)
        if ready.get("method") != "server.ready":
            await transport.close()
            self._transport = None
            raise RuntimeError("The server did not begin with server.ready")

        initialize_id = self._next_request_id()
        await self._send_object(
            {
                "jsonrpc": "2.0",
                "id": initialize_id,
                "method": "server.initialize",
                "params": {
                    "protocol_version": APP_SERVER_PROTOCOL_VERSION,
                    "client_name": self.client_name,
                    "client_version": self.client_version,
                },
            }
        )
        initialized = await self._receive_object(transport)
        if initialized.get("id") != initialize_id:
            await transport.close()
            self._transport = None
            raise RuntimeError("The initialize response did not match its request")
        if error := initialized.get("error"):
            await transport.close()
            self._transport = None
            raise _decode_error(error)
        result = initialized.get("result")
        if not isinstance(result, dict):
            await transport.close()
            self._transport = None
            raise RuntimeError("The initialize response has no result")
        self._reader = asyncio.create_task(
            self._read_loop(),
            name="obsion-sdk-app-server-reader",
        )
        return cast(dict[str, Any], result)

    async def aclose(self) -> None:
        reader = self._reader
        self._reader = None
        if reader is not None:
            reader.cancel()
            with suppress(asyncio.CancelledError):
                await reader
        transport = self._transport
        self._transport = None
        if transport is not None:
            await transport.close()
        self._fail_pending(RuntimeError("The App Server connection was closed"))

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if self._transport is None or self._reader is None:
            raise RuntimeError("Call connect() before sending requests")
        request_id = self._next_request_id()
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._send_object(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params or {},
                }
            )
            return await future
        except BaseException:
            self._pending.pop(request_id, None)
            raise

    async def notifications(self) -> AsyncIterator[Notification]:
        while self._transport is not None:
            yield await self._notifications.get()

    async def create_thread(
        self,
        workspace_id: str,
        title: str,
        *,
        client_request_id: str,
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self.request(
                "thread.create",
                {
                    "client_request_id": client_request_id,
                    "workspace_id": workspace_id,
                    "title": title,
                },
            ),
        )

    async def create_turn(
        self,
        thread_id: str,
        text: str,
        *,
        client_request_id: str,
        context_refs: list[dict[str, Any]] | None = None,
        attachment_refs: list[dict[str, Any]] | None = None,
        model_profile: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "client_request_id": client_request_id,
            "thread_id": thread_id,
            "input": text,
            "context_refs": context_refs or [],
            "attachment_refs": attachment_refs or [],
        }
        if model_profile is not None:
            params["model_profile"] = model_profile
        return cast(dict[str, Any], await self.request("turn.create", params))

    async def subscribe_run(self, run_id: str, *, after_sequence: int = 0) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self.request(
                "run.subscribe",
                {"run_id": run_id, "after_sequence": after_sequence},
            ),
        )

    async def unsubscribe_run(self, subscription_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self.request(
                "run.unsubscribe",
                {"subscription_id": subscription_id},
            ),
        )

    async def list_workspaces(self, *, include_archived: bool = False) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self.request("workspace.list", {"include_archived": include_archived}),
        )

    async def list_threads(
        self, workspace_id: str, *, include_archived: bool = False
    ) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self.request(
                "thread.list",
                {"workspace_id": workspace_id, "include_archived": include_archived},
            ),
        )

    async def archive_thread(self, thread_id: str, *, client_request_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self.request(
                "thread.archive",
                {"client_request_id": client_request_id, "thread_id": thread_id},
            ),
        )

    async def resume_thread(self, thread_id: str, *, client_request_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self.request(
                "thread.resume",
                {"client_request_id": client_request_id, "thread_id": thread_id},
            ),
        )

    async def fork_thread(
        self,
        thread_id: str,
        *,
        client_request_id: str,
        title: str | None = None,
        from_turn_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "client_request_id": client_request_id,
            "thread_id": thread_id,
        }
        if title is not None:
            params["title"] = title
        if from_turn_id is not None:
            params["from_turn_id"] = from_turn_id
        return cast(dict[str, Any], await self.request("thread.fork", params))

    async def list_turns(self, thread_id: str) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self.request("thread.turns", {"thread_id": thread_id}),
        )

    async def list_thread_runs(self, thread_id: str) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self.request("thread.runs", {"thread_id": thread_id}),
        )

    async def list_thread_events(
        self, thread_id: str, *, after_sequence: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self.request(
                "thread.events",
                {
                    "thread_id": thread_id,
                    "after_sequence": after_sequence,
                    "limit": limit,
                },
            ),
        )

    async def get_run(self, run_id: str) -> dict[str, Any]:
        return cast(dict[str, Any], await self.request("run.get", {"run_id": run_id}))

    async def cancel_run(self, run_id: str, *, client_request_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self.request(
                "run.cancel",
                {"client_request_id": client_request_id, "run_id": run_id},
            ),
        )

    async def replay_run(self, run_id: str, *, client_request_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self.request(
                "run.replay",
                {"client_request_id": client_request_id, "run_id": run_id},
            ),
        )

    async def list_run_events(
        self, run_id: str, *, after_sequence: int = 0, limit: int = 500
    ) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self.request(
                "run.events",
                {"run_id": run_id, "after_sequence": after_sequence, "limit": limit},
            ),
        )

    async def list_approvals(self, *, status: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if status is not None:
            params["status"] = status
        return cast(list[dict[str, Any]], await self.request("approval.list", params))

    async def decide_approval(
        self,
        approval_id: str,
        *,
        client_request_id: str,
        approve: bool,
        reason: str,
    ) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self.request(
                "approval.decide",
                {
                    "client_request_id": client_request_id,
                    "approval_id": approval_id,
                    "decision": "approve" if approve else "reject",
                    "reason": reason,
                },
            ),
        )

    async def list_artifacts(self, workspace_id: str) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self.request("artifact.list", {"workspace_id": workspace_id}),
        )

    async def get_artifact(self, artifact_id: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self.request("artifact.get", {"artifact_id": artifact_id}),
        )

    async def _read_loop(self) -> None:
        assert self._transport is not None
        try:
            while True:
                message = await self._receive_object(self._transport)
                if "id" in message:
                    request_id = message.get("id")
                    if not isinstance(request_id, int):
                        continue
                    future = self._pending.pop(request_id, None)
                    if future is None or future.done():
                        continue
                    if error := message.get("error"):
                        future.set_exception(_decode_error(error))
                    else:
                        future.set_result(message.get("result"))
                elif isinstance(message.get("method"), str):
                    await self._notifications.put(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail_pending(exc)

    async def _send_object(self, body: dict[str, Any]) -> None:
        if self._transport is None:
            raise RuntimeError("The App Server client is not connected")
        await self._transport.send(json.dumps(body, separators=(",", ":"), ensure_ascii=False))

    @staticmethod
    async def _receive_object(transport: AppServerTransport) -> dict[str, Any]:
        raw = await transport.recv()
        if not isinstance(raw, str):
            raise RuntimeError("The App Server sent a binary frame")
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise RuntimeError("The App Server sent a non-object frame")
        return cast(dict[str, Any], body)

    def _next_request_id(self) -> int:
        request_id = self._next_id
        self._next_id += 1
        return request_id

    def _fail_pending(self, exc: BaseException) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()


def _decode_error(error: Any) -> ObsionAppServerError:
    if not isinstance(error, dict):
        return ObsionAppServerError(-32603, "Malformed App Server error")
    data = error.get("data")
    if not isinstance(data, dict):
        data = {}
    details = data.get("details")
    return ObsionAppServerError(
        int(error.get("code", -32603)),
        str(error.get("message", "App Server request failed")),
        code=str(data["code"]) if "code" in data else None,
        status=int(data["status"]) if "status" in data else None,
        correlation_id=(str(data["correlation_id"]) if "correlation_id" in data else None),
        details=details if isinstance(details, dict) else {},
    )


async def _default_transport_factory(
    url: str,
    subprotocols: list[str],
    headers: dict[str, str],
) -> AppServerTransport:
    try:
        module = importlib.import_module("websockets.asyncio.client")
    except ImportError as exc:
        raise RuntimeError(
            "Install obsion-sdk[app-server] to use the default WebSocket transport"
        ) from exc
    connector = cast(Callable[..., Awaitable[AppServerTransport]], module.connect)
    return await connector(
        url,
        subprotocols=subprotocols,
        additional_headers=headers or None,
        max_size=4 * 1024 * 1024,
    )
