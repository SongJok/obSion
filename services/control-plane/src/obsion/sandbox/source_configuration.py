"""来源登记专用的封闭配置契约；不解析凭据、不验证厂商 scopes、不联网。"""

import json
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from obsion.capabilities.codeup_contract import CODEUP_ORIGIN, CODEUP_PROTOCOL
from obsion.release.hardening import scan_secret_text

CONFIG_FIELDS = (
    "connector_type",
    "environment",
    "endpoint",
    "configuration",
    "credential_ref",
    "declared_grants",
    "allowed_egress",
)
_REPOSITORY_ID = re.compile(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*")
_CODEUP_REMOTE_ID = re.compile(r"[1-9][0-9]{0,19}")
_CODEUP_NAME = re.compile(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+")
_AUTHORITY = re.compile(r"[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[1-9][0-9]{0,4})?")


def valid_repository_id(value: str) -> bool:
    return bool(type(value) is str and len(value) <= 500 and _REPOSITORY_ID.fullmatch(value))


def configuration_snapshot(values: dict[str, Any], *, environment: str) -> dict[str, Any]:
    """冻结受支持只读来源的最小声明；未知连接器或字段直接拒绝。"""
    invalid = ValueError("project_source_configuration_invalid")
    connector_type = values.get("connector_type")
    if connector_type not in {"git-http", "codeup"} or values.get("environment") != environment:
        raise invalid
    configuration = values["configuration"]
    if type(configuration) is not dict or values["declared_grants"] != ["code.read"]:
        raise invalid
    if connector_type == "git-http":
        if set(configuration) != {"allowed_repositories"}:
            raise invalid
        repositories = configuration["allowed_repositories"]
        if (
            type(repositories) is not list
            or not 1 <= len(repositories) <= 100
            or any(not valid_repository_id(item) for item in repositories)
            or len(set(repositories)) != len(repositories)
        ):
            raise invalid
    else:
        if (
            set(configuration)
            - {
                "protocol",
                "organization_id",
                "repositories",
                "allowed_repositories",
                "rate_limit_per_minute",
            }
            or configuration.get("protocol") != CODEUP_PROTOCOL
        ):
            raise invalid
        organization = configuration.get("organization_id")
        repositories = configuration.get("repositories")
        allowed = configuration.get("allowed_repositories")
        rate = configuration.get("rate_limit_per_minute")
        if (
            type(organization) is not str
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", organization)
            or type(repositories) is not dict
            or not 1 <= len(repositories) <= 100
            or type(allowed) is not list
            or any(type(item) is not str for item in allowed)
            or set(allowed) != set(repositories)
            or len(allowed) != len(repositories)
            or any(
                type(key) is not str or len(key) > 240 or _CODEUP_NAME.fullmatch(key) is None
                for key in repositories
            )
            or (rate is not None and (type(rate) is not int or not 1 <= rate <= 600))
        ):
            raise invalid
        for mapping in repositories.values():
            if (
                type(mapping) is not dict
                or set(mapping) != {"id", "repository_id"}
                or type(mapping["id"]) is not str
                or _CODEUP_REMOTE_ID.fullmatch(mapping["id"]) is None
                or type(mapping["repository_id"]) is not str
            ):
                raise invalid
            try:
                UUID(mapping["repository_id"])
            except (ValueError, AttributeError):
                raise invalid from None
    endpoint = values["endpoint"]
    authorities = values["allowed_egress"]
    if (
        type(endpoint) is not str
        or len(endpoint) > 1024
        or not endpoint.isascii()
        or any(char.isspace() or ord(char) < 32 for char in endpoint)
        or type(authorities) is not list
        or len(authorities) != 1
        or type(authorities[0]) is not str
        or not _AUTHORITY.fullmatch(authorities[0])
    ):
        raise invalid
    if connector_type == "codeup" and endpoint != CODEUP_ORIGIN:
        raise invalid
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
    except ValueError:
        raise invalid from None
    authorities_match = False
    if parsed.scheme == "https" and port is None:
        authorities_match = authorities[0] in {parsed.netloc, f"{parsed.netloc}:443"}
    else:
        authorities_match = authorities[0] == parsed.netloc
    if (
        parsed.scheme != "https"
        or not authorities_match
        or parsed.path not in {"", "/"}
        or "?" in endpoint
        or "#" in endpoint
        or parsed.username is not None
        or parsed.password is not None
        or port == 0
    ):
        raise invalid
    reference = values["credential_ref"]
    if reference is not None and (
        type(reference) is not str
        or not re.fullmatch(r"secret://[A-Za-z][A-Za-z0-9_-]{0,199}", reference)
    ):
        raise invalid
    payload = json.dumps(
        {key: values[key] for key in CONFIG_FIELDS}, allow_nan=False, sort_keys=True
    )
    if scan_secret_text(payload, path="project-source-configuration"):
        raise invalid
    # JSON 往返与 ORM 可变 dict/list 解耦；响应及审计不包含该快照。
    snapshot: dict[str, Any] = json.loads(payload)
    return snapshot
