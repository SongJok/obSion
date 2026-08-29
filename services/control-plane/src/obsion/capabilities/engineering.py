"""Read-only source-control and deployment response contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from obsion.common.errors import ObsionError, ValidationError
from obsion.security.redaction import redact

ENGINEERING_OPERATIONS = frozenset(
    {"code.search", "git.commit", "git.diff", "git.history", "deployment.commit"}
)
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "timestamp": ("timestamp", "time", "created_at", "createdAt", "committed_at", "deployed_at"),
    "repository": ("repository", "repo", "repository_name", "repositoryName"),
    "commit_id": ("commit_id", "commitId", "sha", "commit"),
    "deployment_id": ("deployment_id", "deploymentId", "release_id", "releaseId"),
    "service": ("service", "service_name", "serviceName"),
    "environment": ("environment", "env"),
    "author_hash": ("author_hash", "authorHash"),
    "title": ("title", "subject"),
    "status": ("status", "state"),
}
_ATTRIBUTE_KEYS = frozenset(
    {"branch", "ref", "message", "files", "changed_files", "patch", "diff", "url", "provider"}
)


class EngineeringUnavailableError(ObsionError):
    def __init__(self, message: str = "The engineering connector is unavailable") -> None:
        super().__init__("engineering_unavailable", message, status_code=503)


class EngineeringResponseError(ObsionError):
    def __init__(
        self, message: str = "The engineering connector returned an invalid response"
    ) -> None:
        super().__init__("engineering_response_invalid", message, status_code=503)


@dataclass(frozen=True, slots=True)
class ChangeEvent:
    timestamp: str
    repository: str
    commit_id: str | None = None
    deployment_id: str | None = None
    service: str | None = None
    environment: str | None = None
    author_hash: str | None = None
    title: str | None = None
    status: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "repository": self.repository,
            "commit_id": self.commit_id,
            "deployment_id": self.deployment_id,
            "service": self.service,
            "environment": self.environment,
            "author_hash": self.author_hash,
            "title": self.title,
            "status": self.status,
            "attributes": self.attributes,
        }


def normalize_response(
    payload: Any,
    *,
    operation: str,
    default_repository: str,
    default_environment: str,
) -> dict[str, Any]:
    if operation not in ENGINEERING_OPERATIONS:
        raise ValidationError(
            "engineering_operation_invalid",
            "The engineering operation is not part of the read-only contract",
        )
    records = _records(payload)
    items = [
        _normalize_event(
            item,
            index=index,
            operation=operation,
            default_repository=default_repository,
            default_environment=default_environment,
        ).as_dict()
        for index, item in enumerate(records)
    ]
    return {
        "operation": operation,
        "items": items,
        "count": len(items),
        "next_cursor": _cursor(payload),
    }


def _records(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return _mapping_records(payload)
    if not isinstance(payload, Mapping):
        raise ValueError("response must be an object or array")
    for key in ("items", "events", "results", "commits", "deployments"):
        value = payload.get(key)
        if isinstance(value, list):
            return _mapping_records(value)
    nested = payload.get("data")
    if isinstance(nested, (Mapping, list)):
        records = _records(nested)
        if records:
            return records
    if _looks_like_record(payload):
        return [payload]
    return []


def _mapping_records(values: list[Any]) -> list[Mapping[str, Any]]:
    if not all(isinstance(value, Mapping) for value in values):
        raise ValueError("response records must be objects")
    return [value for value in values if isinstance(value, Mapping)]


def _looks_like_record(value: Mapping[str, Any]) -> bool:
    return any(alias in value for aliases in _FIELD_ALIASES.values() for alias in aliases) or any(
        key in value for key in _ATTRIBUTE_KEYS
    )


def _normalize_event(
    value: Mapping[str, Any],
    *,
    index: int,
    operation: str,
    default_repository: str,
    default_environment: str,
) -> ChangeEvent:
    timestamp = _field(value, "timestamp")
    if timestamp is None:
        raise ValueError(f"change {index} has no timestamp")
    repository = _field(value, "repository") or default_repository
    if not repository:
        raise ValueError(f"change {index} has no repository")
    attributes: dict[str, Any] = {"operation": operation}
    for key in _ATTRIBUTE_KEYS:
        candidate = value.get(key)
        if candidate is not None:
            attributes[key] = _safe_attribute(key, candidate)
    return ChangeEvent(
        timestamp=timestamp,
        repository=repository,
        commit_id=_field(value, "commit_id"),
        deployment_id=_field(value, "deployment_id"),
        service=_field(value, "service"),
        environment=_field(value, "environment") or default_environment,
        author_hash=_field(value, "author_hash"),
        title=_field(value, "title"),
        status=_field(value, "status"),
        attributes=attributes,
    )


def _field(value: Mapping[str, Any], name: str) -> str | None:
    for alias in _FIELD_ALIASES[name]:
        candidate = value.get(alias)
        if candidate is not None:
            if name == "timestamp":
                return _timestamp(candidate)
            return _string(candidate)
    return None


def _timestamp(value: Any) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value, tz=UTC).isoformat()
    candidate = _string(value)
    if candidate is None:
        return None
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError:
        return candidate
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat()


def _string(value: Any) -> str | None:
    if isinstance(value, str):
        candidate = value.strip()
        return candidate or None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return None


def _safe_attribute(key: str, value: Any) -> Any:
    if key in {"patch", "diff", "message"} and isinstance(value, str):
        return redact(value)[:200_000]
    if key in {"files", "changed_files"} and isinstance(value, list):
        return [item for item in (_string(candidate) for candidate in value[:500]) if item]
    return redact(value)


def _cursor(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    for key in ("next_cursor", "nextCursor", "cursor"):
        candidate = payload.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None
