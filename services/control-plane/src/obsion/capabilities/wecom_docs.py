"""WeCom cloud documents as a Knowledge source.

This is a Capability Fabric integration. Credentials never leave the connector
executor. Agents never receive WeCom tokens. IM Experience does not ingest
documents. Only https://qyapi.weixin.qq.com is permitted.
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

WECOM_ORIGIN = "https://qyapi.weixin.qq.com"
WECOM_DOCS_PROTOCOL = "wecom.docs.v1"
WECOM_KNOWLEDGE_SOURCE = "wecom"
WECOM_DOCS_CONNECTOR_TYPES = frozenset({"wecom-docs", "wecom-docs-http"})
WECOM_DOCS_OPERATIONS = frozenset(
    {"knowledge.ingest", "knowledge.sync", *VENDOR_KNOWLEDGE_BROWSE_OPERATIONS}
)
DEFAULT_CORP_ID_ENV = "OBSION_WECOM_CORP_ID"
TOKEN_PATH = "/cgi-bin/gettoken"  # noqa: S105 - vendor endpoint path, not a credential
DOC_BASE_INFO_PATH = "/cgi-bin/wedoc/get_doc_base_info"
DOC_CONTENT_PATH = "/cgi-bin/wedoc/document/get"
DOC_AUTH_PATH = "/cgi-bin/wedoc/doc_get_auth"
SPACE_INFO_PATH = "/cgi-bin/wedrive/space_info"
FILE_LIST_PATH = "/cgi-bin/wedrive/file_list"
MAX_RESPONSE_BYTES = 2_097_152
MAX_ATTEMPTS = 3
PAGE_SIZE = 50
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
DOCUMENT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{7,127}$")
SPACE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,127}$")
# WeDrive file_type: 1=folder, 3=微文档. Others are skipped during sync.
WEDRIVE_FOLDER_TYPE = 1
WEDRIVE_DOC_TYPE = 3

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


class WeComDocsUnavailableError(ObsionError):
    def __init__(self, message: str = "The WeCom docs connector is unavailable") -> None:
        super().__init__("wecom_docs_upstream_unavailable", message, status_code=503)


class WeComDocsResponseError(ObsionError):
    def __init__(
        self,
        message: str = "The WeCom docs connector returned an invalid response",
    ) -> None:
        super().__init__("wecom_docs_response_invalid", message, status_code=503)


class WeComDocsDeniedError(AuthorizationError):
    def __init__(self, message: str = "WeCom denied the document fetch") -> None:
        super().__init__("wecom_docs_upstream_denied", message)


@dataclass(frozen=True, slots=True)
class WeComDocument:
    document_id: str
    title: str
    content: str
    revision_id: str | None
    space_id: str | None
    inherited_acl: dict[str, Any] | None = None

    def as_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "wecom_document_id": self.document_id,
            "wecom_obj_type": "document",
        }
        if self.revision_id:
            metadata["wecom_revision_id"] = self.revision_id
        if self.space_id:
            metadata["wecom_space_id"] = self.space_id
        return metadata


@dataclass(frozen=True, slots=True)
class WeComSpace:
    space_id: str
    name: str
    description: str


@dataclass(frozen=True, slots=True)
class WeComSpaceNode:
    space_id: str
    node_id: str
    document_id: str
    node_type: str
    title: str


def normalize_document_id(value: Any) -> str:
    if not isinstance(value, str) or not DOCUMENT_ID_PATTERN.fullmatch(value.strip()):
        raise ValidationError(
            "wecom_docs_document_id_invalid",
            "A valid WeCom document id is required",
        )
    return value.strip()


def normalize_space_id(value: Any) -> str:
    if not isinstance(value, str) or not SPACE_ID_PATTERN.fullmatch(value.strip()):
        raise ValidationError(
            "wecom_docs_space_id_invalid",
            "A valid WeCom WeDrive space id is required",
        )
    return value.strip()


def is_wecom_docs_connector(connector: Connector) -> bool:
    connector_type = connector.connector_type.casefold()
    configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
    protocol = configuration.get("protocol")
    return connector_type in WECOM_DOCS_CONNECTOR_TYPES or (
        isinstance(protocol, str) and protocol.casefold() == WECOM_DOCS_PROTOCOL
    )


def resolve_wecom_docs_credentials(connector: Connector, credential: str | None) -> tuple[str, str]:
    configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
    forbidden = {
        "corp_id",
        "corp_secret",
        "corpId",
        "corpSecret",
        "secret",
        "agent_id",
        "agentId",
    }
    if forbidden & set(configuration):
        raise ValidationError(
            "wecom_docs_operation_invalid",
            "WeCom credentials cannot be stored on the connector configuration",
        )
    corp_id_env = configuration.get("corp_id_env", DEFAULT_CORP_ID_ENV)
    if not isinstance(corp_id_env, str) or not corp_id_env.startswith("OBSION_"):
        raise ValidationError(
            "wecom_docs_operation_invalid",
            "WeCom corp id must be referenced by an OBSION_ environment name",
        )
    corp_id = os.environ.get(corp_id_env, "").strip()
    corp_secret = (credential or "").strip()
    if not corp_id or not corp_secret:
        raise ValidationError(
            "credential_unavailable",
            "The WeCom docs connector credential is not available",
        )
    return corp_id, corp_secret


def merge_permission_members(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Map WeCom doc_get_auth members. Never invent organization: true."""
    members = payload.get("doc_member_list") or payload.get("co_auth_list")
    if not isinstance(members, list):
        members = []
        for key in ("doc_member_list", "co_auth_list"):
            value = payload.get(key)
            if isinstance(value, list):
                members.extend(value)
    if not members:
        return None
    users: list[str] = []
    departments: list[str] = []
    for item in members:
        if not isinstance(item, Mapping):
            continue
        # Official: type 1 = userid, type 2 = department.
        member_type = item.get("type")
        if member_type in (1, "1", "userid", "user"):
            userid = item.get("userid") or item.get("userId") or item.get("id")
            if isinstance(userid, str) and userid.strip():
                users.append(userid.strip())
        elif member_type in (2, "2", "party", "department", "dept"):
            dept = (
                item.get("departmentid")
                or item.get("department_id")
                or item.get("partyid")
                or item.get("id")
            )
            if dept is not None and str(dept).strip():
                departments.append(str(dept).strip())
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


