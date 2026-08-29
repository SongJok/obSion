from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker

from obsion.common.errors import ValidationError
from obsion.contracts.errors import ErrorContractDefinitionError, validate_error_code
from obsion.domain.enums import ActorType, Classification
from obsion.security.redaction import redact

_CONTRACT_ROOT = Path(__file__).resolve().parent
_REGISTRY_FILE = "registry.json"
_REGISTRY_SCHEMA_FILE = "registry.schema.json"


class EventContractDefinitionError(RuntimeError):
    """机器可读 Event 合同自身无效。"""


@dataclass(frozen=True, slots=True)
class EventContractSummary:
    registry_version: int
    event_count: int
    version_count: int


@dataclass(frozen=True, slots=True)
class PreparedEventDraft:
    event_id: UUID
    created_at: datetime
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _EventContracts:
    registry_version: int
    envelope_validator: Draft202012Validator
    payload_validators: dict[tuple[str, int], Draft202012Validator]


def canonicalize_json(value: Any) -> Any:
    """将值转换为确定性的 JSON 数据模型；不猜测不受支持类型的语义。"""

    return _canonicalize_json(value, path="$", active_containers=set())


def prepare_event_draft(
    *,
    event_id: UUID,
    name: str,
    schema_version: int,
    organization_id: UUID,
    aggregate_type: str,
    aggregate_id: UUID,
    run_id: UUID | None,
    causation_id: UUID | None,
    correlation_id: UUID,
    actor_type: ActorType,
    actor_id: UUID | None,
    classification: Classification,
    payload: Mapping[str, Any],
    created_at: datetime,
) -> PreparedEventDraft:
    contracts = _load_contracts()
    key = (name, schema_version)
    payload_validator = contracts.payload_validators.get(key)
    if payload_validator is None:
        supported_versions = sorted(
            version for event_name, version in contracts.payload_validators if event_name == name
        )
        if not supported_versions:
            raise ValidationError(
                "event_name_unregistered",
                "The event name is not registered",
                event_name=name,
            )
        raise ValidationError(
            "event_schema_version_unsupported",
            "The event schema version is not registered",
            event_name=name,
            schema_version=schema_version,
            supported_versions=supported_versions,
        )

    canonical_payload = canonicalize_json(payload)
    redacted_payload = redact(canonical_payload)
    if not isinstance(redacted_payload, dict):
        raise ValidationError(
            "event_payload_not_object",
            "The event payload must be a JSON object",
            event_name=name,
            schema_version=schema_version,
        )
    _validate_instance(
        payload_validator,
        redacted_payload,
        code="event_payload_schema_invalid",
        message="The event payload does not satisfy its registered schema",
        event_name=name,
        schema_version=schema_version,
    )
    _validate_payload_error_code(
        redacted_payload,
        event_name=name,
        schema_version=schema_version,
    )

    provisional_envelope = build_event_envelope(
        event_id=event_id,
        organization_id=organization_id,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        sequence=1,
        name=name,
        run_id=run_id,
        run_sequence=1 if run_id is not None else None,
        causation_id=causation_id,
        correlation_id=correlation_id,
        actor_type=actor_type,
        actor_id=actor_id,
        schema_version=schema_version,
        classification=classification,
        payload=redacted_payload,
        created_at=created_at,
    )
    validate_event_envelope(
        provisional_envelope,
        event_name=name,
        schema_version=schema_version,
    )
    return PreparedEventDraft(
        event_id=event_id,
        created_at=created_at,
        payload=redacted_payload,
    )


def build_event_envelope(
    *,
    event_id: UUID,
    organization_id: UUID,
    aggregate_type: str,
    aggregate_id: UUID,
    sequence: int,
    name: str,
    run_id: UUID | None,
    run_sequence: int | None,
    causation_id: UUID | None,
    correlation_id: UUID,
    actor_type: ActorType,
    actor_id: UUID | None,
    schema_version: int,
    classification: Classification,
    payload: dict[str, Any],
    created_at: datetime,
) -> dict[str, Any]:
    identifier = str(event_id)
    return {
        "id": identifier,
        "event_id": identifier,
        "organization_id": str(organization_id),
        "aggregate_type": aggregate_type,
        "aggregate_id": str(aggregate_id),
        "sequence": sequence,
        "name": name,
        "run_id": str(run_id) if run_id is not None else None,
        "run_sequence": run_sequence,
        "causation_id": str(causation_id) if causation_id is not None else None,
        "correlation_id": str(correlation_id),
        "actor_type": actor_type.value,
        "actor_id": str(actor_id) if actor_id is not None else None,
        "schema_version": schema_version,
        "classification": classification.value,
        "payload": payload,
        "created_at": _canonical_datetime(created_at, path="$.created_at"),
    }


