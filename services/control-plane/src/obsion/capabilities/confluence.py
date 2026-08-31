"""Confluence Cloud pages as a Knowledge source.

This is a Capability Fabric integration. Credentials never leave the connector
executor. Agents never receive Confluence tokens. IM Experience does not ingest
documents. Only Confluence Cloud hosts (`*.atlassian.net`) are permitted.
"""

from __future__ import annotations

import asyncio
import base64
import os
import re
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

CONFLUENCE_KNOWLEDGE_SOURCE = "confluence"
CONFLUENCE_PROTOCOL = "confluence.cloud.v2"
CONFLUENCE_CONNECTOR_TYPES = frozenset({"confluence", "confluence-http"})
CONFLUENCE_OPERATIONS = frozenset(
    {"knowledge.ingest", "knowledge.sync", *VENDOR_KNOWLEDGE_BROWSE_OPERATIONS}
)
DEFAULT_EMAIL_ENV = "OBSION_CONFLUENCE_EMAIL"
PAGE_PATH = "/wiki/api/v2/pages/{page_id}"
SPACES_PATH = "/wiki/api/v2/spaces"
SPACE_PAGES_PATH = "/wiki/api/v2/spaces/{space_id}/pages"
RESTRICTIONS_PATH = "/wiki/rest/api/content/{page_id}/restriction/byOperation"
MAX_RESPONSE_BYTES = 2_097_152
MAX_ATTEMPTS = 3
PAGE_SIZE = 25
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
SITE_HOST_PATTERN = re.compile(
    r"^(?=.{1,253}$)[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*\.atlassian\.net$"
)
PAGE_ID_PATTERN = re.compile(r"^[1-9][0-9]{0,19}$")
SPACE_ID_PATTERN = re.compile(r"^[1-9][0-9]{0,19}$")

Sleep = Callable[[float], Awaitable[None]]


class ConfluenceUnavailableError(ObsionError):
    def __init__(self, message: str = "The Confluence connector is unavailable") -> None:
        super().__init__("confluence_upstream_unavailable", message, status_code=503)


class ConfluenceResponseError(ObsionError):
    def __init__(
        self,
        message: str = "The Confluence connector returned an invalid response",
    ) -> None:
        super().__init__("confluence_response_invalid", message, status_code=503)


class ConfluenceDeniedError(AuthorizationError):
    def __init__(self, message: str = "Confluence denied the document fetch") -> None:
        super().__init__("confluence_upstream_denied", message)


@dataclass(frozen=True, slots=True)
class ConfluencePage:
    page_id: str
    title: str
    content: str
    version: str | None
    space_id: str | None
    inherited_acl: dict[str, Any] | None = None

    def as_metadata(self) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "confluence_page_id": self.page_id,
            "confluence_obj_type": "page",
        }
        if self.version:
            metadata["confluence_version"] = self.version
        if self.space_id:
            metadata["confluence_space_id"] = self.space_id
        return metadata


@dataclass(frozen=True, slots=True)
class ConfluenceSpace:
    space_id: str
    key: str
    name: str


@dataclass(frozen=True, slots=True)
class ConfluenceSpacePage:
    space_id: str
    page_id: str
    title: str
    status: str


def normalize_page_id(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not PAGE_ID_PATTERN.fullmatch(value.strip()):
        raise ValidationError(
            "confluence_page_id_invalid",
            "A valid Confluence Cloud page id is required",
        )
    return value.strip()


def normalize_space_id(value: Any) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str) or not SPACE_ID_PATTERN.fullmatch(value.strip()):
        raise ValidationError(
            "confluence_space_id_invalid",
            "A valid Confluence Cloud space id is required",
        )
    return value.strip()


def normalize_site_host(value: Any) -> str:
    host = value.strip().casefold() if isinstance(value, str) else ""
    labels = host.split(".")
    if not host or not SITE_HOST_PATTERN.fullmatch(host) or "localhost" in labels:
        raise ValidationError(
            "confluence_site_invalid",
            "Confluence connectors may only use a Cloud *.atlassian.net host",
        )
    return host


