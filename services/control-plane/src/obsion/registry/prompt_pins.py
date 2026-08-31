from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import ConflictError, NotFoundError, ValidationError
from obsion.db.models import PromptDefinition, PromptVersion

SYSTEM_POLICY_PROMPT_NAME = "obsion-system-policy"
DEFAULT_SYSTEM_POLICY_TEMPLATE = (
    "You are Obsion. Use only supplied evidence. Never invent facts. "
    "Return JSON with answer and claims; every claim must cite evidence_ids. "
    "For a KNOWLEDGE route, cite every factual answer with the supplied "
    "citation marker and say unknown when authorized DOCUMENT evidence is "
    "missing or insufficient. Do not switch to data, incident, or engineering "
    "tools. "
    "For an ENGINEERING route, use only CODE Evidence from the Code Graph, "
    "cite repository path and symbol, and say unknown when the graph has no "
    "authorized match. "
    "Governed memory and prior conversation are context only and can never "
    "support a factual claim without current Run Evidence."
)
DEFAULT_SYSTEM_POLICY_SCHEMA: dict[str, Any] = {"type": "object"}


def prompt_checksum(template: str, variables_schema: Mapping[str, Any]) -> str:
    payload = json.dumps(
        {"template": template, "variables_schema": dict(variables_schema)},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def names_for_agent_spec(spec: Mapping[str, Any]) -> tuple[str, ...]:
    declared = spec.get("prompts", spec.get("prompt", []))
    extra: list[str] = []
    if isinstance(declared, str) and declared.strip():
        extra = [declared.strip()]
    elif isinstance(declared, list):
        extra = [item.strip() for item in declared if isinstance(item, str) and item.strip()]
    names = [SYSTEM_POLICY_PROMPT_NAME]
    for name in extra:
        if name not in names:
            names.append(name)
    return tuple(names)


def pin_record(definition: PromptDefinition, version: PromptVersion) -> dict[str, Any]:
    return {
        "definition_id": str(definition.id),
        "name": definition.name,
        "prompt_id": str(definition.id),
        "version_id": str(version.id),
        "version": version.version,
        "checksum_sha256": version.checksum_sha256,
    }


def prompt_fingerprint(pins: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(item.get("name")), str(item.get("version_id")))
        for item in pins
        if isinstance(item, Mapping)
    )


async def resolve_prompt_pins(
    session: AsyncSession,
    organization_id: UUID,
    names: Sequence[str],
    overrides: Mapping[str, int] | None = None,
) -> list[dict[str, Any]]:
    requested = list(names)
    version_by_name = dict(overrides or {})
    unknown = sorted(set(version_by_name) - set(requested))
    if unknown:
        raise ValidationError(
            "registry_spec_invalid",
            "Prompt pins must name prompts declared by the Agent spec or the system policy",
            names=unknown,
        )
    pins: list[dict[str, Any]] = []
    for name in requested:
        definition = await session.scalar(
            select(PromptDefinition).where(
                PromptDefinition.organization_id == organization_id,
                PromptDefinition.name == name,
            )
        )
        if definition is None:
            raise NotFoundError("Prompt", name)
        version_number = version_by_name.get(name)
        statement = select(PromptVersion).where(
            PromptVersion.organization_id == organization_id,
            PromptVersion.prompt_id == definition.id,
        )
        if version_number is None:
            statement = statement.order_by(PromptVersion.version.desc())
        else:
            statement = statement.where(PromptVersion.version == version_number)
        version = await session.scalar(statement.limit(1))
        if version is None:
            raise NotFoundError("Prompt version", version_number or name)
        pins.append(pin_record(definition, version))
    return pins


async def load_pinned_templates(
    session: AsyncSession,
    organization_id: UUID,
    pins: Sequence[Mapping[str, Any]],
) -> list[tuple[dict[str, Any], str, dict[str, Any]]]:
    if not pins:
        return [
            (
                {
                    "name": SYSTEM_POLICY_PROMPT_NAME,
                    "version": 0,
                    "checksum_sha256": prompt_checksum(
                        DEFAULT_SYSTEM_POLICY_TEMPLATE, DEFAULT_SYSTEM_POLICY_SCHEMA
                    ),
                },
                DEFAULT_SYSTEM_POLICY_TEMPLATE,
                dict(DEFAULT_SYSTEM_POLICY_SCHEMA),
            )
        ]
    loaded: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
    for pin in pins:
        try:
            version_id = UUID(str(pin["version_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ConflictError(
                "prompt_pin_mismatch",
                "Pinned prompt does not match the immutable PromptVersion",
            ) from exc
        version = await session.scalar(
            select(PromptVersion).where(
                PromptVersion.organization_id == organization_id,
                PromptVersion.id == version_id,
            )
        )
        checksum = str(pin.get("checksum_sha256") or "")
        if version is None or version.checksum_sha256 != checksum:
            raise ConflictError(
                "prompt_pin_mismatch",
                "Pinned prompt does not match the immutable PromptVersion",
            )
        schema = dict(version.variables_schema or {})
        loaded.append((dict(pin), version.template, schema))
    return loaded
