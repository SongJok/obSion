"""Internal robot transport for the durable Outbox Gateway (not an Agent tool).

No API or executor registration until durable admission and current audience Policy
are wired. The caller must commit the dispatch claim before invoking send_text.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeGuard

import httpx

ORIGIN = "https://api.dingtalk.com"
TOKEN_PATH = "/v1.0/oauth2/accessToken"  # noqa: S105
SEND_PATH = "/v1.0/robot/oToMessages/batchSend"
GROUP_SEND_PATH = "/v1.0/robot/groupMessages/send"
QUERY_PATH = "/v1.0/robot/oToMessages/readStatus"
MAX_TEXT_BYTES = 4096
MAX_RESPONSE_BYTES = 65_536
DINGTALK_ROBOT_CAPABILITY = "im.dingtalk.robot.reply"
DINGTALK_ROBOT_PROTOCOL = "dingtalk.robot.oto.v1"


class RobotSendState(StrEnum):
    NOT_ATTEMPTED = "NOT_ATTEMPTED"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    UNKNOWN = "UNKNOWN"


class RobotQueryState(StrEnum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class RobotCredentials:
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)

    def __post_init__(self) -> None:
        if not _opaque(self.app_key, 255) or not _opaque(self.app_secret, 4096):
            raise ValueError("Invalid robot credential configuration")


@dataclass(frozen=True, slots=True)
class RobotSendResult:
    state: RobotSendState
    process_query_key: str | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class RobotQueryResult:
    state: RobotQueryState
    send_status: str | None = None
    read_status: str | None = None
    read_timestamp_ms: int | None = None
    reason: str | None = None


def connector_fingerprint(
    *,
    connector_type: str,
    environment: str,
    endpoint: str | None,
    configuration: Mapping[str, Any],
    credential_ref: str | None,
    declared_grants: list[object],
    allowed_egress: list[object],
) -> str:
    """Hash the complete delivery configuration without exposing its values."""

    payload = {
        "connector_type": connector_type,
        "environment": environment,
        "endpoint": endpoint,
        "configuration": dict(configuration),
        "credential_ref": credential_ref,
        "declared_grants": declared_grants,
        "allowed_egress": allowed_egress,
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _opaque(value: Any, maximum: int) -> TypeGuard[str]:
    return (
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and value.isascii()
        and all(33 <= ord(char) < 127 for char in value)
    )


def _validate_text(text: str, credentials: RobotCredentials) -> None:
    if (
        not isinstance(text, str)
        or not text.strip()
        or len(text) > MAX_TEXT_BYTES
        or any(ord(char) < 32 and char not in "\n\t" for char in text)
    ):
        raise ValueError("Invalid robot text")
    try:
        encoded = text.encode("utf-8")
    except UnicodeError:
        raise ValueError("Invalid robot text encoding") from None
    if len(encoded) > MAX_TEXT_BYTES:
        raise ValueError("Robot text exceeds the delivery budget")
    if credentials.app_secret in text:
        raise ValueError("Robot text contains a credential")


class DingTalkRobotTransport:
    """One bounded token request, then at most one vendor message POST.

    Tokens are scoped to this invocation and discarded afterwards. This avoids
    cross-installation cache confusion; a broker cache needs its own revocation
    and lifetime contract. No exception or raw vendor error enters the result.
    """

    def __init__(self, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def send_text(
        self,
        credentials: RobotCredentials,
        *,
        robot_code: str,
        user_id: str,
        text: str,
    ) -> RobotSendResult:
        # Invalid caller input fails before authentication or message I/O.
        if not _opaque(robot_code, 255) or not _opaque(user_id, 255):
            raise ValueError("Invalid robot or recipient identity")
        _validate_text(text, credentials)

        message_attempted = False
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=httpx.Timeout(15.0, connect=5.0),
                follow_redirects=False,
                trust_env=False,
                headers={"Accept": "application/json", "Accept-Encoding": "identity"},
            ) as client:
                token_response = await self._json(
                    client,
                    TOKEN_PATH,
                    body={"appKey": credentials.app_key, "appSecret": credentials.app_secret},
                )
                token = self._access_token(token_response, credentials)
                if token in text:
                    return RobotSendResult(
                        RobotSendState.NOT_ATTEMPTED, reason="credential_in_text"
                    )
                message_attempted = True
                response = await self._json(
                    client,
                    SEND_PATH,
                    body={
                        "robotCode": robot_code,
                        "userIds": [user_id],
                        "msgKey": "sampleText",
                        "msgParam": json.dumps({"content": text}, ensure_ascii=False),
                    },
                    token=token,
                )
                return self._receipt(response, user_id, (token, credentials.app_secret))
        except (httpx.HTTPError, OSError, TimeoutError, ValueError, TypeError, RecursionError):
            # CancelledError deliberately propagates. A durable DISPATCHING claim
            # must be recovered as UNKNOWN, never requeued after cancellation.
            return RobotSendResult(
                RobotSendState.UNKNOWN if message_attempted else RobotSendState.NOT_ATTEMPTED,
                reason="unverified_send_response" if message_attempted else "authentication_failed",
            )

    async def send_group_text(
        self,
        credentials: RobotCredentials,
        *,
        robot_code: str,
        open_conversation_id: str,
        text: str,
    ) -> RobotSendResult:
        """Send one text message to an operator-authorized group.

        Group delivery uses the fixed application-bot endpoint and the same
        one-attempt/UNKNOWN semantics as one-to-one delivery. No webhook or
        arbitrary recipient list is accepted.
        """

        if not _opaque(robot_code, 255) or not _opaque(open_conversation_id, 512):
            raise ValueError("Invalid robot or group identity")
        _validate_text(text, credentials)
        message_attempted = False
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=httpx.Timeout(15.0, connect=5.0),
                follow_redirects=False,
                trust_env=False,
                headers={"Accept": "application/json", "Accept-Encoding": "identity"},
            ) as client:
                token_response = await self._json(
                    client,
                    TOKEN_PATH,
                    body={"appKey": credentials.app_key, "appSecret": credentials.app_secret},
                )
                token = self._access_token(token_response, credentials)
                if token in text:
                    return RobotSendResult(
                        RobotSendState.NOT_ATTEMPTED, reason="credential_in_text"
                    )
                message_attempted = True
                response = await self._json(
                    client,
                    GROUP_SEND_PATH,
                    body={
                        "robotCode": robot_code,
                        "openConversationId": open_conversation_id,
                        "msgKey": "sampleText",
                        "msgParam": json.dumps({"content": text}, ensure_ascii=False),
                    },
                    token=token,
                )
                return self._receipt(response, None, (token, credentials.app_secret))
        except (httpx.HTTPError, OSError, TimeoutError, ValueError, TypeError, RecursionError):
            return RobotSendResult(
                RobotSendState.UNKNOWN if message_attempted else RobotSendState.NOT_ATTEMPTED,
                reason="unverified_send_response" if message_attempted else "authentication_failed",
            )

    async def query_status(
        self,
        credentials: RobotCredentials,
        *,
        robot_code: str,
        user_id: str | None = None,
        open_conversation_id: str | None = None,
        process_query_key: str,
    ) -> RobotQueryResult:
        """Read one vendor send status without creating another message."""

        if (
            not _opaque(robot_code, 255)
            or (user_id is not None and not _opaque(user_id, 255))
            or (open_conversation_id is not None and not _opaque(open_conversation_id, 512))
            or (user_id is None and open_conversation_id is None)
            or not _opaque(process_query_key, 500)
        ):
            raise ValueError("Invalid robot reconciliation identity")
        try:
            async with httpx.AsyncClient(
                transport=self._transport,
                timeout=httpx.Timeout(15.0, connect=5.0),
                follow_redirects=False,
                trust_env=False,
                headers={"Accept": "application/json", "Accept-Encoding": "identity"},
            ) as client:
                token_response = await self._json(
                    client,
                    TOKEN_PATH,
                    body={"appKey": credentials.app_key, "appSecret": credentials.app_secret},
                )
                token = self._access_token(token_response, credentials)
                query: dict[str, str] = {
                    "processQueryKey": process_query_key,
                    "robotCode": robot_code,
                }
                if open_conversation_id is not None:
                    query["openConversationId"] = open_conversation_id
                response = await self._json(
                    client,
                    QUERY_PATH,
                    method="GET",
                    query=query,
                    token=token,
                )
                return self._query_receipt(response, user_id)
        except (httpx.HTTPError, OSError, TimeoutError, ValueError, TypeError, RecursionError):
            return RobotQueryResult(RobotQueryState.UNKNOWN, reason="reconciliation_unavailable")

    @staticmethod
    def _access_token(response: dict[str, Any], credentials: RobotCredentials) -> str:
        token = response.get("accessToken")
        expire = response.get("expireIn")
        if (
            not _opaque(token, 4096)
            or type(expire) is not int
            or not 0 < expire <= 86_400
            or token in {credentials.app_key, credentials.app_secret}
        ):
            raise ValueError("Invalid token response")
        return token

    @staticmethod
    async def _json(
        client: httpx.AsyncClient,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        method: str = "POST",
        query: Mapping[str, str] | None = None,
        token: str | None = None,
    ) -> dict[str, Any]:
        headers = {"x-acs-dingtalk-access-token": token} if token else {}
        if method == "GET":
            request = client.stream(
                method,
                ORIGIN + path,
                params=dict(query or {}),
                headers=headers,
            )
        else:
            request = client.stream(
                method,
                ORIGIN + path,
                json=body or {},
                headers=headers,
            )
        async with request as response:
            if response.status_code != 200:
                raise ValueError("Vendor request was not successful")
            if (
                response.headers.get("content-encoding", "identity").strip().casefold()
                != "identity"
            ):
                raise ValueError("Encoded vendor response is unsupported")
            data = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=8192):
                if len(data) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise ValueError("Vendor response exceeds budget")
                data.extend(chunk)
        parsed = json.loads(data)
        if not isinstance(parsed, dict) or "code" in parsed or "errcode" in parsed:
            raise ValueError("Invalid vendor response")
        return parsed

    @staticmethod
    def _query_receipt(response: dict[str, Any], user_id: str | None) -> RobotQueryResult:
        status = response.get("sendStatus")
        if not _opaque(status, 64):
            return RobotQueryResult(
                RobotQueryState.UNKNOWN, reason="reconciliation_response_invalid"
            )
        normalized = status.upper()
        if normalized == "SUCCESS":
            state = RobotQueryState.SUCCESS
        elif normalized in {"FAIL", "FAILED", "FAILURE"}:
            state = RobotQueryState.FAILURE
        else:
            state = RobotQueryState.UNKNOWN

        read_status: str | None = None
        read_timestamp_ms: int | None = None
        values = response.get("messageReadInfoList", [])
        if not isinstance(values, list) or len(values) > 1:
            return RobotQueryResult(
                RobotQueryState.UNKNOWN,
                send_status=status,
                reason="reconciliation_response_invalid",
            )
        if values:
            item = values[0]
            if not isinstance(item, dict) or (
                user_id is not None and item.get("userId") != user_id
            ):
                return RobotQueryResult(
                    RobotQueryState.UNKNOWN,
                    send_status=status,
                    reason="reconciliation_response_invalid",
                )
            candidate_status = item.get("readStatus")
            candidate_timestamp = item.get("readTimestamp")
            if not _opaque(candidate_status, 32) or (
                candidate_timestamp is not None
                and (type(candidate_timestamp) is not int or candidate_timestamp < 0)
            ):
                return RobotQueryResult(
                    RobotQueryState.UNKNOWN,
                    send_status=status,
                    reason="reconciliation_response_invalid",
                )
            read_status = candidate_status
            read_timestamp_ms = candidate_timestamp
        return RobotQueryResult(
            state,
            send_status=status,
            read_status=read_status,
            read_timestamp_ms=read_timestamp_ms,
            reason=None if state != RobotQueryState.UNKNOWN else "reconciliation_status_unknown",
        )

    @staticmethod
    def _receipt(
        response: dict[str, Any], recipient_id: str | None, credentials: tuple[str, ...]
    ) -> RobotSendResult:
        rejected = False
        for field_name in ("invalidStaffIdList", "flowControlledStaffIdList"):
            values = response.get(field_name, [])
            if (
                not isinstance(values, list)
                or len(values) > (1 if recipient_id is not None else 500)
                or any(
                    not _opaque(value, 255) or (recipient_id is not None and value != recipient_id)
                    for value in values
                )
            ):
                raise ValueError("Unexpected recipient in vendor response")
            rejected = rejected or bool(values)
        if rejected:
            return RobotSendResult(RobotSendState.REJECTED, reason="recipient_rejected")
        key = response.get("processQueryKey")
        if not _opaque(key, 500) or any(secret in key for secret in credentials):
            raise ValueError("Invalid vendor processing key")
        return RobotSendResult(RobotSendState.ACCEPTED, process_query_key=key)