def is_confluence_connector(connector: Connector) -> bool:
    connector_type = connector.connector_type.casefold()
    configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
    protocol = configuration.get("protocol")
    return connector_type in CONFLUENCE_CONNECTOR_TYPES or (
        isinstance(protocol, str) and protocol.casefold() == CONFLUENCE_PROTOCOL
    )


def resolve_confluence_credentials(
    connector: Connector, credential: str | None
) -> tuple[str, str, str]:
    configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
    forbidden = {
        "token",
        "api_token",
        "apiToken",
        "password",
        "email",
        "secret",
    }
    if forbidden & set(configuration):
        raise ValidationError(
            "confluence_operation_invalid",
            "Confluence credentials cannot be stored on the connector configuration",
        )
    email_env = configuration.get("email_env", DEFAULT_EMAIL_ENV)
    if not isinstance(email_env, str) or not email_env.startswith("OBSION_"):
        raise ValidationError(
            "confluence_operation_invalid",
            "Confluence email must be referenced by an OBSION_ environment name",
        )
    email = os.environ.get(email_env, "").strip()
    token = (credential or "").strip()
    site_host = normalize_site_host(configuration.get("site_host"))
    if not email or not token:
        raise ValidationError(
            "credential_unavailable",
            "The Confluence connector credential is not available",
        )
    return email, token, site_host


