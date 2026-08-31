"""Read-only observability response contracts.

Provider payloads are intentionally treated as untrusted transport data.  This
module reduces the first observability slice to one stable event envelope before
the Capability Gateway persists it as Evidence.  The envelope is deliberately
small; provider-specific fields remain under ``attributes`` and are allowlisted.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from obsion.common.errors import ObsionError, ValidationError
from obsion.security.redaction import redact

OBSERVABILITY_OPERATIONS = frozenset(
    {
        "metric.query",
        "metric.compare",
        "metric.anomaly",
        "metric.dimension",
        "log.search",
        "log.aggregate",
        "deployment.list",
        "trace.search",
        "trace.timeline",
    }
)

_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "timestamp": ("timestamp", "time", "ts", "occurred_at", "created_at"),
    "service": ("service", "service_name", "serviceName", "app", "application"),
    "environment": ("environment", "env"),
    "trace_id": ("trace_id", "traceId"),
    "request_id": ("request_id", "requestId"),
    "user_id_hash": ("user_id_hash", "userIdHash"),
    "order_id_hash": ("order_id_hash", "orderIdHash"),
    "deployment_id": ("deployment_id", "deploymentId", "deployment"),
    "commit_id": ("commit_id", "commitId", "commit"),
    "host": ("host", "hostname"),
    "pod": ("pod", "pod_name", "podName"),
    "severity": ("severity", "level", "log_level", "logLevel"),
}
_ATTRIBUTE_KEYS = frozenset(
    {
        "kind",
        "metric",
        "unit",
        "value",
        "message",
        "labels",
        "status",
        "duration_ms",
        "count",
        "version",
        "revision",
        "reason",
        "span_id",
        "parent_span_id",
        "span_name",
        "status_code",
    }
)
_SENSITIVE_LABEL = frozenset(
    {"user", "userid", "user_id", "order", "orderid", "order_id", "email", "phone", "ip"}
)


class ObservabilityUnavailableError(ObsionError):
    """The configured read-only observability dependency is unavailable."""

    def __init__(self, message: str = "The observability connector is unavailable") -> None:
        super().__init__("observability_unavailable", message, status_code=503)


class ObservabilityResponseError(ObsionError):
    """The dependency returned a payload outside the versioned contract."""

    def __init__(
        self, message: str = "The observability connector returned an invalid response"
    ) -> None:
        super().__init__("observability_response_invalid", message, status_code=503)


@dataclass(frozen=True, slots=True)
class ObservabilityEvent:
    timestamp: str
    service: str
    environment: str
    trace_id: str | None = None
    request_id: str | None = None
    user_id_hash: str | None = None
    order_id_hash: str | None = None
    deployment_id: str | None = None
    commit_id: str | None = None
    host: str | None = None
    pod: str | None = None
    severity: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "service": self.service,
            "environment": self.environment,
            "trace_id": self.trace_id,
            "request_id": self.request_id,
            "user_id_hash": self.user_id_hash,
            "order_id_hash": self.order_id_hash,
            "deployment_id": self.deployment_id,
            "commit_id": self.commit_id,
            "host": self.host,
            "pod": self.pod,
            "severity": self.severity,
            "attributes": self.attributes,
        }


def normalize_response(
    payload: Any,
    *,
    operation: str,
    default_service: str,
    default_environment: str,
) -> dict[str, Any]:
    """Normalize a provider response into the canonical observability envelope."""

    if operation not in OBSERVABILITY_OPERATIONS:
        raise ValidationError(
            "observability_operation_invalid",
            "The observability operation is not part of the read-only contract",
        )
    records = _records(payload)
    events = [
        _normalize_event(
            item,
            index=index,
            operation=operation,
            default_service=default_service,
            default_environment=default_environment,
        ).as_dict()
        for index, item in enumerate(records)
    ]
    return {
        "operation": operation,
        "events": events,
        "count": len(events),
        "next_cursor": _cursor(payload),
    }


def _records(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return _mapping_records(payload)
    if not isinstance(payload, Mapping):
        raise ValueError("response must be an object or array")

    for key in ("events", "items", "results", "result", "spans", "traces"):
        value = payload.get(key)
        if isinstance(value, list):
            if key == "result" and any(
                isinstance(item, Mapping) and ("values" in item or "value" in item)
                for item in value
            ):
                return _series_records(value)
            return _mapping_records(value)
    nested = payload.get("data")
    if isinstance(nested, (Mapping, list)):
        records = _records(nested)
        if records:
            return records
    series = payload.get("series")
    if isinstance(series, list):
        return _series_records(series)
    if _looks_like_record(payload):
        return [payload]
    return []


def _mapping_records(values: list[Any]) -> list[Mapping[str, Any]]:
    if not all(isinstance(value, Mapping) for value in values):
        raise ValueError("response records must be objects")
    return [value for value in values if isinstance(value, Mapping)]


def _series_records(values: list[Any]) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []
    for series in values:
        if not isinstance(series, Mapping):
            raise ValueError("series records must be objects")
        labels = series.get("labels", series.get("metric", {}))
        labels_map = dict(labels) if isinstance(labels, Mapping) else {}
        base: dict[str, Any] = dict(labels_map)
        if labels_map:
            base["labels"] = labels_map
        points = series.get("values")
        if isinstance(points, list):
            for point in points:
                if isinstance(point, (list, tuple)) and len(point) == 2:
                    records.append({**base, "timestamp": point[0], "value": point[1]})
                elif isinstance(point, Mapping):
                    records.append({**base, **point})
            continue
        point = series.get("value")
        if isinstance(point, (list, tuple)) and len(point) == 2:
            records.append({**base, "timestamp": point[0], "value": point[1]})
        else:
            records.append(series)
    return records


def _looks_like_record(value: Mapping[str, Any]) -> bool:
    return any(alias in value for aliases in _FIELD_ALIASES.values() for alias in aliases) or any(
        key in value for key in _ATTRIBUTE_KEYS
    )


def _normalize_event(
    value: Mapping[str, Any],
    *,
    index: int,
    operation: str,
    default_service: str,
    default_environment: str,
) -> ObservabilityEvent:
    metric_labels = value.get("metric")
    nested_labels = metric_labels if isinstance(metric_labels, Mapping) else {}
    timestamp = _field(value, "timestamp")
    if timestamp is None:
        raise ValueError(f"event {index} has no timestamp")
    service = _field(value, "service") or _string(nested_labels.get("service")) or default_service
    environment = (
        _field(value, "environment")
        or _string(nested_labels.get("environment"))
        or default_environment
    )
    if not service or not environment:
        raise ValueError(f"event {index} has no service or environment")
    attributes: dict[str, Any] = {"operation": operation}
    for key in _ATTRIBUTE_KEYS:
        candidate = value.get(key)
        if candidate is None and key in nested_labels:
            candidate = nested_labels[key]
        if candidate is not None:
            attributes[key] = redact(candidate)
    labels = value.get("labels")
    if isinstance(labels, Mapping):
        attributes["labels"] = _safe_labels(labels)
    elif nested_labels:
        attributes["labels"] = _safe_labels(nested_labels)
    return ObservabilityEvent(
        timestamp=timestamp,
        service=service,
        environment=environment,
        trace_id=_field(value, "trace_id") or _string(nested_labels.get("trace_id")),
        request_id=_field(value, "request_id") or _string(nested_labels.get("request_id")),
        user_id_hash=_field(value, "user_id_hash") or _string(nested_labels.get("user_id_hash")),
        order_id_hash=_field(value, "order_id_hash") or _string(nested_labels.get("order_id_hash")),
        deployment_id=_field(value, "deployment_id") or _string(nested_labels.get("deployment_id")),
        commit_id=_field(value, "commit_id") or _string(nested_labels.get("commit_id")),
        host=_field(value, "host") or _string(nested_labels.get("host")),
        pod=_field(value, "pod") or _string(nested_labels.get("pod")),
        severity=_field(value, "severity") or _string(nested_labels.get("severity")),
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


def _cursor(payload: Any) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    for key in ("next_cursor", "nextCursor", "cursor"):
        candidate = payload.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _safe_labels(value: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, candidate in value.items():
        normalized = str(key).casefold().replace("-", "_")
        if normalized in _SENSITIVE_LABEL or any(
            token in normalized for token in ("email", "phone", "token", "secret", "password")
        ):
            continue
        safe[str(key)] = redact(candidate)
    return safe