def validate_event_envelope(
    envelope: Mapping[str, Any],
    *,
    event_name: str | None = None,
    schema_version: int | None = None,
) -> None:
    canonical_envelope = canonicalize_json(envelope)
    if not isinstance(canonical_envelope, dict):
        raise ValidationError(
            "event_envelope_schema_invalid",
            "The Event envelope must be a JSON object",
        )
    if canonical_envelope.get("id") != canonical_envelope.get("event_id"):
        raise ValidationError(
            "event_envelope_schema_invalid",
            "The Event envelope id and event_id must identify the same Event",
            path="$.event_id",
            event_name=event_name,
            schema_version=schema_version,
        )
    _validate_instance(
        _load_contracts().envelope_validator,
        canonical_envelope,
        code="event_envelope_schema_invalid",
        message="The Event envelope does not satisfy the frozen schema",
        event_name=event_name,
        schema_version=schema_version,
    )


def _validate_payload_error_code(
    payload: Mapping[str, Any],
    *,
    event_name: str,
    schema_version: int,
) -> None:
    error_code = payload.get("error_code")
    if error_code is None:
        return
    if not isinstance(error_code, str):
        # The versioned payload schema owns type/nullability. Reaching this branch
        # means a contract regression bypassed that structural boundary.
        raise ValidationError(
            "event_payload_schema_invalid",
            "The event payload error_code must be a registered string",
            path="$.error_code",
            event_name=event_name,
            schema_version=schema_version,
        )
    try:
        validate_error_code(error_code)
    except ErrorContractDefinitionError as exc:
        raise ValidationError(
            "event_payload_schema_invalid",
            "The event payload error_code is not registered",
            path="$.error_code",
            event_name=event_name,
            schema_version=schema_version,
        ) from exc


def validate_event_contracts() -> EventContractSummary:
    contracts = _load_contracts()
    return EventContractSummary(
        registry_version=contracts.registry_version,
        event_count=len({name for name, _ in contracts.payload_validators}),
        version_count=len(contracts.payload_validators),
    )


def registered_event_versions() -> frozenset[tuple[str, int]]:
    return frozenset(_load_contracts().payload_validators)


def _canonicalize_json(value: Any, *, path: str, active_containers: set[int]) -> Any:
    if isinstance(value, Enum):
        return _canonicalize_json(value.value, path=path, active_containers=active_containers)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return _canonical_datetime(value, path=path)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        if not value.is_finite():
            _raise_not_json_safe(path, value)
        return format(value, "f")
    if isinstance(value, float):
        if not math.isfinite(value):
            _raise_not_json_safe(path, value)
        return value

    if isinstance(value, Mapping):
        container_id = id(value)
        if container_id in active_containers:
            _raise_not_json_safe(path, value, reason="cyclic_container")
        active_containers.add(container_id)
        try:
            result: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    _raise_not_json_safe(path, key, reason="non_string_object_key")
                result[key] = _canonicalize_json(
                    item,
                    path=f"{path}.{key}",
                    active_containers=active_containers,
                )
            return result
        finally:
            active_containers.remove(container_id)

    if isinstance(value, (list, tuple)):
        container_id = id(value)
        if container_id in active_containers:
            _raise_not_json_safe(path, value, reason="cyclic_container")
        active_containers.add(container_id)
        try:
            return [
                _canonicalize_json(
                    item,
                    path=f"{path}[{index}]",
                    active_containers=active_containers,
                )
                for index, item in enumerate(value)
            ]
        finally:
            active_containers.remove(container_id)

    _raise_not_json_safe(path, value)


def _canonical_datetime(value: datetime, *, path: str) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        _raise_not_json_safe(path, value, reason="naive_datetime")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _raise_not_json_safe(path: str, value: Any, *, reason: str = "unsupported_type") -> None:
    raise ValidationError(
        "event_payload_not_json_safe",
        "The event value cannot be represented safely as canonical JSON",
        path=path,
        reason=reason,
        python_type=type(value).__name__,
    )