def merge_restriction_members(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    read = payload.get("read")
    restrictions = read.get("restrictions") if isinstance(read, Mapping) else None
    if not isinstance(restrictions, Mapping):
        return None
    users: list[str] = []
    roles: list[str] = []
    user_block = restrictions.get("user")
    group_block = restrictions.get("group")
    user_results = user_block.get("results") if isinstance(user_block, Mapping) else None
    group_results = group_block.get("results") if isinstance(group_block, Mapping) else None
    if isinstance(user_results, list):
        for item in user_results:
            if not isinstance(item, Mapping):
                continue
            account_id = item.get("accountId") or item.get("account_id")
            if isinstance(account_id, str) and account_id.strip():
                users.append(account_id.strip())
    if isinstance(group_results, list):
        for item in group_results:
            if not isinstance(item, Mapping):
                continue
            name = item.get("name")
            if isinstance(name, str) and name.strip():
                roles.append(name.strip())
    if not users and not roles:
        return None
    return {
        "organization": False,
        "users": users,
        "roles": roles,
        "departments": [],
        "deny_users": [],
        "deny_roles": [],
        "deny_departments": [],
    }


class ConfluenceClient:
    """Bounded Confluence Cloud client. Secrets never leave this object."""

    def __init__(
        self,
        *,
        email: str,
        api_token: str,
        site_host: str,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._email = email
        self._api_token = api_token
        self._site_host = normalize_site_host(site_host)
        self._sleep = sleep
        token = base64.b64encode(f"{email}:{api_token}".encode()).decode("ascii")
        self._client = httpx.AsyncClient(
            base_url=f"https://{self._site_host}",
            transport=transport,
            timeout=httpx.Timeout(15.0, connect=5.0),
            follow_redirects=False,
            headers={
                "Accept": "application/json",
                "Authorization": f"Basic {token}",
            },
        )

    def __repr__(self) -> str:
        return "ConfluenceClient(authenticated=redacted)"

    async def health(self) -> dict[str, Any]:
        payload = await self._request_json("GET", SPACES_PATH, params={"limit": "1"})
        if not isinstance(payload.get("results"), list):
            raise ConfluenceResponseError("Confluence health response omitted results")
        return {
            "source": CONFLUENCE_KNOWLEDGE_SOURCE,
            "protocol": CONFLUENCE_PROTOCOL,
            "authenticated": True,
            "site_host": self._site_host,
        }

    async def fetch_page(self, *, page_id: str, inherit_acl: bool = False) -> ConfluencePage:
        token = normalize_page_id(page_id)
        payload = await self._request_json(
            "GET",
            PAGE_PATH.format(page_id=token),
            params={"body-format": "storage"},
        )
        title = payload.get("title")
        status = str(payload.get("status") or "")
        if status and status != "current":
            raise ValidationError(
                "confluence_page_id_invalid",
                "Only current Confluence pages can enter the Knowledge pipeline",
                status=status,
            )
        content = _page_content(payload)
        version = _page_version(payload)
        space_id = payload.get("spaceId") or payload.get("space_id")
        resolved_space = None
        if space_id is not None:
            resolved_space = normalize_space_id(space_id)
        inherited_acl = None
        if inherit_acl:
            members = await self._request_json(
                "GET",
                RESTRICTIONS_PATH.format(page_id=token),
            )
            inherited_acl = merge_restriction_members(members)
        return ConfluencePage(
            page_id=token,
            title=title.strip() if isinstance(title, str) and title.strip() else token,
            content=content,
            version=version,
            space_id=resolved_space,
            inherited_acl=inherited_acl,
        )

    async def list_spaces(
        self,
        *,
        tracker: SyncBudgetTracker | None = None,
    ) -> list[ConfluenceSpace]:
        items = await self._paginate(SPACES_PATH, tracker=tracker)
        spaces: list[ConfluenceSpace] = []
        for item in items:
            space_id = _required_id(item.get("id"), field="space id")
            key = item.get("key")
            name = item.get("name")
            spaces.append(
                ConfluenceSpace(
                    space_id=space_id,
                    key=key.strip() if isinstance(key, str) and key.strip() else space_id,
                    name=name.strip() if isinstance(name, str) and name.strip() else space_id,
                )
            )
        return spaces

    async def list_pages(
        self,
        space_id: str,
        *,
        tracker: SyncBudgetTracker | None = None,
    ) -> list[ConfluenceSpacePage]:
        resolved = normalize_space_id(space_id)
        budget = tracker or SyncBudgetTracker(KnowledgeConnectorBudget())
        items = await self._paginate(
            SPACE_PAGES_PATH.format(space_id=resolved),
            tracker=budget,
        )
        pages: list[ConfluenceSpacePage] = []
        for item in items:
            page_id = _required_id(item.get("id"), field="page id")
            title = item.get("title")
            status = item.get("status")
            budget.consume_node()
            pages.append(
                ConfluenceSpacePage(
                    space_id=resolved,
                    page_id=page_id,
                    title=title.strip() if isinstance(title, str) and title.strip() else page_id,
                    status=str(status or "current"),
                )
            )
        return pages

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _paginate(
        self,
        path: str,
        *,
        tracker: SyncBudgetTracker | None = None,
    ) -> list[Mapping[str, Any]]:
        budget = tracker or SyncBudgetTracker(KnowledgeConnectorBudget())
        collected: list[Mapping[str, Any]] = []
        next_path = path
        params: dict[str, str] | None = {"limit": str(PAGE_SIZE)}
        while True:
            budget.consume_page()
            payload = await self._request_json("GET", next_path, params=params)
            results = payload.get("results")
            if results is None:
                results = []
            if not isinstance(results, list):
                raise ConfluenceResponseError("Confluence list response results must be a list")
            for item in results:
                if not isinstance(item, Mapping):
                    raise ConfluenceResponseError("Confluence list response returned a non-object")
                collected.append(item)
            nxt = _next_path(payload, site_host=self._site_host)
            if nxt is None:
                return collected
            next_path = nxt
            params = None

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        last_transport_error: httpx.TransportError | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await self._client.request(method, path, params=params)
            except httpx.TransportError as exc:
                last_transport_error = exc
                if attempt + 1 == MAX_ATTEMPTS:
                    break
                await self._sleep(0.25 * (2**attempt))
                continue
            if response.status_code in RETRYABLE_STATUS_CODES and attempt + 1 < MAX_ATTEMPTS:
                await self._sleep(_retry_delay(response, attempt))
                continue
            if response.status_code in {401, 403}:
                raise ConfluenceDeniedError("Confluence denied the document request")
            if response.status_code < 200 or response.status_code >= 300:
                raise ConfluenceUnavailableError(
                    f"Confluence HTTP request failed with status {response.status_code}"
                )
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise ConfluenceResponseError("Confluence HTTP response exceeded the size limit")
            try:
                payload = response.json()
            except ValueError as exc:
                raise ConfluenceResponseError(
                    "Confluence HTTP response was not valid JSON"
                ) from exc
            if not isinstance(payload, dict):
                raise ConfluenceResponseError("Confluence HTTP response must be a JSON object")
            return payload
        if last_transport_error is not None:
            raise ConfluenceUnavailableError(
                "Confluence HTTP request failed after bounded retries"
            ) from last_transport_error
        raise ConfluenceUnavailableError("Confluence HTTP request failed after bounded retries")


async def fetch_authorized_confluence_page(
    *,
    email: str,
    api_token: str,
    site_host: str,
    page_id: str,
    inherit_acl: bool = False,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ConfluencePage:
    client = ConfluenceClient(
        email=email,
        api_token=api_token,
        site_host=site_host,
        transport=transport,
    )
    try:
        return await client.fetch_page(page_id=page_id, inherit_acl=inherit_acl)
    finally:
        await client.aclose()


def assert_confluence_egress(connector: Connector) -> None:
    configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
    if configuration.get("base_url") or configuration.get("baseUrl"):
        raise ValidationError(
            "confluence_operation_invalid",
            "Confluence connectors cannot override the Cloud origin",
        )
    site_host = normalize_site_host(configuration.get("site_host"))
    origin = f"https://{site_host}"
    endpoint = connector.endpoint or origin
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or parsed.hostname != site_host:
        raise ValidationError(
            "connector_egress_denied",
            "Confluence connectors may only call their Cloud site origin",
        )
    if parsed.username is not None or parsed.password is not None:
        raise ValidationError(
            "connector_egress_denied",
            "Confluence connectors may only call their Cloud site origin",
        )
    try:
        allowed = {_authority(item) for item in connector.allowed_egress if isinstance(item, str)}
        expected = _authority(origin)
    except ValueError as exc:
        raise ValidationError(
            "connector_egress_invalid",
            "Connector egress configuration is invalid",
        ) from exc
    if expected not in allowed:
        raise ValidationError(
            "connector_egress_denied",
            "Confluence connectors may only call their Cloud site origin",
        )


def _required_id(value: Any, *, field: str) -> str:
    if isinstance(value, bool):
        raise ConfluenceResponseError(f"Confluence response omitted {field}")
    if isinstance(value, int):
        value = str(value)
    if not isinstance(value, str) or not value.strip():
        raise ConfluenceResponseError(f"Confluence response omitted {field}")
    if field == "page id":
        return normalize_page_id(value)
    if field == "space id":
        return normalize_space_id(value)
    return value.strip()


def _page_content(payload: Mapping[str, Any]) -> str:
    body = payload.get("body")
    storage = body.get("storage") if isinstance(body, Mapping) else None
    content = storage.get("value") if isinstance(storage, Mapping) else None
    if not isinstance(content, str) or not content.strip():
        raise ValidationError("document_empty", "The Confluence page contains no extractable text")
    return content.replace("\x00", "").replace("\r\n", "\n")


def _page_version(payload: Mapping[str, Any]) -> str | None:
    version = payload.get("version")
    number = version.get("number") if isinstance(version, Mapping) else None
    if number is None:
        return None
    return str(number)


def _next_path(payload: Mapping[str, Any], *, site_host: str) -> str | None:
    links = payload.get("_links")
    raw = None
    if isinstance(links, Mapping):
        raw = links.get("next")
    if raw is None:
        raw = payload.get("next")
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = raw.strip()
    if candidate.startswith("/"):
        return candidate
    parsed = urlparse(candidate)
    if parsed.scheme != "https" or parsed.hostname != site_host:
        raise ConfluenceResponseError("Confluence pagination left the Cloud site origin")
    path = parsed.path or "/"
    if parsed.query:
        return f"{path}?{parsed.query}"
    return path


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
