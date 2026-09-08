"""Codeup Central OpenAPI reads through the existing Capability Gateway.

Only a fixed HTTPS origin and GET routes are supported. Local repository ACL and
provider read authorization must both hold; a successful read does not attest the
token's complete scopes or authorize a sandbox checkout.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote
from uuid import UUID

import httpx
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.capabilities.codeup_contract import (
    CODEUP_CATALOG_PROTOCOL,
    CODEUP_DISCOVERY_OPERATION,
    CODEUP_OPERATIONS,
    CODEUP_ORIGIN,
    CODEUP_PROTOCOL,
    MAX_FILE_BYTES,
    MAX_RESPONSE_BYTES,
    input_schema,
)
from obsion.code_intelligence.service import repository_access_clause
from obsion.common.errors import AuthorizationError, ObsionError, ValidationError
from obsion.db.models import CodeRepository, Connector, Organization
from obsion.domain.enums import ConnectorStatus
from obsion.security.auth import load_principal_by_id
from obsion.security.identity import Principal
from obsion.security.redaction import redact_text

_NAME = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+")
_ID = re.compile(r"[A-Za-z0-9_-]{1,100}")
_SHA = re.compile(r"[a-f0-9]{40}")
_TOKEN = re.compile(r"\bpt-[A-Za-z0-9_-]{32,}\b")


@dataclass(frozen=True, slots=True)
class CodeupRepository:
    name: str
    local_id: UUID
    remote_id: str
    organization: str


@dataclass(frozen=True, slots=True)
class CodeupCatalog:
    connector_id: UUID
    organization: str


def is_codeup_connector(connector: Connector) -> bool:
    return connector.connector_type.casefold() == "codeup" or (
        isinstance(connector.configuration, dict)
        and connector.configuration.get("protocol") == CODEUP_PROTOCOL
    )


def is_codeup_catalog_connector(connector: Connector) -> bool:
    return connector.connector_type.casefold() == "codeup-catalog" or (
        isinstance(connector.configuration, dict)
        and connector.configuration.get("protocol") == CODEUP_CATALOG_PROTOCOL
    )


def _valid_rate_limit(config: dict[str, Any]) -> bool:
    return "rate_limit_per_minute" not in config or (
        type(config["rate_limit_per_minute"]) is int and 1 <= config["rate_limit_per_minute"] <= 600
    )


def _valid_origin(connector: Connector) -> bool:
    return (
        connector.endpoint == CODEUP_ORIGIN
        and isinstance(connector.allowed_egress, list)
        and bool(connector.allowed_egress)
        and all(
            isinstance(value, str)
            and value in {CODEUP_ORIGIN, "openapi-rdc.aliyuncs.com", "openapi-rdc.aliyuncs.com:443"}
            for value in connector.allowed_egress
        )
    )


def catalog_configuration(connector: Connector) -> CodeupCatalog:
    config = connector.configuration
    if (
        not isinstance(config, dict)
        or connector.connector_type.casefold() != "codeup-catalog"
        or set(config) - {"protocol", "organization_id", "rate_limit_per_minute"}
        or config.get("protocol") != CODEUP_CATALOG_PROTOCOL
        or not _valid_origin(connector)
        or connector.declared_grants != ["connectors.read"]
        or not _valid_rate_limit(config)
        or not isinstance(config.get("organization_id"), str)
        or not _ID.fullmatch(config["organization_id"])
    ):
        raise ValidationError(
            "codeup_configuration_invalid",
            "云效目录连接配置无效，请检查组织 ID、固定接入点和只读授权。",
        )
    return CodeupCatalog(connector.id, config["organization_id"])


def repository_configuration(connector: Connector, name: Any) -> CodeupRepository:
    config = connector.configuration
    allowed_keys = {
        "protocol",
        "organization_id",
        "repositories",
        "allowed_repositories",
        "rate_limit_per_minute",
    }
    if (
        not isinstance(config, dict)
        or set(config) - allowed_keys
        or config.get("protocol") != CODEUP_PROTOCOL
        or not _valid_origin(connector)
        or connector.declared_grants != ["code.read"]
        or not _valid_rate_limit(config)
    ):
        raise ValidationError(
            "codeup_configuration_invalid",
            "云效连接配置无效，请检查固定接入点、仓库映射和只读授权。",
        )
    organization = config.get("organization_id")
    repositories = config.get("repositories")
    allowed = config.get("allowed_repositories")
    if (
        not isinstance(organization, str)
        or not _ID.fullmatch(organization)
        or not isinstance(repositories, dict)
        or not 1 <= len(repositories) <= 100
        or not isinstance(allowed, list)
        or not all(isinstance(item, str) for item in allowed)
        or len(set(allowed)) != len(allowed)
        or set(allowed) != set(repositories)
        or any(
            not isinstance(key, str)
            or len(key) > 240
            or not _NAME.fullmatch(key)
            or any(part in {".", ".."} for part in key.split("/"))
            for key in repositories
        )
    ):
        raise ValidationError(
            "codeup_configuration_invalid",
            "请配置云效组织 ID，并逐个绑定已授权的本地仓库与云效仓库 ID。",
        )
    for mapping in repositories.values():
        if (
            not isinstance(mapping, dict)
            or set(mapping) != {"id", "repository_id"}
            or not isinstance(mapping["id"], str)
            or not re.fullmatch(r"[1-9][0-9]{0,19}", mapping["id"])
            or not isinstance(mapping["repository_id"], str)
        ):
            raise ValidationError("codeup_configuration_invalid", "云效仓库映射无效。")
        try:
            UUID(mapping["repository_id"])
        except ValueError:
            raise ValidationError("codeup_configuration_invalid", "本地仓库 ID 无效。") from None
    if not isinstance(name, str) or name not in repositories:
        raise AuthorizationError("codeup_repository_denied", "该仓库未获授权，请联系项目管理员。")
    selected = repositories[name]
    return CodeupRepository(name, UUID(selected["repository_id"]), selected["id"], organization)


async def repository_allowed(
    session: AsyncSession,
    principal: Principal,
    connector: Connector,
    payload: dict[str, Any],
    resource: dict[str, Any],
) -> bool:
    """Additional resource ACL constraint, never a replacement for Policy evaluation."""
    try:
        repository = repository_configuration(connector, payload.get("repository"))
        current = await load_principal_by_id(session, principal.organization_id, principal.id)
    except ObsionError:
        return False
    if (
        connector.organization_id != current.organization_id
        or connector.status != ConnectorStatus.ACTIVE
        or not current.can("code.read")
        or resource.get("repository") != repository.name
    ):
        return False
    await session.flush()
    # Read columns rather than refreshing the ORM instance: a changed mapping or
    # credential must invalidate the in-flight read, not overwrite its old scope.
    if not await _connector_unchanged(session, connector, current.organization_id):
        return False
    return bool(
        await session.scalar(
            select(CodeRepository.id)
            .join(Organization, Organization.id == CodeRepository.organization_id)
            .where(
                CodeRepository.id == repository.local_id,
                CodeRepository.organization_id == current.organization_id,
                CodeRepository.name == repository.name,
                Organization.active.is_(True),
                repository_access_clause(current),
            )
        )
    )


async def _connector_unchanged(
    session: AsyncSession, connector: Connector, organization_id: UUID
) -> bool:
    snapshot = await session.execute(
        select(
            Connector.status,
            Connector.environment,
            Connector.endpoint,
            Connector.credential_ref,
            Connector.configuration,
            Connector.declared_grants,
            Connector.allowed_egress,
            Connector.connector_type,
        ).where(Connector.id == connector.id, Connector.organization_id == organization_id)
    )
    return snapshot.one_or_none() == (
        ConnectorStatus.ACTIVE,
        connector.environment,
        connector.endpoint,
        connector.credential_ref,
        connector.configuration,
        connector.declared_grants,
        connector.allowed_egress,
        connector.connector_type,
    )


async def catalog_allowed(
    session: AsyncSession,
    principal: Principal,
    connector: Connector,
    payload: dict[str, Any],
    resource: dict[str, Any],
) -> bool:
    try:
        catalog = catalog_configuration(connector)
        current = await load_principal_by_id(session, principal.organization_id, principal.id)
    except ObsionError:
        return False
    if (
        connector.organization_id != current.organization_id
        or connector.status != ConnectorStatus.ACTIVE
        or not current.can("connectors.read")
        or payload.get("operation") != CODEUP_DISCOVERY_OPERATION
        or resource.get("connector_id") != str(catalog.connector_id)
        or resource.get("source") != "codeup-catalog"
    ):
        return False
    await session.flush()
    organization_active = await session.scalar(
        select(Organization.active).where(Organization.id == current.organization_id)
    )
    return bool(organization_active) and await _connector_unchanged(
        session, connector, current.organization_id
    )


def _request(repository: CodeupRepository, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    operation = payload.get("operation")
    if not isinstance(operation, str) or operation not in CODEUP_OPERATIONS:
        raise ValidationError("codeup_operation_invalid", "云效连接只支持已登记的查询操作。")
    try:
        Draft202012Validator(input_schema(operation)).validate(payload)
    except SchemaError:
        raise ValidationError("codeup_operation_invalid", "云效查询参数不符合能力契约。") from None
    prefix = (
        f"/oapi/v1/codeup/organizations/{repository.organization}"
        f"/repositories/{repository.remote_id}"
    )
    if operation == "codeup.repository.get":
        return prefix, {}
    if operation == "codeup.commit.get":
        return f"{prefix}/commits/{payload['commit_id']}", {}
    if operation == "codeup.file.read":
        path = payload["path"]
        parsed = PurePosixPath(path)
        if (
            parsed.is_absolute()
            or str(parsed) != path
            or ".." in parsed.parts
            or not parsed.parts
            or any(ord(char) < 32 for char in path)
            or "\\" in path
            or "%" in path
            or "?" in path
            or "#" in path
            or any(part.casefold() in {".git", ".ssh", ".env"} for part in parsed.parts)
            or parsed.name.casefold().startswith(".env.")
            or parsed.suffix.casefold() in {".pem", ".key", ".p12", ".pfx"}
        ):
            raise ValidationError("codeup_operation_invalid", "该文件路径不允许由机器人读取。")
        return f"{prefix}/files/{quote(path, safe='')}", {"ref": payload["commit_id"]}
    ref = payload["ref"]
    if any(ord(char) < 32 for char in ref) or ref.startswith("-"):
        raise ValidationError("codeup_operation_invalid", "请提供有效的分支、标签或提交引用。")
    return f"{prefix}/commits", {
        "refName": ref,
        "page": payload.get("page", 1),
        "perPage": payload.get("limit", 20),
    }


def _catalog_request(catalog: CodeupCatalog, payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    try:
        Draft202012Validator(input_schema(CODEUP_DISCOVERY_OPERATION)).validate(payload)
    except SchemaError:
        raise ValidationError(
            "codeup_operation_invalid", "云效目录查询参数不符合能力契约。"
        ) from None
    search = payload.get("search")
    if search is not None and (
        search != search.strip() or any(ord(character) < 32 for character in search)
    ):
        raise ValidationError("codeup_operation_invalid", "请提供有效的仓库搜索词。")
    params: dict[str, Any] = {
        "page": payload.get("page", 1),
        "perPage": payload.get("limit", 20),
        "orderBy": "path",
        "sort": "asc",
        "archived": False,
    }
    if search is not None:
        params["search"] = search
    return f"/oapi/v1/codeup/organizations/{catalog.organization}/repositories", params


def _text(value: Any, maximum: int = 4000) -> str:
    if not isinstance(value, str) or len(value) > maximum or "\x00" in value:
        raise ValueError("invalid text")
    return _TOKEN.sub("[REDACTED]", redact_text(value))


def _commit(value: Any, expected: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("invalid commit")
    sha = value.get("id")
    parents = value.get("parentIds", [])
    if (
        not isinstance(sha, str)
        or not _SHA.fullmatch(sha)
        or (expected is not None and sha != expected)
        or not isinstance(parents, list)
        or len(parents) > 64
        or any(not isinstance(item, str) or not _SHA.fullmatch(item) for item in parents)
    ):
        raise ValueError("invalid commit identity")
    timestamp = value.get("committedDate")
    if not isinstance(timestamp, str) or len(timestamp) > 80:
        raise ValueError("invalid commit timestamp")
    if datetime.fromisoformat(timestamp.replace("Z", "+00:00")).tzinfo is None:
        raise ValueError("timestamp needs timezone")
    return {
        "commit_id": sha,
        "parent_ids": parents,
        "committed_at": timestamp,
        "title": _text(value.get("title", "")),
        "message": _text(value.get("message", "")),
    }


def _file(value: Any, payload: dict[str, Any]) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or value.get("filePath") != payload["path"]
        or value.get("commitId") != payload["commit_id"]
    ):
        raise ValueError("file identity mismatch")
    size = value.get("size")
    content = value.get("content")
    if type(size) is not int or not 0 <= size <= MAX_FILE_BYTES or not isinstance(content, str):
        raise ValueError("invalid file size")
    encoding = value.get("encoding")
    if encoding == "base64":
        raw = base64.b64decode(content, validate=True)
    elif encoding == "text":
        raw = content.encode("utf-8")
    else:
        raise ValueError("unsupported file encoding")
    if len(raw) != size:
        raise ValueError("file size mismatch")
    blob_id = hashlib.sha1(
        b"blob " + str(size).encode() + b"\0" + raw, usedforsecurity=False
    ).hexdigest()
    if value.get("blobId") != blob_id:
        raise ValueError("file hash mismatch")
    original = raw.decode("utf-8")
    safe = _text(original, MAX_FILE_BYTES)
    return {
        "path": payload["path"],
        "commit_id": payload["commit_id"],
        "blob_id": blob_id,
        "content": safe,
        "source_size_bytes": size,
        "redacted": safe != original,
        "content_sha256": hashlib.sha256(safe.encode("utf-8")).hexdigest(),
    }


def _normalize(repository: CodeupRepository, payload: dict[str, Any], data: Any) -> dict[str, Any]:
    operation = payload["operation"]
    next_page = None
    complete = True
    if operation == "codeup.repository.get":
        if (
            not isinstance(data, dict)
            or type(data.get("id")) is not int
            or str(data["id"]) != repository.remote_id
            or data.get("visibility") not in {"private", "internal"}
        ):
            raise ValueError("repository identity mismatch")
        items = [
            {
                "id": repository.remote_id,
                "name": _text(data.get("name")),
                "default_branch": _text(data.get("defaultBranch"), 200),
                "visibility": data["visibility"],
            }
        ]
    elif operation == "codeup.commit.get":
        items = [_commit(data, payload["commit_id"])]
    elif operation == "codeup.file.read":
        items = [_file(data, payload)]
    else:
        limit = payload.get("limit", 20)
        if not isinstance(data, list) or len(data) > limit:
            raise ValueError("invalid commit page")
        items = [_commit(item) for item in data]
        if len({item["commit_id"] for item in items}) != len(items):
            raise ValueError("duplicate commits")
        if len(items) == limit:
            # A full page is not proof of exhaustion. Fetching subsequent pages
            # is a separate authorized operation, never an unbounded auto-loop.
            complete = False
            page = payload.get("page", 1)
            next_page = page + 1 if page < 100 else None
    return {
        "operation": operation,
        "repository": repository.name,
        "repository_id": str(repository.local_id),
        "items": items,
        "count": len(items),
        "next_page": next_page,
        "complete": complete,
    }


def _normalize_catalog(
    catalog: CodeupCatalog, payload: dict[str, Any], data: Any
) -> dict[str, Any]:
    limit = payload.get("limit", 20)
    if not isinstance(data, list) or len(data) > limit:
        raise ValueError("invalid repository page")
    items: list[dict[str, Any]] = []
    for value in data:
        if (
            not isinstance(value, dict)
            or type(value.get("id")) is not int
            or not 0 < value["id"] <= 9_999_999_999_999_999_999
            or value.get("visibility") not in {"private", "internal"}
            or type(value.get("archived")) is not bool
            or type(value.get("accessLevel")) is not int
            or value["accessLevel"] not in {20, 30, 40}
        ):
            raise ValueError("invalid repository identity")
        name = _text(value.get("name"), 240)
        path = _text(value.get("pathWithNamespace"), 500)
        if not name.strip() or name != name.strip() or not path.strip() or path != path.strip():
            raise ValueError("invalid repository name")
        items.append(
            {
                "id": str(value["id"]),
                "name": name,
                "path": path,
                "visibility": value["visibility"],
                "archived": value["archived"],
            }
        )
    if len({item["id"] for item in items}) != len(items) or len(
        {item["path"] for item in items}
    ) != len(items):
        raise ValueError("duplicate repository identity")
    complete = len(items) < limit
    page = payload.get("page", 1)
    return {
        "operation": CODEUP_DISCOVERY_OPERATION,
        "connector_id": str(catalog.connector_id),
        "items": items,
        "count": len(items),
        "next_page": page + 1 if not complete and page < 100 else None,
        "complete": complete,
    }


async def read_codeup(
    connector: Connector,
    payload: dict[str, Any],
    credential: str | None,
    *,
    request_timeout: float,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    """Trusted executor leaf. Call only after Gateway Policy, grants and resource ACL."""
    target: CodeupRepository | CodeupCatalog
    if is_codeup_catalog_connector(connector):
        catalog = catalog_configuration(connector)
        target = catalog
        path, params = _catalog_request(catalog, payload)
    else:
        repository = repository_configuration(connector, payload.get("repository"))
        target = repository
        path, params = _request(repository, payload)
    if (
        not credential
        or not credential.isascii()
        or len(credential) > 4096
        or any(ord(char) < 33 or ord(char) == 127 for char in credential)
    ):
        raise ValidationError(
            "credential_unavailable",
            "请由管理员在密钥管理中配置云效只读访问令牌，勿将令牌发送到聊天。",
        )
    # Do not follow redirects or inherit ambient proxy credentials. Upstream bodies,
    # exception messages, request headers and URLs never enter a failure response.
    try:
        async with (
            httpx.AsyncClient(
                timeout=request_timeout,
                transport=transport,
                follow_redirects=False,
                trust_env=False,
            ) as client,
            client.stream(
                "GET",
                CODEUP_ORIGIN + path,
                params=params,
                headers={
                    "x-yunxiao-token": credential,
                    "Accept": "application/json",
                    "Accept-Encoding": "identity",
                },
            ) as response,
        ):
            if response.status_code in {401, 403, 404}:
                raise AuthorizationError(
                    "codeup_upstream_denied",
                    "云效拒绝访问或资源不可见，请核对令牌权限与仓库成员关系。",
                )
            if response.status_code == 429:
                raise ObsionError("codeup_rate_limited", "云效请求已限流，请稍后重试。", 429)
            if response.status_code != 200:
                raise ObsionError(
                    "codeup_upstream_unavailable", "云效暂不可用，请检查连接状态后重试。", 503
                )
            # Reject encoded bodies before httpx's decoder can allocate an
            # unbounded expanded chunk. Never trust Content-Length as a budget.
            if (
                response.headers.get("content-encoding", "identity").strip().casefold()
                != "identity"
            ):
                raise ObsionError(
                    "codeup_response_invalid", "云效返回了不支持的压缩内容，未予读取。", 503
                )
            body = bytearray()
            async for chunk in response.aiter_bytes(chunk_size=65_536):
                if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                    raise ObsionError(
                        "codeup_response_too_large", "云效返回超过读取预算，请缩小查询范围。", 503
                    )
                body.extend(chunk)
    except (httpx.HTTPError, OSError, TimeoutError):
        raise ObsionError(
            "codeup_upstream_unavailable", "云效连接失败，请检查网络出口及服务状态。", 503
        ) from None
    try:
        parsed = json.loads(body)
        normalized = (
            _normalize_catalog(target, payload, parsed)
            if isinstance(target, CodeupCatalog)
            else _normalize(target, payload, parsed)
        )
        # A malicious upstream can echo even a short credential in ordinary text.
        # Normalize/hash original file bytes first, then scrub every exposed string.
        return _scrub(normalized, credential)
    except (ValueError, TypeError, KeyError, UnicodeError, binascii.Error, RecursionError):
        raise ObsionError(
            "codeup_response_invalid", "云效返回未通过身份或内容校验，未作为可信结果使用。", 503
        ) from None


def _scrub(value: dict[str, Any], credential: str) -> dict[str, Any]:
    def visit(item: Any) -> Any:
        if isinstance(item, str):
            return item.replace(credential, "[REDACTED]")
        if isinstance(item, list):
            return [visit(child) for child in item]
        if isinstance(item, dict):
            return {key: visit(child) for key, child in item.items()}
        return item

    result: dict[str, Any] = visit(value)
    if value["operation"] == "codeup.file.read":
        item = result["items"][0]
        item["redacted"] = item["redacted"] or item["content"] != value["items"][0]["content"]
        item["content_sha256"] = hashlib.sha256(item["content"].encode("utf-8")).hexdigest()
    return result