def _validate_instance(
    validator: Draft202012Validator,
    instance: Any,
    *,
    code: str,
    message: str,
    **details: Any,
) -> None:
    errors = sorted(
        validator.iter_errors(instance),
        key=lambda error: (
            tuple(str(item) for item in error.absolute_path),
            tuple(str(item) for item in error.absolute_schema_path),
        ),
    )
    if not errors:
        return
    error = errors[0]
    raise ValidationError(
        code,
        message,
        path=_json_path(error.absolute_path),
        schema_path=_json_path(error.absolute_schema_path),
        validator=str(error.validator),
        **details,
    )


def _json_path(parts: Any) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path


@lru_cache(maxsize=1)
def _load_contracts() -> _EventContracts:
    registry_schema = _read_schema(_REGISTRY_SCHEMA_FILE)
    envelope_name: str
    registry = _read_json(_REGISTRY_FILE)
    try:
        Draft202012Validator.check_schema(registry_schema)
        Draft202012Validator(registry_schema, format_checker=FormatChecker()).validate(registry)
        envelope_name = registry["envelope_schema"]
    except Exception as exc:
        raise EventContractDefinitionError("Event registry is not a valid frozen contract") from exc

    envelope_schema = _read_schema(envelope_name)
    payload_validators: dict[tuple[str, int], Draft202012Validator] = {}
    referenced_payloads: set[str] = set()
    event_names: list[str] = []
    try:
        Draft202012Validator.check_schema(envelope_schema)
        envelope_validator = Draft202012Validator(
            envelope_schema,
            format_checker=FormatChecker(),
        )
        for event in registry["events"]:
            name = event["name"]
            event_names.append(name)
            versions = event["versions"]
            version_numbers = [item["schema_version"] for item in versions]
            if version_numbers != sorted(version_numbers) or len(version_numbers) != len(
                set(version_numbers)
            ):
                raise ValueError(f"Event versions are not unique and ordered: {name}")
            for version in versions:
                schema_version = version["schema_version"]
                schema_path = version["payload_schema"]
                expected_path = f"payloads/{name}.v{schema_version}.schema.json"
                if schema_path != expected_path:
                    raise ValueError(f"Event payload schema path is not canonical: {name}")
                schema_bytes = _read_bytes(schema_path)
                if sha256(schema_bytes).hexdigest() != version["payload_schema_sha256"]:
                    raise ValueError(f"Event payload schema checksum mismatch: {name}")
                payload_schema = json.loads(schema_bytes)
                Draft202012Validator.check_schema(payload_schema)
                key = (name, schema_version)
                if key in payload_validators:
                    raise ValueError(f"Duplicate Event contract: {name} v{schema_version}")
                payload_validators[key] = Draft202012Validator(
                    payload_schema,
                    format_checker=FormatChecker(),
                )
                referenced_payloads.add(schema_path)
        if event_names != sorted(event_names) or len(event_names) != len(set(event_names)):
            raise ValueError("Event names are not unique and ordered")
        payload_root = _CONTRACT_ROOT / "payloads"
        available_payloads = {
            path.relative_to(_CONTRACT_ROOT).as_posix()
            for path in payload_root.glob("*.schema.json")
        }
        if referenced_payloads != available_payloads:
            raise ValueError(
                "Event registry and payload schema files do not have one-to-one coverage"
            )
    except EventContractDefinitionError:
        raise
    except Exception as exc:
        raise EventContractDefinitionError("Event schemas are not valid frozen contracts") from exc

    return _EventContracts(
        registry_version=registry["registry_version"],
        envelope_validator=envelope_validator,
        payload_validators=payload_validators,
    )


def _read_schema(relative_path: str) -> dict[str, Any]:
    document = _read_json(relative_path)
    if not isinstance(document, dict):
        raise EventContractDefinitionError(f"Event schema must be a JSON object: {relative_path}")
    return document


def _read_json(relative_path: str) -> Any:
    try:
        return json.loads(_read_bytes(relative_path))
    except (OSError, json.JSONDecodeError) as exc:
        raise EventContractDefinitionError(f"Cannot load Event contract: {relative_path}") from exc


def _read_bytes(relative_path: str) -> bytes:
    candidate = (_CONTRACT_ROOT / relative_path).resolve()
    if _CONTRACT_ROOT not in candidate.parents:
        raise EventContractDefinitionError("Event contract path escapes the contract root")
    try:
        return candidate.read_bytes()
    except OSError as exc:
        raise EventContractDefinitionError(f"Cannot read Event contract: {relative_path}") from exc
