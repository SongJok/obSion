from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from obsion_im.channel import ImDeliveryReceipt, OutboundMessage
from obsion_im.config import DINGTALK_HTTP_DELIVERY, DingTalkCredentials, ImError, normalize_channel

DINGTALK_ORIGIN = "https://oapi.dingtalk.com"
TOKEN_PATH = "/gettoken"  # noqa: S105 - vendor endpoint path, not a credential
MESSAGE_PATH = "/chat/send"
MAX_RESPONSE_BYTES = 1_048_576
MAX_ATTEMPTS = 3
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class DingTalkReceipt:
    message_id: str


class DingTalkClient:
    """Bounded DingTalk OpenAPI client. Credentials and access tokens never leave this object."""

    def __init__(
        self,
        credentials: DingTalkCredentials,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
    ) -> None:
        self._credentials = credentials
        self._sleep = sleep
        self._clock = clock
        self._client = httpx.AsyncClient(
            base_url=DINGTALK_ORIGIN,
            transport=transport,
            timeout=httpx.Timeout(15.0, connect=5.0),
            follow_redirects=False,
            headers={"Accept": "application/json"},
        )
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    async def health(self) -> dict[str, Any]:
        await self._access_token_value()
        return {
            "channel": "dingtalk",
            "delivery": DINGTALK_HTTP_DELIVERY,
            "authenticated": True,
            "token_cached": True,
            "expires_in_seconds": max(
                0,
                int(self._access_token_expires_at - self._clock()),
            ),
        }

    async def send_text(
        self,
        *,
        chat_id: str,
        text: str,
        idempotency_key: str,
    ) -> DingTalkReceipt:
        receive_id = chat_id.strip()
        if not receive_id:
            raise ImError("DingTalk delivery requires a conversation id")
        content = text.strip()
        if not content:
            raise ImError("DingTalk delivery requires non-empty text")
        if not idempotency_key.strip():
            raise ImError("DingTalk delivery requires an idempotency key")
        token = await self._access_token_value()
        payload = await self._request_json(
            "POST",
            MESSAGE_PATH,
            params={"access_token": token},
            json_body={
                "chatid": receive_id,
                "msg": {
                    "msgtype": "text",
                    "text": {"content": content},
                },
            },
        )
        self._require_success(payload, operation="send message", token=token)
        message_id = str(payload.get("messageId") or payload.get("message_id") or "").strip()
        if not message_id:
            # DingTalk chat/send may omit messageId on some tenants; pin the governed delivery id.
            message_id = idempotency_key.strip()
        return DingTalkReceipt(message_id=message_id)

    async def aclose(self) -> None:
        self._access_token = None
        self._access_token_expires_at = 0.0
        await self._client.aclose()

    async def _access_token_value(self) -> str:
        if self._access_token and self._clock() < self._access_token_expires_at:
            return self._access_token
        async with self._token_lock:
            if self._access_token and self._clock() < self._access_token_expires_at:
                return self._access_token
            payload = await self._request_json(
                "GET",
                TOKEN_PATH,
                params={
                    "appkey": self._credentials.app_key,
                    "appsecret": self._credentials.app_secret,
                },
            )
            self._require_success(payload, operation="authenticate")
            token = payload.get("access_token")
            expire = payload.get("expires_in")
            if not isinstance(token, str) or not token:
                raise ImError("DingTalk authentication response did not contain an access token")
            if not isinstance(expire, int) or expire <= 0:
                raise ImError("DingTalk authentication response did not contain a valid expiry")
            self._access_token = token
            self._access_token_expires_at = self._clock() + max(1, expire - 60)
            return token

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_transport_error: httpx.TransportError | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await self._client.request(
                    method,
                    path,
                    params=params,
                    json=dict(json_body) if json_body is not None else None,
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
                raise ImError(f"DingTalk HTTP request failed with status {response.status_code}")
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise ImError("DingTalk HTTP response exceeded the size limit")
            try:
                payload = response.json()
            except ValueError as exc:
                raise ImError("DingTalk HTTP response was not valid JSON") from exc
            if not isinstance(payload, dict):
                raise ImError("DingTalk HTTP response must be a JSON object")
            return payload
        if last_transport_error is not None:
            raise ImError(
                "DingTalk HTTP request failed after bounded retries"
            ) from last_transport_error
        raise ImError("DingTalk HTTP request failed after bounded retries")

    def _require_success(
        self,
        payload: Mapping[str, Any],
        *,
        operation: str,
        token: str | None = None,
    ) -> None:
        code = payload.get("errcode")
        if code in (0, "0"):
            return
        if code is None and payload.get("access_token"):
            return
        raw_message = str(payload.get("errmsg") or "request rejected")
        safe_message = _redact_vendor_message(
            raw_message,
            secrets=(self._credentials.app_key, self._credentials.app_secret, token or ""),
        )
        safe_code = code if isinstance(code, int) else "unknown"
        raise ImError(f"DingTalk {operation} failed (code {safe_code}): {safe_message}")


class DingTalkHttpChannel:
    name = "dingtalk"
    delivery = DINGTALK_HTTP_DELIVERY

    def __init__(self, client: DingTalkClient) -> None:
        self._client = client

    async def reply(self, message: OutboundMessage) -> ImDeliveryReceipt:
        if normalize_channel(message.channel) != self.name:
            raise ImError("DingTalk HTTP channel cannot deliver another vendor namespace")
        if message.delivery != self.delivery:
            raise ImError("DingTalk HTTP channel requires dingtalk-http delivery")
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
