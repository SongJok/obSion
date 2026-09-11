"""Read-only Alibaba Cloud Yunxiao Codeup repository discovery.

The official Yunxiao MCP integration sends a personal access token (PAT) as
``X-Yunxiao-Token`` when it calls Yunxiao Codeup REST. This adapter retains only
the repository discovery contract, and runs it behind the Capability Gateway.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from obsion.common.errors import ValidationError
from obsion.db.models import Connector
from obsion.security.redaction import redact_text

YUNXIAO_PROTOCOL = "yunxiao.devops.v1"
YUNXIAO_CONNECTOR_TYPES = frozenset({"yunxiao", "yunxiao-http"})
YUNXIAO_OPERATIONS = frozenset({"yunxiao.repositories.list"})
YUNXIAO_REPOSITORY_PATH = "/oapi/v1/codeup/repositories"
YUNXIAO_ORGANIZATIONS_PATH = "/oapi/v1/platform/organizations"
YUNXIAO_CENTRAL_HOST = "openapi-rdc.aliyuncs.com"
MAX_PAGE = 10_000
MAX_PER_PAGE = 100
MAX_SEARCH_LENGTH = 200
MAX_RESPONSE_BYTES = 2_097_152
MAX_ORGANIZATIONS = 100
_ORGANIZATION_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_INLINE_CREDENTIAL_KEY = re.compile(
    r"(?:^|[_-])(?:credential|secret|token|password|api[_-]?key|access[_-]?key)(?:$|[_-])"
    r"|(?:credential|secret|token|password|apiKey|accessKey)$",
    re.IGNORECASE,
)


class YunxiaoUnavailableError(RuntimeError):
    """The upstream API was unavailable without exposing its response details."""


class YunxiaoResponseError(RuntimeError):
    """The upstream response was rejected without exposing vendor-controlled text."""


def is_yunxiao_connector(connector: Connector) -> bool:
    connector_type = connector.connector_type.casefold()
    configuration = connector.configuration if isinstance(connector.configuration, dict) else {}
    protocol = configuration.get("protocol")
    return connector_type in YUNXIAO_CONNECTOR_TYPES or (
        isinstance(protocol, str) and protocol.casefold() == YUNXIAO_PROTOCOL
    )


def assert_yunxiao_egress(connector: Connector) -> None:
    """Require an operator-pinned HTTPS origin and fixed Codeup API paths."""

    configuration = _configuration(connector)
    if any(_INLINE_CREDENTIAL_KEY.search(str(key)) for key in configuration):
        raise ValidationError(
            "credential_unavailable",
            "Yunxiao credentials must be supplied through a credential reference",
        )
    if any(key in configuration for key in ("base_url", "baseUrl", "path", "operation_paths")):
        raise ValidationError(
            "connector_egress_denied",
            "Yunxiao repository discovery uses the configured HTTPS origin and fixed API paths",
        )
    endpoint = (connector.endpoint or "").strip()
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValidationError(
            "connector_egress_denied",
            "Yunxiao repository discovery requires an exact HTTPS base origin",
        )
    if parsed.hostname.casefold() == YUNXIAO_CENTRAL_HOST and (parsed.port or 443) != 443:
        raise ValidationError(
            "connector_egress_denied",
            "The central Yunxiao origin must use its standard HTTPS authority",
        )
    try:
        expected = _https_authority(endpoint)
        allowed = {_https_authority(item) for item in connector.allowed_egress}
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            "connector_egress_invalid", "Connector egress configuration is invalid"
        ) from exc
    if expected not in allowed:
        raise ValidationError(
            "connector_egress_denied",
            "Yunxiao repository discovery origin is outside its egress allowlist",
        )


def resolve_yunxiao_credentials(connector: Connector, credential: str | None) -> str:
    """Return an opaque PAT after rejecting inline credential configuration."""

    configuration = _configuration(connector)
    if any(_INLINE_CREDENTIAL_KEY.search(str(key)) for key in configuration):
        raise ValidationError(
            "credential_unavailable",
            "Yunxiao credentials must be supplied through a credential reference",
        )
    if not isinstance(credential, str) or not credential.strip():
        raise ValidationError("credential_unavailable", "The Yunxiao credential is not available")
    if credential.lstrip().startswith(("{", "[")):
        try:
            json.loads(credential)
        except json.JSONDecodeError:
            pass
        else:
            raise ValidationError(
                "credential_unavailable", "The Yunxiao credential has an invalid secret format"
            )
    return credential.strip()


class YunxiaoClient:
    """Minimal PAT-authenticated repository discovery client."""

    def __init__(
        self,
        connector: Connector,
        token: str,
        *,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._connector = connector
        self._token = token
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def list_repositories(
        self,
        *,
        page: int = 1,
        per_page: int = 50,
        search: str | None = None,
    ) -> dict[str, Any]:
        _validate_page(page, per_page, search)
        endpoint = self._connector.endpoint or ""
        organizations = await self._organizations(endpoint)
        first_pages: dict[str, dict[str, Any]] = {}
        total = 0
        for organization_id in organizations:
            payload = await self._get_json(
                _repository_url(endpoint, organization_id),
                params=_repository_params(
                    endpoint,
                    organization_id,
                    page=1,
                    per_page=per_page,
                    search=search,
                ),
            )
            normalized = normalize_repository_response(payload, page=1, per_page=per_page)
            first_pages[organization_id] = normalized
            total += normalized["total"]

        offset = (page - 1) * per_page
        remaining = per_page
        items: list[dict[str, Any]] = []
        for organization_id in organizations:
            organization_total = first_pages[organization_id]["total"]
            if offset >= organization_total:
                offset -= organization_total
                continue
            local_offset = offset
            offset = 0
            while remaining and local_offset < organization_total:
                local_page = local_offset // per_page + 1
                page_offset = local_offset % per_page
                normalized = first_pages[organization_id]
                if local_page != 1:
                    payload = await self._get_json(
                        _repository_url(endpoint, organization_id),
                        params=_repository_params(
                            endpoint,
                            organization_id,
                            page=local_page,
                            per_page=per_page,
                            search=search,
                        ),
                    )
                    normalized = normalize_repository_response(
                        payload, page=local_page, per_page=per_page
                    )
                    if normalized["total"] != organization_total:
                        raise YunxiaoResponseError("Yunxiao repository total changed during paging")
                page_items = normalized["items"][page_offset : page_offset + remaining]
                items.extend({"organization_id": organization_id, **item} for item in page_items)
                consumed = len(page_items)
                remaining -= consumed
                if consumed == 0:
                    raise YunxiaoResponseError(
                        "Yunxiao repository response ended before its declared total"
                    )
                local_offset += consumed
            if not remaining:
                break
        result = {
            "operation": "yunxiao.repositories.list",
            "items": items,
            "count": len(items),
            "total": total,
            "page": page,
            "per_page": per_page,
            "next_page": page + 1 if page * per_page < total else None,
        }
        if result["next_page"] is not None and page == MAX_PAGE:
            raise YunxiaoResponseError("Yunxiao repository pagination exceeded the page limit")
        if len({(item["organization_id"], item["id"]) for item in items}) != len(items):
            raise YunxiaoResponseError("Yunxiao repository pages contain duplicate identifiers")
        return _scrub_credential(result, self._token)

    async def _organizations(self, endpoint: str) -> list[str]:
        configured = _configured_organization_ids(_configuration(self._connector))
        if configured:
            return configured
        if _is_central_endpoint(endpoint):
            payload = await self._get_json(
                _organizations_url(endpoint), params={"page": "1", "perPage": "100"}
            )
            # The discovery contract admits at most 100 organizations. Never
            # silently accept the first page of a larger organization set.
            rows, total = _page_values(payload, page=1, per_page=MAX_ORGANIZATIONS)
            if total > MAX_ORGANIZATIONS:
                raise YunxiaoResponseError("Yunxiao organization discovery exceeded its limit")
            return _organization_ids(rows)
        raise ValidationError(
            "capability_input_invalid",
            "A regional Yunxiao connector requires explicit organization_ids",
        )

    async def _get_json(
        self, url: str, *, params: Mapping[str, str] | None = None
    ) -> dict[str, Any] | list[Any]:
        try:
            async with (
                httpx.AsyncClient(
                    timeout=self._timeout_seconds,
                    follow_redirects=False,
                    trust_env=False,
                    transport=self._transport,
                ) as client,
                client.stream(
                    "GET",
                    url,
                    params=params,
                    headers={
                        "Accept": "application/json",
                        "X-Yunxiao-Token": self._token,
                        "Accept-Encoding": "identity",
                    },
                ) as response,
            ):
                if response.status_code >= 500:
                    raise YunxiaoUnavailableError("Yunxiao repository discovery is unavailable")
                if response.status_code != 200:
                    raise YunxiaoResponseError("Yunxiao rejected repository discovery")
                if (
                    response.headers.get("content-encoding", "identity").strip().casefold()
                    != "identity"
                ):
                    raise YunxiaoResponseError("Yunxiao response encoding is unsupported")
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65_536):
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise YunxiaoResponseError(
                            "Yunxiao repository response exceeded the size limit"
                        )
                    body.extend(chunk)
        except (httpx.TimeoutException, httpx.TransportError, OSError) as exc:
            raise YunxiaoUnavailableError("Yunxiao repository discovery is unavailable") from exc
        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise YunxiaoResponseError("Yunxiao repository response was not valid JSON") from exc
        if not isinstance(payload, (dict, list)):
            raise YunxiaoResponseError("Yunxiao repository response was not structured JSON")
        return _with_pagination_headers(payload, response.headers, params or {})


def _with_pagination_headers(
    payload: dict[str, Any] | list[Any], headers: httpx.Headers, params: Mapping[str, str]
) -> dict[str, Any]:
    """Central OpenAPI returns arrays and carries totals in HTTP headers."""
    result: dict[str, Any] = dict(payload) if isinstance(payload, dict) else {"data": payload}
    page = int(params.get("page", "1"))
    per_page = int(params.get("perPage", "100"))
    for key, expected in (("x-page", page), ("x-per-page", per_page)):
        if key in headers and _header_integer(headers[key]) != expected:
            raise YunxiaoResponseError("Yunxiao pagination does not match the request")
    if "x-total" in headers:
        total = _header_integer(headers["x-total"])
        if "total" in result and (type(result["total"]) is not int or result["total"] != total):
            raise YunxiaoResponseError("Yunxiao pagination totals conflict")
        result["total"] = total
    _, total = _page_values(result, page=page, per_page=per_page)
    if (
        "x-total-pages" in headers
        and _header_integer(headers["x-total-pages"]) != (total + per_page - 1) // per_page
    ):
        raise YunxiaoResponseError("Yunxiao pagination totals conflict")
    if "x-next-page" in headers:
        next_page = _header_integer(headers["x-next-page"]) if headers["x-next-page"] else 0
        if page * per_page < total:
            if next_page != page + 1:
                raise YunxiaoResponseError("Yunxiao pagination continuation is inconsistent")
        elif next_page not in {0, page}:
            # The production Codeup endpoint reports the current page on the
            # final page; accept that equivalent terminal marker, but reject
            # any value that could hide an unrequested continuation.
            raise YunxiaoResponseError("Yunxiao pagination continuation is inconsistent")
    result["total"] = total
    return result


def _header_integer(value: str) -> int:
    if not re.fullmatch(r"[0-9]{1,12}", value):
        raise YunxiaoResponseError("Yunxiao pagination header is invalid")
    return int(value)


def _page_values(payload: Any, *, page: int, per_page: int) -> tuple[list[Any], int]:
    total = None
    if isinstance(payload, Mapping):
        if payload.get("success") is False:
            raise YunxiaoResponseError("Yunxiao rejected repository discovery")
        rows = payload.get("result", payload.get("data"))
        if "total" in payload:
            total = payload["total"]
            if type(total) is not int or total < 0:
                raise YunxiaoResponseError("Yunxiao pagination total is invalid")
    else:
        rows = payload
    if not isinstance(rows, list) or len(rows) > per_page:
        raise YunxiaoResponseError("Yunxiao repository response has an invalid result page")
    if total is None:
        # Preserve unpaginated legacy responses, but a full page is never a total.
        if page != 1 or len(rows) == per_page:
            raise YunxiaoResponseError("Yunxiao pagination total is unavailable")
        total = len(rows)
    expected = min(per_page, max(0, total - (page - 1) * per_page))
    if len(rows) != expected:
        raise YunxiaoResponseError("Yunxiao response size disagrees with its total")
    return rows, total


def _scrub_credential(value: dict[str, Any], credential: str) -> dict[str, Any]:
    def visit(item: Any) -> Any:
        if isinstance(item, str):
            return item.replace(credential, "[REDACTED]")
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, dict):
            return {key: visit(child) for key, child in item.items()}
        return item

    result: dict[str, Any] = visit(value)
    return result


def normalize_repository_response(payload: Any, *, page: int, per_page: int) -> dict[str, Any]:
    """Reduce one organization response to bounded read-only repository metadata."""

    _validate_page(page, per_page, None)
    result, total = _page_values(payload, page=page, per_page=per_page)
    try:
        items = [_normalize_repository(item) for item in result]
    except (TypeError, ValueError) as exc:
        raise YunxiaoResponseError("Yunxiao repository response contains an invalid row") from exc
    if len({item["id"] for item in items}) != len(items):
        raise YunxiaoResponseError("Yunxiao repository response contains duplicate identifiers")
    return {
        "operation": "yunxiao.repositories.list",
        "items": items,
        "count": len(items),
        "total": total,
        "page": page,
        "per_page": per_page,
        "next_page": page + 1 if page * per_page < total else None,
    }


def _organization_ids(payload: dict[str, Any] | list[Any]) -> list[str]:
    values: Any = payload
    if isinstance(payload, Mapping):
        values = payload.get("result", payload.get("data"))
    if not isinstance(values, list) or len(values) > MAX_ORGANIZATIONS:
        raise YunxiaoResponseError("Yunxiao organization discovery returned an invalid result")
    identifiers: list[str] = []
    for item in values:
        if not isinstance(item, Mapping):
            raise YunxiaoResponseError("Yunxiao organization discovery contains an invalid row")
        value = item.get("id", item.get("organizationId", item.get("organization_id")))
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise YunxiaoResponseError("Yunxiao organization discovery omitted an identifier")
        candidate = str(value).strip()
        if not _ORGANIZATION_PATTERN.fullmatch(candidate):
            raise YunxiaoResponseError("Yunxiao organization identifier is invalid")
        if candidate not in identifiers:
            identifiers.append(candidate)
    return identifiers


def _normalize_repository(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("repository must be an object")
    return {
        "id": _required_string(value.get("id", value.get("Id"))),
        "name": _required_string(value.get("name")),
        "path": _optional_string(value.get("path")),
        "path_with_namespace": _optional_string(
            value.get("pathWithNamespace", value.get("path_with_namespace"))
        ),
        "namespace_id": _optional_string(value.get("namespaceId", value.get("namespace_id"))),
        "visibility": _optional_string(value.get("visibilityLevel", value.get("visibility"))),
        "archived": _optional_bool(value.get("archive", value.get("archived"))),
        "access_level": _optional_integer(value.get("accessLevel", value.get("access_level"))),
        "description": _safe_description(value.get("description")),
        "web_url": _safe_url(value.get("webUrl", value.get("web_url"))),
        "created_at": _optional_string(value.get("createdAt", value.get("created_at"))),
        "updated_at": _optional_string(value.get("updatedAt", value.get("updated_at"))),
        "last_activity_at": _optional_string(
            value.get("lastActivityAt", value.get("last_activity_at"))
        ),
        "star_count": _optional_integer(value.get("starCount", value.get("star_count"))),
    }


def _configuration(connector: Connector) -> dict[str, Any]:
    if not isinstance(connector.configuration, dict):
        raise ValidationError(
            "capability_input_invalid", "Yunxiao connector configuration is invalid"
        )
    protocol = connector.configuration.get("protocol")
    if not isinstance(protocol, str) or protocol.casefold() != YUNXIAO_PROTOCOL:
        raise ValidationError("capability_input_invalid", "Yunxiao connector protocol is invalid")
    return connector.configuration


def _configured_organization_ids(configuration: Mapping[str, Any]) -> list[str]:
    value = configuration.get("organization_ids")
    if value is None:
        return []
    if not isinstance(value, list) or not value or len(value) > MAX_ORGANIZATIONS:
        raise ValidationError(
            "capability_input_invalid", "Yunxiao connector organization_ids are invalid"
        )
    identifiers: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _ORGANIZATION_PATTERN.fullmatch(item.strip()):
            raise ValidationError(
                "capability_input_invalid", "Yunxiao connector organization_ids are invalid"
            )
        if item.strip() not in identifiers:
            identifiers.append(item.strip())
    return identifiers


def _https_authority(value: Any) -> tuple[str, int]:
    if not isinstance(value, str):
        raise ValueError("invalid authority")
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("invalid authority")
    return parsed.hostname.casefold(), parsed.port or 443


def _is_central_endpoint(endpoint: str) -> bool:
    parsed = urlsplit(endpoint)
    return (
        parsed.hostname is not None
        and parsed.hostname.casefold() == YUNXIAO_CENTRAL_HOST
        and (parsed.port or 443) == 443
    )


def _organizations_url(endpoint: str) -> str:
    parsed = urlsplit(endpoint.strip())
    return urlunsplit(("https", parsed.netloc, YUNXIAO_ORGANIZATIONS_PATH, "", ""))


def _repository_url(endpoint: str, organization_id: str) -> str:
    parsed = urlsplit(endpoint.strip())
    path = YUNXIAO_REPOSITORY_PATH
    if _is_central_endpoint(endpoint):
        path = f"/oapi/v1/codeup/organizations/{quote(organization_id, safe='')}/repositories"
    return urlunsplit(("https", parsed.netloc, path, "", ""))


def _repository_params(
    endpoint: str,
    organization_id: str,
    *,
    page: int,
    per_page: int,
    search: str | None,
) -> dict[str, str]:
    params = {"page": str(page), "perPage": str(per_page), "orderBy": "path", "sort": "asc"}
    if not _is_central_endpoint(endpoint):
        params["organizationId"] = organization_id
    if search is not None:
        params["search"] = search.strip()
    return params


def _validate_page(page: int, per_page: int, search: str | None) -> None:
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= MAX_PAGE:
        raise ValidationError(
            "capability_input_invalid", "Yunxiao page is outside the supported range"
        )
    if (
        isinstance(per_page, bool)
        or not isinstance(per_page, int)
        or not 1 <= per_page <= MAX_PER_PAGE
    ):
        raise ValidationError(
            "capability_input_invalid", "Yunxiao per_page is outside the supported range"
        )
    if search is not None and (
        not isinstance(search, str) or not search.strip() or len(search) > MAX_SEARCH_LENGTH
    ):
        raise ValidationError(
            "capability_input_invalid", "Yunxiao search is outside the supported range"
        )


def _required_string(value: Any) -> str:
    result = _optional_string(value)
    if result is None:
        raise ValueError("missing string")
    return result


def _optional_string(value: Any) -> str | None:
    if isinstance(value, str):
        result = value.strip()
        return result or None
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return None


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_integer(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _safe_description(value: Any) -> str | None:
    text = _optional_string(value)
    return redact_text(text)[:2_000] if text is not None else None


def _safe_url(value: Any) -> str | None:
    text = _optional_string(value)
    if text is None:
        return None
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