class WeComDocsClient:
    """Bounded WeCom Doc/WeDrive OpenAPI client. Secrets never leave this object."""

    def __init__(
        self,
        *,
        corp_id: str,
        corp_secret: str,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
    ) -> None:
        self._corp_id = corp_id
        self._corp_secret = corp_secret
        self._sleep = sleep
        self._clock = clock
        self._client = httpx.AsyncClient(
            base_url=WECOM_ORIGIN,
            transport=transport,
            timeout=httpx.Timeout(15.0, connect=5.0),
            follow_redirects=False,
            headers={"Accept": "application/json"},
        )
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

    def __repr__(self) -> str:
        return "WeComDocsClient(authenticated=redacted)"

    async def fetch_document(
        self,
        *,
        document_id: str,
        inherit_acl: bool = False,
    ) -> WeComDocument:
        token_id = normalize_document_id(document_id)
        meta = await self._request_json(
            "POST",
            DOC_BASE_INFO_PATH,
            authorized=True,
            json_body={"docid": token_id},
        )
        base = meta.get("doc_base_info")
        if not isinstance(base, Mapping):
            raise WeComDocsResponseError("WeCom document metadata was not an object")
        title = str(base.get("doc_name") or base.get("name") or token_id).strip() or token_id
        revision = base.get("version") or meta.get("version")
        space_id = base.get("spaceid") or base.get("space_id")
        content_payload = await self._request_json(
            "POST",
            DOC_CONTENT_PATH,
            authorized=True,
            json_body={"docid": token_id},
        )
        content = _extract_wedoc_text(content_payload)
        if not content.strip():
            raise WeComDocsResponseError("WeCom document content was empty")
        if revision is None:
            revision = content_payload.get("version")
        inherited: dict[str, Any] | None = None
        if inherit_acl:
            auth = await self._request_json(
                "POST",
                DOC_AUTH_PATH,
                authorized=True,
                json_body={"docid": token_id},
            )
            inherited = merge_permission_members(auth)
        return WeComDocument(
            document_id=token_id,
            title=title,
            content=content,
            revision_id=str(revision) if revision is not None else None,
            space_id=str(space_id).strip() if space_id else None,
            inherited_acl=inherited,
        )

    async def describe_space(self, space_id: str) -> WeComSpace:
        space = normalize_space_id(space_id)
        payload = await self._request_json(
            "POST",
            SPACE_INFO_PATH,
            authorized=True,
            json_body={"spaceid": space},
        )
        info = (
            payload.get("space_info") if isinstance(payload.get("space_info"), Mapping) else payload
        )
        if not isinstance(info, Mapping):
            raise WeComDocsResponseError("WeCom space metadata was not an object")
        name = str(info.get("space_name") or info.get("name") or space).strip() or space
        description = str(info.get("space_desc") or info.get("description") or "").strip()
        return WeComSpace(space_id=space, name=name, description=description)

    async def list_space_nodes(
        self,
        space_id: str,
        *,
        tracker: SyncBudgetTracker | None = None,
    ) -> list[WeComSpaceNode]:
        space = normalize_space_id(space_id)
        await self.describe_space(space)
        budget = tracker or SyncBudgetTracker(KnowledgeConnectorBudget())
        nodes: list[WeComSpaceNode] = []
        folders: list[str] = [""]
        seen_folders: set[str] = {""}
        while folders:
            parent = folders.pop(0)
            next_start = 0
            while True:
                budget.consume_page()
                body: dict[str, Any] = {
                    "spaceid": space,
                    "fatherid": parent,
                    "start": next_start,
                    "limit": PAGE_SIZE,
                }
                payload = await self._request_json(
                    "POST",
                    FILE_LIST_PATH,
                    authorized=True,
                    json_body=body,
                )
                items = payload.get("file_list") or payload.get("files") or payload.get("filelist")
                if isinstance(items, Mapping):
                    items = items.get("item") or items.get("items") or items.get("list")
                if not isinstance(items, list):
                    break
                for item in items:
                    if not isinstance(item, Mapping):
                        continue
                    file_id = str(
                        item.get("fileid") or item.get("file_id") or item.get("id") or ""
                    ).strip()
                    title = (
                        str(item.get("file_name") or item.get("name") or file_id).strip() or file_id
                    )
                    file_type = item.get("file_type") or item.get("filetype") or item.get("type")
                    try:
                        typed = int(file_type) if file_type is not None else -1
                    except (TypeError, ValueError):
                        typed = -1
                    if typed == WEDRIVE_FOLDER_TYPE and file_id and file_id not in seen_folders:
                        seen_folders.add(file_id)
                        folders.append(file_id)
                        continue
                    if typed != WEDRIVE_DOC_TYPE:
                        if file_id:
                            budget.consume_node()
                            nodes.append(
                                WeComSpaceNode(
                                    space_id=space,
                                    node_id=file_id,
                                    document_id=file_id,
                                    node_type=f"file_type_{typed}",
                                    title=title,
                                )
                            )
                        continue
                    docid = str(
                        item.get("docid") or item.get("doc_id") or item.get("document_id") or ""
                    ).strip()
                    if not docid:
                        budget.consume_node()
                        nodes.append(
                            WeComSpaceNode(
                                space_id=space,
                                node_id=file_id or title,
                                document_id="",
                                node_type="wedrive_doc_without_docid",
                                title=title,
                            )
                        )
                        continue
                    budget.consume_node()
                    nodes.append(
                        WeComSpaceNode(
                            space_id=space,
                            node_id=file_id or docid,
                            document_id=docid,
                            node_type="document",
                            title=title,
                        )
                    )
                has_more = payload.get("has_more")
                if has_more is True or has_more == 1:
                    next_start += PAGE_SIZE
                    continue
                next_cursor = payload.get("next_start")
                if isinstance(next_cursor, int) and next_cursor > next_start:
                    next_start = next_cursor
                    continue
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
                "GET",
                TOKEN_PATH,
                params={"corpid": self._corp_id, "corpsecret": self._corp_secret},
            )
            token = payload.get("access_token")
            expire = payload.get("expires_in")
            if not isinstance(token, str) or not token:
                raise WeComDocsResponseError(
                    "WeCom authentication response did not contain an access token"
                )
            if not isinstance(expire, int) or expire <= 0:
                raise WeComDocsResponseError(
                    "WeCom authentication response did not contain a valid expiry"
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
            request_params = dict(params or {})
            if authorized:
                request_params["access_token"] = await self._access_token_value()
            try:
                response = await self._client.request(
                    method,
                    path,
                    params=request_params or None,
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
                raise WeComDocsDeniedError()
            if response.status_code < 200 or response.status_code >= 300:
                raise WeComDocsUnavailableError(
                    f"WeCom docs HTTP request failed with status {response.status_code}"
                )
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise WeComDocsResponseError("WeCom docs HTTP response exceeded the size limit")
            try:
                payload = response.json()
            except ValueError as exc:
                raise WeComDocsResponseError("WeCom docs HTTP response was not valid JSON") from exc
            if not isinstance(payload, dict):
                raise WeComDocsResponseError("WeCom docs HTTP response must be a JSON object")
            code = payload.get("errcode")
            if code not in (None, 0, "0"):
                message = str(payload.get("errmsg") or "request rejected")
                lowered = message.casefold()
                if any(
                    part in lowered
                    for part in ("denied", "forbidden", "unauthorized", "no privilege", "not allow")
                ) or code in {48002, 60011, 60111, 40014, 42001}:
                    raise WeComDocsDeniedError(message)
                raise WeComDocsUnavailableError(message)
            return payload
        if last_transport_error is not None:
            raise WeComDocsUnavailableError() from last_transport_error
        raise WeComDocsUnavailableError()


async def fetch_authorized_wecom_document(
    *,
    corp_id: str,
    corp_secret: str,
    document_id: str,
    inherit_acl: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> WeComDocument:
    client = WeComDocsClient(
        corp_id=corp_id,
        corp_secret=corp_secret,
        transport=transport,
    )
    try:
        return await client.fetch_document(document_id=document_id, inherit_acl=inherit_acl)
    finally:
        await client.aclose()


def assert_wecom_docs_egress(connector: Connector) -> None:
    endpoint = (connector.endpoint or "").strip() or WECOM_ORIGIN
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or parsed.hostname != "qyapi.weixin.qq.com":
        raise ValidationError(
            "connector_egress_denied",
            "WeCom docs connectors may only call https://qyapi.weixin.qq.com",
        )
    configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
    if "base_url" in configuration or "baseUrl" in configuration:
        raise ValidationError(
            "wecom_docs_operation_invalid",
            "WeCom docs connectors may only call https://qyapi.weixin.qq.com",
        )
    allowed = connector.allowed_egress or []
    if allowed and "https://qyapi.weixin.qq.com" not in allowed:
        raise ValidationError(
            "connector_egress_denied",
            "WeCom docs connectors may only call https://qyapi.weixin.qq.com",
        )


def _extract_wedoc_text(payload: Mapping[str, Any]) -> str:
    content = payload.get("content")
    if isinstance(content, str):
        return content
    document = payload.get("document")
    if isinstance(document, str):
        return document
    chunks: list[str] = []
    if isinstance(document, Mapping):
        _walk_text_nodes(document, chunks)
    elif isinstance(document, list):
        for item in document:
            if isinstance(item, Mapping):
                _walk_text_nodes(item, chunks)
    for key in ("markdown", "text", "body"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return "\n".join(part for part in chunks if part.strip()).strip()


def _walk_text_nodes(node: Mapping[str, Any], chunks: list[str]) -> None:
    for key in ("text", "content", "plain_text", "plaintext"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            chunks.append(value.strip())
    text_obj = node.get("Text") or node.get("text_run") or node.get("textRun")
    if isinstance(text_obj, Mapping):
        for key in ("content", "text", "plain_text"):
            value = text_obj.get(key)
            if isinstance(value, str) and value.strip():
                chunks.append(value.strip())
    elif isinstance(text_obj, str) and text_obj.strip():
        chunks.append(text_obj.strip())
    for key in ("children", "paragraph", "elements", "content", "body", "blocks"):
        child = node.get(key)
        if isinstance(child, list):
            for item in child:
                if isinstance(item, Mapping):
                    _walk_text_nodes(item, chunks)
        elif isinstance(child, Mapping):
            _walk_text_nodes(child, chunks)
