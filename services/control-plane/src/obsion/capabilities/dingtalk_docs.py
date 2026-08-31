"""DingTalk cloud documents as a Knowledge source.

This is a Capability Fabric integration. Credentials never leave the connector
executor. Agents never receive DingTalk tokens. IM Experience does not ingest
documents. Only https://api.dingtalk.com is permitted.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from obsion.capabilities.vendor_knowledge import VENDOR_KNOWLEDGE_BROWSE_OPERATIONS
from obsion.common.errors import AuthorizationError, ObsionError, ValidationError
from obsion.db.models import Connector
from obsion.knowledge.connector_contract import (
    KnowledgeConnectorBudget,
    SyncBudgetTracker,
)

DINGTALK_ORIGIN = "https://api.dingtalk.com"
DINGTALK_DOCS_PROTOCOL = "dingtalk.docs.v1"
DINGTALK_KNOWLEDGE_SOURCE = "dingtalk"
DINGTALK_DOCS_CONNECTOR_TYPES = frozenset({"dingtalk-docs", "dingtalk-docs-http"})
DINGTALK_DOCS_OPERATIONS = frozenset(
    {"knowledge.ingest", "knowledge.sync", *VENDOR_KNOWLEDGE_BROWSE_OPERATIONS}
)
DEFAULT_APP_KEY_ENV = "OBSION_DINGTALK_APP_KEY"
TOKEN_PATH = "/v1.0/oauth2/accessToken"  # noqa: S105 - vendor endpoint path
DOCUMENT_PATH = "/v1.0/doc/documents/{document_id}"
CONTENT_PATH = "/v1.0/doc/documents/{document_id}/content"
MEMBERS_PATH = "/v1.0/doc/documents/{document_id}/members"
WORKSPACES_PATH = "/v1.0/doc/workspaces"
WORKSPACE_NODES_PATH = "/v1.0/doc/workspaces/{workspace_id}/nodes"
MAX_RESPONSE_BYTES = 2_097_152
MAX_ATTEMPTS = 3
PAGE_SIZE = 50
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
DOCUMENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")
WORKSPACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,127}$")

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class DingTalkDocsUnavailableError(ObsionError):
    def __init__(self, message: str = "The DingTalk docs connector is unavailable") -> None:
        super().__init__("dingtalk_docs_upstream_unavailable", message, status_code=503)


class DingTalkDocsResponseError(ObsionError):
    def __init__(
        self,
        message: str = "The DingTalk docs connector returned an invalid response",
    ) -> None:
        super().__init__("dingtalk_docs_response_invalid", message, status_code=503)


class DingTalkDocsDeniedError(AuthorizationError):
    def __init__(self, message: str = "DingTalk denied the document fetch") -> None:
        super().__init__("dingtalk_docs_upstream_denied", message)


@dataclass(frozen=True, slots=True)
class DingTalkDocument:
    document_id: str
    title: str
    content: str
    revision_id: str | None
    workspace_id: str | None
    inherited_acl: dict[str, Any] | None = None

    def as_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "dingtalk_document_id": self.document_id,
            "dingtalk_obj_type": "document",
        }
        if self.revision_id:
            metadata["dingtalk_revision_id"] = self.revision_id
        if self.workspace_id:
            metadata["dingtalk_workspace_id"] = self.workspace_id
        return metadata


@dataclass(frozen=True, slots=True)
class DingTalkWorkspace:
    workspace_id: str
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class DingTalkWorkspaceNode:
    workspace_id: str
    node_id: str
    document_id: str
    node_type: str
    title: str


def normalize_document_id(value: Any) -> str:
    if not isinstance(value, str) or not DOCUMENT_ID_PATTERN.fullmatch(value.strip()):
        raise ValidationError(
            "dingtalk_docs_document_id_invalid",
            "A valid DingTalk document id is required",
        )
    return value.strip()


def normalize_workspace_id(value: Any) -> str:
    if not isinstance(value, str) or not WORKSPACE_ID_PATTERN.fullmatch(value.strip()):
        raise ValidationError(
            "dingtalk_docs_workspace_id_invalid",
            "A valid DingTalk workspace id is required",
        )
    return value.strip()


def is_dingtalk_docs_connector(connector: Connector) -> bool:
    connector_type = connector.connector_type.casefold()
    configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
    protocol = configuration.get("protocol")
    return connector_type in DINGTALK_DOCS_CONNECTOR_TYPES or (
        isinstance(protocol, str) and protocol.casefold() == DINGTALK_DOCS_PROTOCOL
    )


def resolve_dingtalk_docs_credentials(
    connector: Connector, credential: str | None
) -> tuple[str, str]:
    configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
    forbidden = {"app_key", "app_secret", "appKey", "appSecret", "secret", "client_secret"}
    if forbidden & set(configuration):
        raise ValidationError(
            "dingtalk_docs_operation_invalid",
            "DingTalk credentials cannot be stored on the connector configuration",
        )
    app_key_env = configuration.get("app_key_env", DEFAULT_APP_KEY_ENV)
    if not isinstance(app_key_env, str) or not app_key_env.startswith("OBSION_"):
        raise ValidationError(
            "dingtalk_docs_operation_invalid",
            "DingTalk app key must be referenced by an OBSION_ environment name",
        )
    app_key = os.environ.get(app_key_env, "").strip()
    app_secret = (credential or "").strip()
    if not app_key or not app_secret:
        raise ValidationError(
            "credential_unavailable",
            "The DingTalk docs connector credential is not available",
        )
    return app_key, app_secret


def merge_permission_members(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    items = payload.get("members") or payload.get("items") or payload.get("list")
    if not isinstance(items, list):
        data = payload.get("data")
        if isinstance(data, Mapping):
            items = data.get("members") or data.get("items") or data.get("list")
    if not isinstance(items, list):
        return None
    users: list[str] = []
    departments: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        member_type = str(
            item.get("memberType") or item.get("member_type") or item.get("type") or ""
        ).casefold()
        member_id = (
            item.get("memberId")
            or item.get("member_id")
            or item.get("userid")
            or item.get("userId")
            or item.get("id")
        )
        if not isinstance(member_id, str) or not member_id.strip():
            continue
        if member_type in {"userid", "user", "staffid", "staff_id", ""}:
            users.append(member_id.strip())
        elif member_type in {"dept", "department", "dept_id", "deptid"}:
            departments.append(member_id.strip())
    if not users and not departments:
        return None
    return {
        "organization": False,
        "users": users,
        "roles": [],
        "departments": departments,
        "deny_users": [],
        "deny_roles": [],
        "deny_departments": [],
    }


class DingTalkDocsClient:
    """Bounded DingTalk Doc OpenAPI client. Secrets never leave this object."""

    def __init__(
        self,
        *,
        app_key: str,
        app_secret: str,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
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

    def __repr__(self) -> str:
        return "DingTalkDocsClient(authenticated=redacted)"

    async def fetch_document(
        self,
        *,
        document_id: str,
        inherit_acl: bool = False,
    ) -> DingTalkDocument:
        token_id = normalize_document_id(document_id)
        meta = await self._request_json(
            "GET",
            DOCUMENT_PATH.format(document_id=token_id),
            authorized=True,
        )
        data = meta.get("result") if isinstance(meta.get("result"), Mapping) else meta
        if not isinstance(data, Mapping):
            raise DingTalkDocsResponseError("DingTalk document metadata was not an object")
        title = str(data.get("name") or data.get("title") or token_id).strip() or token_id
        revision = data.get("version") or data.get("revisionId") or data.get("revision_id")
        workspace_id = data.get("workspaceId") or data.get("workspace_id")
        content_payload = await self._request_json(
            "GET",
            CONTENT_PATH.format(document_id=token_id),
            authorized=True,
            params={"type": "markdown"},
        )
        content = _extract_content(content_payload)
        if not content.strip():
            raise DingTalkDocsResponseError("DingTalk document content was empty")
        inherited: dict[str, Any] | None = None
        if inherit_acl:
            members = await self._request_json(
                "GET",
                MEMBERS_PATH.format(document_id=token_id),
                authorized=True,
            )
            inherited = merge_permission_members(members)
        return DingTalkDocument(
            document_id=token_id,
            title=title,
            content=content,
            revision_id=str(revision) if revision is not None else None,
            workspace_id=str(workspace_id).strip() if workspace_id else None,
            inherited_acl=inherited,
        )

    async def list_workspaces(
        self,
        *,
        tracker: SyncBudgetTracker | None = None,
    ) -> list[DingTalkWorkspace]:
        budget = tracker or SyncBudgetTracker(KnowledgeConnectorBudget())
        spaces: list[DingTalkWorkspace] = []
        next_token: str | None = None
        while True:
            budget.consume_page()
            params: dict[str, str] = {"maxResults": str(PAGE_SIZE)}
            if next_token:
                params["nextToken"] = next_token
            payload = await self._request_json(
                "GET",
                WORKSPACES_PATH,
                authorized=True,
                params=params,
            )
            items = payload.get("workspaces") or payload.get("result") or payload.get("items")
            if isinstance(payload.get("result"), Mapping):
                nested = payload["result"]
                items = nested.get("workspaces") or nested.get("items") or items
            if not isinstance(items, list):
                break
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                workspace_id = str(
                    item.get("workspaceId") or item.get("workspace_id") or item.get("id") or ""
                ).strip()
                if not workspace_id:
                    continue
                spaces.append(
                    DingTalkWorkspace(
                        workspace_id=workspace_id,
                        name=str(item.get("name") or workspace_id).strip() or workspace_id,
                        description=str(item.get("description") or "").strip(),
                    )
                )
            next_token = (
                str(payload.get("nextToken") or payload.get("next_token") or "").strip() or None
            )
            if not next_token:
                break
        return spaces

    async def list_workspace_nodes(
        self,
        workspace_id: str,
        *,
        tracker: SyncBudgetTracker | None = None,
    ) -> list[DingTalkWorkspaceNode]:
        space = normalize_workspace_id(workspace_id)
        budget = tracker or SyncBudgetTracker(KnowledgeConnectorBudget())
        nodes: list[DingTalkWorkspaceNode] = []
        next_token: str | None = None
        while True:
            budget.consume_page()
            params: dict[str, str] = {"maxResults": str(PAGE_SIZE)}
            if next_token:
                params["nextToken"] = next_token
            payload = await self._request_json(
                "GET",
                WORKSPACE_NODES_PATH.format(workspace_id=space),
                authorized=True,
                params=params,
            )
            items = payload.get("nodes") or payload.get("result") or payload.get("items")
            if isinstance(payload.get("result"), Mapping):
                nested = payload["result"]
                items = nested.get("nodes") or nested.get("items") or items
            if not isinstance(items, list):
                break
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                node_id = str(
                    item.get("nodeId") or item.get("node_id") or item.get("id") or ""
                ).strip()
                document_id = str(
                    item.get("documentId")
                    or item.get("document_id")
                    or item.get("docId")
                    or node_id
                ).strip()
                node_type = str(item.get("type") or item.get("nodeType") or "document").casefold()
                title = (
                    str(item.get("name") or item.get("title") or document_id).strip() or document_id
                )
                if not node_id or not document_id:
                    continue
                budget.consume_node()
                nodes.append(
                    DingTalkWorkspaceNode(
                        workspace_id=space,
                        node_id=node_id,
                        document_id=document_id,
                        node_type=node_type,
                        title=title,
                    )
                )
            next_token = (
                str(payload.get("nextToken") or payload.get("next_token") or "").strip() or None
            )
            if not next_token:
                break
        return nodes

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
                "POST",
                TOKEN_PATH,
                json_body={"appKey": self._app_key, "appSecret": self._app_secret},
            )
            token = payload.get("accessToken") or payload.get("access_token")
            expire = (
                payload.get("expireIn") or payload.get("expire_in") or payload.get("expires_in")
            )
            if not isinstance(token, str) or not token:
                raise DingTalkDocsResponseError(
                    "DingTalk authentication response did not contain an access token"
                )
            if not isinstance(expire, int) or expire <= 0:
                raise DingTalkDocsResponseError(
                    "DingTalk authentication response did not contain a valid expiry"
                )
            self._access_token = token
            self._access_token_expires_at = self._clock() + max(1, expire - 60)
            return token

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        authorized: bool = False,
        params: Mapping[str, str] | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_transport_error: httpx.TransportError | None = None
        for attempt in range(MAX_ATTEMPTS):
            headers: dict[str, str] = {}
            if authorized:
                headers["x-acs-dingtalk-access-token"] = await self._access_token_value()
            try:
                response = await self._client.request(
                    method,
                    path,
                    params=params,
                    headers=headers or None,
                    json=dict(json_body) if json_body is not None else None,
                )
            except httpx.TransportError as exc:
                last_transport_error = exc
                if attempt + 1 == MAX_ATTEMPTS:
                    break
                await self._sleep(0.25 * (2**attempt))
                continue
            if response.status_code in RETRYABLE_STATUS_CODES and attempt + 1 < MAX_ATTEMPTS:
                await self._sleep(0.25 * float(2**attempt))
                continue
            if response.status_code in {401, 403}:
                raise DingTalkDocsDeniedError()
            if response.status_code < 200 or response.status_code >= 300:
                raise DingTalkDocsUnavailableError(
                    f"DingTalk docs HTTP request failed with status {response.status_code}"
                )
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise DingTalkDocsResponseError(
                    "DingTalk docs HTTP response exceeded the size limit"
                )
            try:
                payload = response.json()
            except ValueError as exc:
                raise DingTalkDocsResponseError(
                    "DingTalk docs HTTP response was not valid JSON"
                ) from exc
            if not isinstance(payload, dict):
                raise DingTalkDocsResponseError("DingTalk docs HTTP response must be a JSON object")
            code = payload.get("code") or payload.get("errcode")
            if code not in (None, 0, "0", "OK", "ok"):
                message = str(payload.get("message") or payload.get("errmsg") or "request rejected")
                if any(
                    part in message.casefold() for part in ("denied", "forbidden", "unauthorized")
                ):
                    raise DingTalkDocsDeniedError(message)
                raise DingTalkDocsUnavailableError(message)
            return payload
        if last_transport_error is not None:
            raise DingTalkDocsUnavailableError() from last_transport_error
        raise DingTalkDocsUnavailableError()


async def fetch_authorized_dingtalk_document(
    *,
    app_key: str,
    app_secret: str,
    document_id: str,
    inherit_acl: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> DingTalkDocument:
    client = DingTalkDocsClient(
        app_key=app_key,
        app_secret=app_secret,
        transport=transport,
    )
    try:
        return await client.fetch_document(document_id=document_id, inherit_acl=inherit_acl)
    finally:
        await client.aclose()


def assert_dingtalk_docs_egress(connector: Connector) -> None:
    endpoint = (connector.endpoint or "").strip() or DINGTALK_ORIGIN
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or parsed.hostname != "api.dingtalk.com":
        raise ValidationError(
            "connector_egress_denied",
            "DingTalk docs connectors may only call https://api.dingtalk.com",
        )
    configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
    if "base_url" in configuration or "baseUrl" in configuration:
        raise ValidationError(
            "dingtalk_docs_operation_invalid",
            "DingTalk docs connectors may only call https://api.dingtalk.com",
        )
    allowed = connector.allowed_egress or []
    if allowed and "https://api.dingtalk.com" not in allowed:
        raise ValidationError(
            "connector_egress_denied",
            "DingTalk docs connectors may only call https://api.dingtalk.com",
        )


def _extract_content(payload: Mapping[str, Any]) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content
    result = payload.get("result")
    if isinstance(result, str):
        return result
    if isinstance(result, Mapping):
        for key in ("content", "markdown", "text", "body"):
            value = result.get(key)
            if isinstance(value, str):
                return value
    for key in ("markdown", "text", "body"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""
