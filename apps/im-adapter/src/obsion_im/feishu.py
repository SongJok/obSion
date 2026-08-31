from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from obsion_im.channel import ImDeliveryReceipt, OutboundMessage
from obsion_im.config import FEISHU_HTTP_DELIVERY, FeishuCredentials, ImError, normalize_channel

FEISHU_ORIGIN = "https://open.feishu.cn"
TENANT_TOKEN_PATH = "/open-apis/auth/v3/tenant_access_token/internal/"  # noqa: S105
MESSAGE_PATH = "/open-apis/im/v1/messages"
MAX_RESPONSE_BYTES = 1_048_576
MAX_ATTEMPTS = 3
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class FeishuReceipt:
    message_id: str


class FeishuClient:
    """Bounded Feishu OpenAPI client. Credentials and access tokens never leave this object."""

    def __init__(
        self,
        credentials: FeishuCredentials,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
    ) -> None:
        self._credentials = credentials
        self._sleep = sleep
        self._clock = clock
        self._client = httpx.AsyncClient(
            base_url=FEISHU_ORIGIN,
            transport=transport,
            timeout=httpx.Timeout(15.0, connect=5.0),
            follow_redirects=False,
            headers={"Accept": "application/json"},
        )
        self._tenant_token: str | None = None
        self._tenant_token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def health(self) -> dict[str, Any]:
        await self._tenant_access_token()
        return {
            "channel": "feishu",
            "delivery": FEISHU_HTTP_DELIVERY,
            "authenticated": True,
            "token_cached": True,
            "expires_in_seconds": max(
                0,
                int(self._tenant_token_expires_at - self._clock()),
            ),
        }

    async def send_text(
        self,
        *,
        chat_id: str,
        text: str,
        idempotency_key: str,
    ) -> FeishuReceipt:
        receive_id = chat_id.strip()
        if not receive_id:
            raise ImError("Feishu delivery requires a chat_id")
        content = text.strip()
        if not content:
            raise ImError("Feishu delivery requires non-empty text")
        token = await self._tenant_access_token()
        payload = await self._request_json(
            "POST",
            MESSAGE_PATH,
            params={"receive_id_type": "chat_id"},
            headers={"Authorization": f"Bearer {token}"},
            json_body={
                "receive_id": receive_id,
                "msg_type": "text",
                "content": json.dumps({"text": content}, ensure_ascii=False),
                "uuid": idempotency_key,
            },
        )
        self._require_success(payload, operation="send message", token=token)
        data = payload.get("data")
        message_id = str(data.get("message_id") or "") if isinstance(data, Mapping) else ""
        if not message_id:
            raise ImError("Feishu send message response did not contain a message_id")
        return FeishuReceipt(message_id=message_id)

    async def aclose(self) -> None:
        self._tenant_token = None
        self._tenant_token_expires_at = 0.0
        await self._client.aclose()

    async def _tenant_access_token(self) -> str:
        if self._tenant_token and self._clock() < self._tenant_token_expires_at:
            return self._tenant_token
        async with self._token_lock:
            if self._tenant_token and self._clock() < self._tenant_token_expires_at:
                return self._tenant_token
            payload = await self._request_json(
                "POST",
                TENANT_TOKEN_PATH,
                json_body={
                    "app_id": self._credentials.app_id,
                    "app_secret": self._credentials.app_secret,
                },
            )
            self._require_success(payload, operation="authenticate")
            token = payload.get("tenant_access_token")
            expire = payload.get("expire")
            if not isinstance(token, str) or not token:
                raise ImError("Feishu authentication response did not contain a tenant token")
            if not isinstance(expire, int) or expire <= 0:
                raise ImError("Feishu authentication response did not contain a valid expiry")
            self._tenant_token = token
            self._tenant_token_expires_at = self._clock() + max(1, expire - 60)
            return token

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any],
    ) -> dict[str, Any]:
        last_transport_error: httpx.TransportError | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await self._client.request(
                    method,
                    path,
                    params=params,
                    headers=headers,
                    json=dict(json_body),
                )
            except httpx.TransportError as exc:
                last_transport_error = exc
                if attempt + 1 == MAX_ATTEMPTS:
                    break
                await self._sleep(0.25 * (2**attempt))
                continue
            if response.status_code in RETRYABLE_STATUS_CODES and attempt + 1 < MAX_ATTEMPTS:
                await self._sleep(_retry_delay(response, attempt))
                continue
            if response.status_code < 200 or response.status_code >= 300:
                raise ImError(f"Feishu HTTP request failed with status {response.status_code}")
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise ImError("Feishu HTTP response exceeded the size limit")
            try:
                payload = response.json()
            except ValueError as exc:
                raise ImError("Feishu HTTP response was not valid JSON") from exc
            if not isinstance(payload, dict):
                raise ImError("Feishu HTTP response must be a JSON object")
            return payload
        if last_transport_error is not None:
            raise ImError(
                "Feishu HTTP request failed after bounded retries"
            ) from last_transport_error
        raise ImError("Feishu HTTP request failed after bounded retries")

    def _require_success(
        self,
        payload: Mapping[str, Any],
        *,
        operation: str,
        token: str | None = None,
    ) -> None:
        code = payload.get("code")
        if code == 0:
            return
        raw_message = str(payload.get("msg") or "request rejected")
        safe_message = _redact_vendor_message(
            raw_message,
            secrets=(self._credentials.app_id, self._credentials.app_secret, token or ""),
        )
        safe_code = code if isinstance(code, int) else "unknown"
        raise ImError(f"Feishu {operation} failed (code {safe_code}): {safe_message}")


class FeishuHttpChannel:
    name = "feishu"
    delivery = FEISHU_HTTP_DELIVERY

    def __init__(self, client: FeishuClient) -> None:
        self._client = client

    async def reply(self, message: OutboundMessage) -> ImDeliveryReceipt:
        if normalize_channel(message.channel) != self.name:
            raise ImError("Feishu HTTP channel cannot deliver another vendor namespace")
        if message.delivery != self.delivery:
            raise ImError("Feishu HTTP channel requires feishu-http delivery")
        receipt = await self._client.send_text(
            chat_id=message.conversation_id,
            text=message.text,
            idempotency_key=message.delivery_id or message.run_id,
        )
        return ImDeliveryReceipt(vendor_message_id=receipt.message_id)

    async def health(self) -> dict[str, Any]:
        return await self._client.health()

    async def aclose(self) -> None:
        await self._client.aclose()


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after:
        try:
            return min(5.0, max(0.0, float(retry_after)))
        except ValueError:
            pass
    return 0.25 * float(2**attempt)


def _redact_vendor_message(message: str, *, secrets: tuple[str, ...]) -> str:
    safe = message.replace("\r", " ").replace("\n", " ")
    for secret in secrets:
        if secret:
            safe = safe.replace(secret, "[redacted]")
    return safe[:240]
