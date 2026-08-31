"""Feishu cloud documents as a Knowledge source.

This is a Capability Fabric integration. Credentials never leave the connector
executor. Agents never receive Feishu tokens. IM Experience does not ingest
documents.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from obsion.capabilities.vendor_knowledge import VENDOR_KNOWLEDGE_BROWSE_OPERATIONS
from obsion.common.errors import AuthorizationError, ObsionError, ValidationError
from obsion.db.models import Connector
from obsion.knowledge.connector_contract import (
    KnowledgeConnectorBudget,
    SyncBudgetTracker,
)

FEISHU_ORIGIN = "https://open.feishu.cn"
FEISHU_DOCS_PROTOCOL = "feishu.docs.v1"
FEISHU_KNOWLEDGE_SOURCE = "feishu"
FEISHU_DOCS_CONNECTOR_TYPES = frozenset({"feishu-docs", "feishu-docs-http"})
FEISHU_DOCS_OPERATIONS = frozenset(
    {"knowledge.ingest", "knowledge.sync", *VENDOR_KNOWLEDGE_BROWSE_OPERATIONS}
)
DEFAULT_APP_ID_ENV = "OBSION_FEISHU_APP_ID"
TENANT_TOKEN_PATH = "/open-apis/auth/v3/tenant_access_token/internal/"  # noqa: S105
DOCUMENT_PATH = "/open-apis/docx/v1/documents/{document_id}"
RAW_CONTENT_PATH = "/open-apis/docx/v1/documents/{document_id}/raw_content"
WIKI_NODE_PATH = "/open-apis/wiki/v2/spaces/get_node"
WIKI_SPACES_PATH = "/open-apis/wiki/v2/spaces"
WIKI_NODES_PATH = "/open-apis/wiki/v2/spaces/{space_id}/nodes"
PERMISSION_MEMBERS_PATH = "/open-apis/drive/v1/permissions/{token}/members"
MAX_RESPONSE_BYTES = 2_097_152
MAX_ATTEMPTS = 3
PAGE_SIZE = 50
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
# Feishu intentionally uses HTTP 400 for both missing and inaccessible document
# resources. Treat both as denied to avoid turning the connector into an existence
# oracle across ACL boundaries.
DENIED_VENDOR_CODES = frozenset({99991663, 99991664, 99991672, 99991668, 99992402})
DOCUMENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{9,99}$")
SPACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,99}$")
ObjType = Literal["auto", "docx", "wiki"]

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class FeishuDocsUnavailableError(ObsionError):
    def __init__(self, message: str = "The Feishu docs connector is unavailable") -> None:
        super().__init__("feishu_docs_upstream_unavailable", message, status_code=503)


class FeishuDocsResponseError(ObsionError):
    def __init__(
        self,
        message: str = "The Feishu docs connector returned an invalid response",
    ) -> None:
        super().__init__(
            "feishu_docs_response_invalid",
            message,
            status_code=503,
        )


class FeishuDocsDeniedError(AuthorizationError):
    def __init__(self, message: str = "Feishu denied the document fetch") -> None:
        super().__init__("feishu_docs_upstream_denied", message)


@dataclass(frozen=True, slots=True)
class FeishuDocument:
    document_id: str
    title: str
    content: str
    revision_id: str | None
    obj_type: str
    wiki_token: str | None
    inherited_acl: dict[str, Any] | None = None

    def as_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "feishu_document_id": self.document_id,
            "feishu_obj_type": self.obj_type,
        }
        if self.revision_id:
            metadata["feishu_revision_id"] = self.revision_id
        if self.wiki_token:
            metadata["feishu_wiki_token"] = self.wiki_token
        return metadata


@dataclass(frozen=True, slots=True)
class FeishuWikiSpace:
    space_id: str
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class FeishuWikiNode:
    space_id: str
    node_token: str
    obj_token: str
    obj_type: str
    title: str


def normalize_document_id(value: Any) -> str:
    if not isinstance(value, str) or not DOCUMENT_ID_PATTERN.fullmatch(value.strip()):
        raise ValidationError(
            "feishu_docs_document_id_invalid",
            "A valid Feishu document or wiki token is required",
        )
    return value.strip()


def normalize_space_id(value: Any) -> str:
    if not isinstance(value, str) or not SPACE_ID_PATTERN.fullmatch(value.strip()):
        raise ValidationError(
            "feishu_docs_space_id_invalid",
            "A valid Feishu wiki space id is required",
        )
    return value.strip()


def normalize_obj_type(value: Any, *, document_id: str) -> ObjType:
    if value in (None, "", "auto"):
        return "wiki" if document_id.casefold().startswith("wik") else "docx"
    if value in {"docx", "wiki"}:
        return "docx" if value == "docx" else "wiki"
    raise ValidationError(
        "feishu_docs_obj_type_unsupported",
        "Only Feishu docx documents and wiki nodes that resolve to docx are supported",
        obj_type=str(value),
    )


def is_feishu_docs_connector(connector: Connector) -> bool:
    connector_type = connector.connector_type.casefold()
    configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
    protocol = configuration.get("protocol")
    return connector_type in FEISHU_DOCS_CONNECTOR_TYPES or (
        isinstance(protocol, str) and protocol.casefold() == FEISHU_DOCS_PROTOCOL
    )


def resolve_feishu_docs_credentials(
    connector: Connector, credential: str | None
) -> tuple[str, str]:
    configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
    forbidden = {"app_id", "app_secret", "appId", "appSecret", "secret", "encrypt_key"}
    if forbidden & set(configuration):
        raise ValidationError(
            "feishu_docs_operation_invalid",
            "Feishu credentials cannot be stored on the connector configuration",
        )
    app_id_env = configuration.get("app_id_env", DEFAULT_APP_ID_ENV)
    if not isinstance(app_id_env, str) or not app_id_env.startswith("OBSION_"):
        raise ValidationError(
            "feishu_docs_operation_invalid",
            "Feishu app id must be referenced by an OBSION_ environment name",
        )
    app_id = os.environ.get(app_id_env, "").strip()
    app_secret = (credential or "").strip()
    if not app_id or not app_secret:
        raise ValidationError(
            "credential_unavailable",
            "The Feishu docs connector credential is not available",
        )
    return app_id, app_secret


def merge_permission_members(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    data = payload.get("data")
    items = data.get("items") if isinstance(data, Mapping) else None
    if not isinstance(items, list):
        return None
    users: list[str] = []
    departments: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        member_type = str(item.get("member_type") or item.get("type") or "").casefold()
        member_id = item.get("member_id") or item.get("id")
        if not isinstance(member_id, str) or not member_id.strip():
            continue
        if member_type in {"userid", "user", "openid", "open_id"}:
            users.append(member_id.strip())
        elif member_type in {"opendepartmentid", "department"}:
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


class FeishuDocsClient:
    """Bounded Feishu OpenAPI client for docs and wiki spaces. Secrets never leave this object."""

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
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

    def __repr__(self) -> str:
        return "FeishuDocsClient(authenticated=redacted)"

    async def health(self) -> dict[str, Any]:
        await self._tenant_access_token()
        return {
            "source": FEISHU_KNOWLEDGE_SOURCE,
            "protocol": FEISHU_DOCS_PROTOCOL,
            "authenticated": True,
            "token_cached": True,
            "expires_in_seconds": max(0, int(self._tenant_token_expires_at - self._clock())),
        }

    async def fetch_document(
        self,
        *,
        document_id: str,
        obj_type: ObjType,
        inherit_acl: bool = False,
    ) -> FeishuDocument:
        token = normalize_document_id(document_id)
        resolved_type = normalize_obj_type(obj_type, document_id=token)
        wiki_token: str | None = None
        if resolved_type == "wiki":
            node = await self._wiki_node(token)
            wiki_token = token
            token = node["document_id"]
            resolved_type = "docx"
        access_token = await self._tenant_access_token()
        meta = await self._request_json(
            "GET",
            DOCUMENT_PATH.format(document_id=token),
            headers={"Authorization": f"Bearer {access_token}"},
        )
        self._require_success(meta, operation="read document", token=access_token)
        raw = await self._request_json(
            "GET",
            RAW_CONTENT_PATH.format(document_id=token),
            headers={"Authorization": f"Bearer {access_token}"},
        )
        self._require_success(raw, operation="read document content", token=access_token)
        title, revision_id = _document_identity(meta, fallback_id=token)
        content = _document_content(raw)
        inherited_acl = None
        if inherit_acl:
            members = await self._request_json(
                "GET",
                PERMISSION_MEMBERS_PATH.format(token=token),
                params={"type": "docx"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            self._require_success(
                members, operation="read document permissions", token=access_token
            )
            inherited_acl = merge_permission_members(members)
        return FeishuDocument(
            document_id=token,
            title=title,
            content=content,
            revision_id=revision_id,
            obj_type=resolved_type,
            wiki_token=wiki_token,
            inherited_acl=inherited_acl,
        )

    async def list_spaces(
        self,
        *,
        tracker: SyncBudgetTracker | None = None,
    ) -> list[FeishuWikiSpace]:
        items = await self._paginate(
            WIKI_SPACES_PATH,
            operation="list wiki spaces",
            tracker=tracker,
        )
        spaces: list[FeishuWikiSpace] = []
        for item in items:
            space_id = _required_id(item.get("space_id"), field="space_id")
            name = item.get("name")
            spaces.append(
                FeishuWikiSpace(
                    space_id=space_id,
                    name=name.strip() if isinstance(name, str) and name.strip() else space_id,
                    description=str(item.get("description") or ""),
                )
            )
        return spaces

    async def list_nodes(
        self,
        space_id: str,
        *,
        tracker: SyncBudgetTracker | None = None,
    ) -> list[FeishuWikiNode]:
        resolved = normalize_space_id(space_id)
        budget = tracker or SyncBudgetTracker(KnowledgeConnectorBudget())
        collected: list[FeishuWikiNode] = []
        seen: set[str] = set()
        await self._walk_nodes(
            resolved,
            parent_node_token=None,
            depth=0,
            collected=collected,
            seen=seen,
            tracker=budget,
        )
        return collected

    async def _walk_nodes(
        self,
        space_id: str,
        *,
        parent_node_token: str | None,
        depth: int,
        collected: list[FeishuWikiNode],
        seen: set[str],
        tracker: SyncBudgetTracker,
    ) -> None:
        tracker.enter_depth(depth)
        items = await self._paginate(
            WIKI_NODES_PATH.format(space_id=space_id),
            operation="list wiki nodes",
            parent_node_token=parent_node_token,
            tracker=tracker,
        )
        for item in items:
            node = _parse_wiki_node(space_id, item)
            if node.node_token in seen:
                continue
            tracker.consume_node()
            seen.add(node.node_token)
            collected.append(node)
            if item.get("has_child") is True:
                await self._walk_nodes(
                    space_id,
                    parent_node_token=node.node_token,
                    depth=depth + 1,
                    collected=collected,
                    seen=seen,
                    tracker=tracker,
                )

    async def _paginate(
        self,
        path: str,
        *,
        operation: str,
        parent_node_token: str | None = None,
        tracker: SyncBudgetTracker | None = None,
    ) -> list[Mapping[str, Any]]:
        access_token = await self._tenant_access_token()
        budget = tracker or SyncBudgetTracker(KnowledgeConnectorBudget())
        collected: list[Mapping[str, Any]] = []
        page_token = ""
        while True:
            budget.consume_page()
            params = {"page_size": str(PAGE_SIZE)}
            if page_token:
                params["page_token"] = page_token
            if parent_node_token:
                params["parent_node_token"] = parent_node_token
            payload = await self._request_json(
                "GET",
                path,
                params=params,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            self._require_success(payload, operation=operation, token=access_token)
            data = payload.get("data")
            if not isinstance(data, Mapping):
                raise FeishuDocsResponseError(f"Feishu {operation} response omitted data")
            items = data.get("items")
            if items is None:
                items = []
            if not isinstance(items, list):
                raise FeishuDocsResponseError(f"Feishu {operation} response items must be a list")
            for item in items:
                if not isinstance(item, Mapping):
                    raise FeishuDocsResponseError(f"Feishu {operation} returned a non-object item")
                collected.append(item)
            if data.get("has_more") is not True:
                return collected
            next_token = data.get("page_token")
            if not isinstance(next_token, str) or not next_token:
                raise FeishuDocsResponseError(f"Feishu {operation} omitted the next page token")
            page_token = next_token

    async def aclose(self) -> None:
        self._tenant_token = None
        self._tenant_token_expires_at = 0.0
        await self._client.aclose()

    async def _wiki_node(self, token: str) -> dict[str, str]:
        access_token = await self._tenant_access_token()
        payload = await self._request_json(
            "GET",
            WIKI_NODE_PATH,
            params={"token": token},
            headers={"Authorization": f"Bearer {access_token}"},
        )
        self._require_success(payload, operation="resolve wiki node", token=access_token)
        data = payload.get("data")
        node = data.get("node") if isinstance(data, Mapping) else None
        if not isinstance(node, Mapping):
            raise FeishuDocsResponseError("Feishu wiki node response did not contain a node")
        obj_type = str(node.get("obj_type") or "")
        obj_token = node.get("obj_token")
        if obj_type != "docx" or not isinstance(obj_token, str) or not obj_token.strip():
            raise ValidationError(
                "feishu_docs_obj_type_unsupported",
                "The Feishu wiki node does not resolve to a docx document",
                obj_type=obj_type,
            )
        return {"document_id": obj_token.strip(), "title": str(node.get("title") or "")}

    async def _tenant_access_token(self) -> str:
        if self._tenant_token and self._clock() < self._tenant_token_expires_at:
            return self._tenant_token
        async with self._token_lock:
            if self._tenant_token and self._clock() < self._tenant_token_expires_at:
                return self._tenant_token
            payload = await self._request_json(
                "POST",
                TENANT_TOKEN_PATH,
                json_body={"app_id": self._app_id, "app_secret": self._app_secret},
            )
            self._require_success(payload, operation="authenticate")
            token = payload.get("tenant_access_token")
            expire = payload.get("expire")
            if not isinstance(token, str) or not token:
                raise FeishuDocsResponseError(
                    "Feishu authentication response did not contain a tenant token"
                )
            if not isinstance(expire, int) or expire <= 0:
                raise FeishuDocsResponseError(
                    "Feishu authentication response did not contain a valid expiry"
                )
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
        json_body: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        last_transport_error: httpx.TransportError | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await self._client.request(
                    method,
                    path,
                    params=params,
                    headers=headers,
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
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise FeishuDocsResponseError("Feishu HTTP response exceeded the size limit")
            try:
                payload = response.json()
            except ValueError as exc:
                if 200 <= response.status_code < 300:
                    raise FeishuDocsResponseError(
                        "Feishu HTTP response was not valid JSON"
                    ) from exc
                payload = {}
            if not isinstance(payload, dict):
                if 200 <= response.status_code < 300:
                    raise FeishuDocsResponseError("Feishu HTTP response must be a JSON object")
                payload = {}
            if response.status_code in {401, 403}:
                raise FeishuDocsDeniedError("Feishu denied the document request")
            if response.status_code < 200 or response.status_code >= 300:
                # Feishu returns structured business errors with HTTP 400. Parse and
                # classify that envelope before falling back to transport health.
                if payload.get("code") not in {None, 0}:
                    self._require_success(payload, operation="request")
                raise FeishuDocsUnavailableError(
                    f"Feishu HTTP request failed with status {response.status_code}"
                )
            return payload
        if last_transport_error is not None:
            raise FeishuDocsUnavailableError(
                "Feishu HTTP request failed after bounded retries"
            ) from last_transport_error
        raise FeishuDocsUnavailableError("Feishu HTTP request failed after bounded retries")

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
            secrets=(self._app_id, self._app_secret, token or ""),
        )
        if isinstance(code, int) and code in DENIED_VENDOR_CODES:
            raise FeishuDocsDeniedError(f"Feishu {operation} was denied (code {code})")
        if isinstance(code, int):
            raise FeishuDocsResponseError(
                f"Feishu {operation} failed (code {code}): {safe_message}"
            )
        raise FeishuDocsResponseError(f"Feishu {operation} failed: {safe_message}")


async def fetch_authorized_feishu_document(
    *,
    app_id: str,
    app_secret: str,
    document_id: str,
    obj_type: ObjType | str = "auto",
    inherit_acl: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FeishuDocument:
    client = FeishuDocsClient(app_id=app_id, app_secret=app_secret, transport=transport)
    try:
        return await client.fetch_document(
            document_id=document_id,
            obj_type=normalize_obj_type(obj_type, document_id=normalize_document_id(document_id)),
            inherit_acl=inherit_acl,
        )
    finally:
        await client.aclose()


def assert_feishu_docs_egress(connector: Connector) -> None:
    if connector.configuration.get("base_url") or connector.configuration.get("baseUrl"):
        raise ValidationError(
            "feishu_docs_operation_invalid",
            "Feishu docs connectors cannot override the OpenAPI origin",
        )
    endpoint = connector.endpoint or FEISHU_ORIGIN
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or parsed.hostname != "open.feishu.cn":
        raise ValidationError(
            "connector_egress_denied",
            "Feishu docs connectors may only call https://open.feishu.cn",
        )
    if parsed.username is not None or parsed.password is not None:
        raise ValidationError(
            "connector_egress_denied",
            "Feishu docs connectors may only call https://open.feishu.cn",
        )
    try:
        allowed = {_authority(item) for item in connector.allowed_egress if isinstance(item, str)}
        origin = _authority(FEISHU_ORIGIN)
    except ValueError as exc:
        raise ValidationError(
            "connector_egress_invalid",
            "Connector egress configuration is invalid",
        ) from exc
    if origin not in allowed:
        raise ValidationError(
            "connector_egress_denied",
            "Feishu docs connectors may only call https://open.feishu.cn",
        )


def _required_id(value: Any, *, field: str) -> str:
    if isinstance(value, bool):
        raise FeishuDocsResponseError(f"Feishu wiki response omitted {field}")
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str) or not value.strip():
        raise FeishuDocsResponseError(f"Feishu wiki response omitted {field}")
    return value.strip()


def _parse_wiki_node(space_id: str, item: Mapping[str, Any]) -> FeishuWikiNode:
    node_token = _required_id(item.get("node_token"), field="node_token")
    obj_token = _required_id(item.get("obj_token"), field="obj_token")
    obj_type = _required_id(item.get("obj_type"), field="obj_type")
    title = item.get("title")
    return FeishuWikiNode(
        space_id=space_id,
        node_token=node_token,
        obj_token=obj_token,
        obj_type=obj_type,
        title=title.strip() if isinstance(title, str) and title.strip() else obj_token,
    )


def _document_identity(payload: Mapping[str, Any], *, fallback_id: str) -> tuple[str, str | None]:
    data = payload.get("data")
    document = data.get("document") if isinstance(data, Mapping) else None
    if not isinstance(document, Mapping):
        raise FeishuDocsResponseError("Feishu document response did not contain document metadata")
    title = document.get("title")
    if not isinstance(title, str) or not title.strip():
        title = fallback_id
    revision = document.get("revision_id")
    revision_id = str(revision) if revision is not None and str(revision) else None
    return title.strip(), revision_id


def _document_content(payload: Mapping[str, Any]) -> str:
    data = payload.get("data")
    content = data.get("content") if isinstance(data, Mapping) else None
    if not isinstance(content, str) or not content.strip():
        raise ValidationError("document_empty", "The Feishu document contains no extractable text")
    return content.replace("\x00", "").replace("\r\n", "\n")


def _authority(value: str) -> tuple[str, int]:
    candidate = value if "://" in value else f"https://{value}"
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or parsed.hostname is None:
        raise ValueError("invalid HTTPS authority")
    return parsed.hostname.casefold(), parsed.port or 443


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
