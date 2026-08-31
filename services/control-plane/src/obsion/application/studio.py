from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import AuthorizationError, NotFoundError, ValidationError
from obsion.common.time import utc_now
from obsion.db.models import (
    AgentDefinition,
    AgentVersion,
    PromptDefinition,
    PromptVersion,
    SkillDefinition,
    SkillVersion,
)
from obsion.domain.enums import ActorType, RegistryStatus
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.registry.manifests import RegistryManifestError, parse_registry_text
from obsion.security.identity import Principal
from obsion.telemetry import studio_counter

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,79}$")
_PUBLISHABLE = frozenset({"Agent", "Skill"})
_COMPARABLE = frozenset({"Agent", "Skill", "Prompt"})
_ROLLBACKABLE = frozenset({"Agent", "Skill"})
_SECRET_PATH_FRAGMENTS = (
    "password",
    "passwd",
    "secret",
    "token",
    "credential",
    "api_key",
    "apikey",
)
PROMPT_CUTOVER_MESSAGE = (
    "Prompt versions are immutable snapshots; publish a replacement instead of "
    "editing production. Each Turn pins a published snapshot; templates are not rewritten."
)
EVAL_COMPARE_HINT = (
    "Pin each version on separate Evaluation Runs of the same Golden Dataset snapshot. "
    "fixtures.actual is rejected. Runtime traffic is not split."
)


def spec_checksum(value: dict[str, Any]) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def mapping_diff(left: Any, right: Any, *, path: str = "$") -> list[dict[str, Any]]:
    if isinstance(left, dict) and isinstance(right, dict):
        changes: list[dict[str, Any]] = []
        keys = sorted(set(left) | set(right))
        for key in keys:
            child = f"{path}.{key}"
            if key not in left:
                changes.append(_change(child, None, right[key]))
            elif key not in right:
                changes.append(_change(child, left[key], None))
            else:
                changes.extend(mapping_diff(left[key], right[key], path=child))
        return changes
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            return [_change(path, left, right)]
        changes = []
        for index, (left_item, right_item) in enumerate(zip(left, right, strict=True)):
            changes.extend(mapping_diff(left_item, right_item, path=f"{path}[{index}]"))
        return changes
    if left == right:
        return []
    return [_change(path, left, right)]


def _change(path: str, baseline: Any, candidate: Any) -> dict[str, Any]:
    return {
        "path": path,
        "baseline": _redact(path, baseline),
        "candidate": _redact(path, candidate),
    }


def _redact(path: str, value: Any) -> Any:
    lowered = path.casefold()
    if any(fragment in lowered for fragment in _SECRET_PATH_FRAGMENTS):
        return "[redacted]"
    return value


class StudioService:
    """Validate and publish immutable Agent/Skill versions. Does not run Harness."""

    def __init__(self) -> None:
        self.audit = AuditWriter()

    async def catalog(
        self, session: AsyncSession, principal: Principal
    ) -> dict[str, list[dict[str, Any]]]:
        self._require_read(principal)
        agents = await self._list_agent_versions(session, principal.organization_id)
        skills = await self._list_skill_versions(session, principal.organization_id)
        studio_counter.add(1, {"operation": "catalog"})
        return {"agents": agents, "skills": skills}

    def validate(self, principal: Principal, document: str) -> dict[str, Any]:
        self._require_read(principal)
        kind, name, spec = self._parse(document)
        studio_counter.add(1, {"operation": "validate", "kind": kind})
        return self._preview(kind, name, spec)

    async def publish(
        self, session: AsyncSession, principal: Principal, expected_kind: str, document: str
    ) -> tuple[dict[str, Any], bool]:
        self._require_write(principal)
        kind, name, spec = self._parse(document)
        if kind != expected_kind:
            raise ValidationError(
                "registry_spec_invalid",
                f"Studio expected a {expected_kind} manifest",
            )
        if kind not in _PUBLISHABLE:
            raise ValidationError(
                "registry_spec_invalid",
                "Studio publishes Agent and Skill manifests",
            )
        if _NAME_PATTERN.fullmatch(name) is None:
            raise ValidationError(
                "registry_spec_invalid",
                "Registry names must be lowercase hyphenated identifiers",
            )
        checksum = spec_checksum(spec)
        if kind == "Agent":
            view, created = await self._publish_agent(session, principal, name, spec, checksum)
        else:
            view, created = await self._publish_skill(session, principal, name, spec, checksum)
        studio_counter.add(1, {"operation": "publish", "kind": kind})
        return view, created

    async def promote(
        self,
        session: AsyncSession,
        principal: Principal,
        kind: str,
        name: str,
        version: int,
    ) -> dict[str, Any]:
        self._require_write(principal)
        if kind not in _PUBLISHABLE:
            raise ValidationError(
                "registry_spec_invalid",
                "Studio promotes Agent and Skill versions",
            )
        now = utc_now()
        if kind == "Agent":
            definition = await session.scalar(
                select(AgentDefinition).where(
                    AgentDefinition.organization_id == principal.organization_id,
                    AgentDefinition.name == name,
                )
            )
            if definition is None:
                raise NotFoundError("Agent", name)
            row = await session.scalar(
                select(AgentVersion).where(
                    AgentVersion.organization_id == principal.organization_id,
                    AgentVersion.agent_id == definition.id,
                    AgentVersion.version == version,
                )
            )
            if row is None:
                raise NotFoundError("Agent version", version)
            definition.active_version = version
            definition.status = RegistryStatus.ACTIVE
            if kind == "Agent":
                row.promoted_at = now
            await self._audit(
                session,
                principal,
                "registry.agent.promote",
                "agent_version",
                row.id,
                {"name": name, "version": version, "promoted_at": now.isoformat()},
            )
            studio_counter.add(1, {"operation": "promote", "kind": kind})
            return self._agent_view(definition, row)
        definition = await session.scalar(
            select(SkillDefinition).where(
                SkillDefinition.organization_id == principal.organization_id,
                SkillDefinition.name == name,
            )
        )
        if definition is None:
            raise NotFoundError("Skill", name)
        row = await session.scalar(
            select(SkillVersion).where(
                SkillVersion.organization_id == principal.organization_id,
                SkillVersion.skill_id == definition.id,
                SkillVersion.version == version,
            )
        )
        if row is None:
            raise NotFoundError("Skill version", version)
        definition.active_version = version
        definition.status = RegistryStatus.ACTIVE
        await self._audit(
            session,
            principal,
            "registry.skill.promote",
            "skill_version",
            row.id,
            {"name": name, "version": version, "promoted_at": now.isoformat()},
        )
        studio_counter.add(1, {"operation": "promote", "kind": kind})
        return self._skill_view(definition, row)

    async def rollback(
        self,
        session: AsyncSession,
        principal: Principal,
        kind: str,
        name: str,
        version: int,
    ) -> dict[str, Any]:
        self._require_write(principal)
        if kind not in _ROLLBACKABLE:
            raise ValidationError("registry_spec_invalid", PROMPT_CUTOVER_MESSAGE)
        definition, _row = await self._load_registry_version(
            session, principal, kind, name, version
        )
        current = definition.active_version
        if current is None:
            raise ValidationError(
                "registry_spec_invalid",
                "A version can be rolled back only after an active cutover",
            )
        if current == version:
            raise ValidationError(
                "registry_spec_invalid",
                "Rollback requires a previously published version, not the active cutover",
            )
        view = await self.promote(session, principal, kind, name, version)
        await self._audit(
            session,
            principal,
            f"registry.{kind.casefold()}.rollback",
            f"{kind.casefold()}_version",
            view["version_id"],
            {
                "name": name,
                "from_version": current,
                "to_version": version,
                "checksum_sha256": view["checksum_sha256"],
            },
        )
        studio_counter.add(1, {"operation": "rollback", "kind": kind})
        return view

    async def compare(
        self,
        session: AsyncSession,
        principal: Principal,
        kind: str,
        name: str,
        baseline_version: int,
        candidate_version: int,
    ) -> dict[str, Any]:
        self._require_read(principal)
        if kind not in _COMPARABLE:
            raise ValidationError(
                "registry_spec_invalid",
                "Studio compares Agent, Skill, and Prompt versions",
            )
        if baseline_version == candidate_version:
            raise ValidationError(
                "registry_spec_invalid",
                "Compare requires two distinct versions",
            )
        baseline = await self._version_payload(session, principal, kind, name, baseline_version)
        candidate = await self._version_payload(session, principal, kind, name, candidate_version)
        changes = mapping_diff(baseline["spec"], candidate["spec"])
        studio_counter.add(1, {"operation": "compare", "kind": kind})
        return {
            "kind": kind,
            "name": name,
            "baseline": {
                "version": baseline["version"],
                "checksum_sha256": baseline["checksum_sha256"],
                "promoted": baseline["promoted"],
            },
            "candidate": {
                "version": candidate["version"],
                "checksum_sha256": candidate["checksum_sha256"],
                "promoted": candidate["promoted"],
            },
            "identical": not changes
            and baseline["checksum_sha256"] == candidate["checksum_sha256"],
            "changes": changes,
            "traffic_split": False,
            "evaluation": EVAL_COMPARE_HINT,
        }

    async def _load_registry_version(
        self,
        session: AsyncSession,
        principal: Principal,
        kind: str,
        name: str,
        version: int,
    ) -> tuple[AgentDefinition | SkillDefinition, AgentVersion | SkillVersion]:
        if kind == "Agent":
            definition = await session.scalar(
                select(AgentDefinition).where(
                    AgentDefinition.organization_id == principal.organization_id,
                    AgentDefinition.name == name,
                )
            )
            if definition is None:
                raise NotFoundError("Agent", name)
            row = await session.scalar(
                select(AgentVersion).where(
                    AgentVersion.organization_id == principal.organization_id,
                    AgentVersion.agent_id == definition.id,
                    AgentVersion.version == version,
                )
            )
            if row is None:
                raise NotFoundError("Agent version", version)
            return definition, row
        definition = await session.scalar(
            select(SkillDefinition).where(
                SkillDefinition.organization_id == principal.organization_id,
                SkillDefinition.name == name,
            )
        )
        if definition is None:
            raise NotFoundError("Skill", name)
        row = await session.scalar(
            select(SkillVersion).where(
                SkillVersion.organization_id == principal.organization_id,
                SkillVersion.skill_id == definition.id,
                SkillVersion.version == version,
            )
        )
        if row is None:
            raise NotFoundError("Skill version", version)
        return definition, row

    async def _version_payload(
        self,
        session: AsyncSession,
        principal: Principal,
        kind: str,
        name: str,
        version: int,
    ) -> dict[str, Any]:
        if kind == "Prompt":
            definition = await session.scalar(
                select(PromptDefinition).where(
                    PromptDefinition.organization_id == principal.organization_id,
                    PromptDefinition.name == name,
                )
            )
            if definition is None:
                raise NotFoundError("Prompt", name)
            row = await session.scalar(
                select(PromptVersion).where(
                    PromptVersion.organization_id == principal.organization_id,
                    PromptVersion.prompt_id == definition.id,
                    PromptVersion.version == version,
                )
            )
            if row is None:
                raise NotFoundError("Prompt version", version)
            return {
                "version": row.version,
                "checksum_sha256": row.checksum_sha256,
                "promoted": False,
                "spec": {"template": row.template, "variables_schema": row.variables_schema},
            }
        registry_definition, registry_row = await self._load_registry_version(
            session, principal, kind, name, version
        )
        if kind == "Agent":
            if not isinstance(registry_definition, AgentDefinition) or not isinstance(
                registry_row, AgentVersion
            ):
                raise RuntimeError("Agent registry version type invariant violated")
            view = self._agent_view(registry_definition, registry_row)
        else:
            if not isinstance(registry_definition, SkillDefinition) or not isinstance(
                registry_row, SkillVersion
            ):
                raise RuntimeError("Skill registry version type invariant violated")
            view = self._skill_view(registry_definition, registry_row)
        return {
            "version": view["version"],
            "checksum_sha256": view["checksum_sha256"],
            "promoted": view["promoted"],
            "spec": view["spec"],
        }

    def _parse(self, document: str) -> tuple[str, str, dict[str, Any]]:
        try:
            return parse_registry_text(document, source="Studio")
        except RegistryManifestError as exc:
            raise ValidationError("registry_spec_invalid", str(exc)) from exc

    async def _publish_agent(
        self,
        session: AsyncSession,
        principal: Principal,
        name: str,
        spec: dict[str, Any],
        checksum: str,
    ) -> tuple[dict[str, Any], bool]:
        now = utc_now()
        definition = await session.scalar(
            select(AgentDefinition).where(
                AgentDefinition.organization_id == principal.organization_id,
                AgentDefinition.name == name,
            )
        )
        if definition is None:
            definition = AgentDefinition(
                organization_id=principal.organization_id,
                name=name,
                display_name=name.replace("-", " ").title(),
                description=str(spec.get("description") or ""),
                status=RegistryStatus.DRAFT,
            )
            session.add(definition)
            await session.flush()
        elif definition.status == RegistryStatus.RETIRED:
            raise ValidationError("registry_spec_invalid", "A retired Agent cannot be revised")
        latest = await session.scalar(
            select(AgentVersion)
            .where(
                AgentVersion.organization_id == principal.organization_id,
                AgentVersion.agent_id == definition.id,
            )
            .order_by(AgentVersion.version.desc())
            .limit(1)
        )
        if latest is not None and latest.checksum_sha256 == checksum:
            return self._agent_view(definition, latest), False
        version_number = (latest.version if latest is not None else 0) + 1
        row = AgentVersion(
            organization_id=principal.organization_id,
            agent_id=definition.id,
            version=version_number,
            spec=spec,
            checksum_sha256=checksum,
            created_by=principal.id,
            created_at=now,
        )
        session.add(row)
        await session.flush()
        if spec.get("description"):
            definition.description = str(spec["description"])
        await self._audit(
            session,
            principal,
            "registry.agent.publish",
            "agent_version",
            row.id,
            {"name": name, "version": version_number, "checksum_sha256": checksum},
        )
        return self._agent_view(definition, row), True

    async def _publish_skill(
        self,
        session: AsyncSession,
        principal: Principal,
        name: str,
        spec: dict[str, Any],
        checksum: str,
    ) -> tuple[dict[str, Any], bool]:
        now = utc_now()
        definition = await session.scalar(
            select(SkillDefinition).where(
                SkillDefinition.organization_id == principal.organization_id,
                SkillDefinition.name == name,
            )
        )
        if definition is None:
            definition = SkillDefinition(
                organization_id=principal.organization_id,
                name=name,
                display_name=name.replace("-", " ").title(),
                description=str(spec.get("description") or ""),
                status=RegistryStatus.DRAFT,
            )
            session.add(definition)
            await session.flush()
        elif definition.status == RegistryStatus.RETIRED:
            raise ValidationError("registry_spec_invalid", "A retired Skill cannot be revised")
        latest = await session.scalar(
            select(SkillVersion)
            .where(
                SkillVersion.organization_id == principal.organization_id,
                SkillVersion.skill_id == definition.id,
            )
            .order_by(SkillVersion.version.desc())
            .limit(1)
        )
        if latest is not None and latest.checksum_sha256 == checksum:
            return self._skill_view(definition, latest), False
        version_number = (latest.version if latest is not None else 0) + 1
        row = SkillVersion(
            organization_id=principal.organization_id,
            skill_id=definition.id,
            version=version_number,
            spec=spec,
            checksum_sha256=checksum,
            created_by=principal.id,
            created_at=now,
        )
        session.add(row)
        await session.flush()
        await self._audit(
            session,
            principal,
            "registry.skill.publish",
            "skill_version",
            row.id,
            {"name": name, "version": version_number, "checksum_sha256": checksum},
        )
        return self._skill_view(definition, row), True

    async def _list_agent_versions(
        self, session: AsyncSession, organization_id: UUID
    ) -> list[dict[str, Any]]:
        rows = (
            await session.execute(
                select(AgentDefinition, AgentVersion)
                .join(AgentVersion, AgentVersion.agent_id == AgentDefinition.id)
                .where(AgentDefinition.organization_id == organization_id)
                .order_by(AgentDefinition.name, AgentVersion.version.desc())
            )
        ).all()
        return [self._agent_view(definition, version) for definition, version in rows]

    async def _list_skill_versions(
        self, session: AsyncSession, organization_id: UUID
    ) -> list[dict[str, Any]]:
        rows = (
            await session.execute(
                select(SkillDefinition, SkillVersion)
                .join(SkillVersion, SkillVersion.skill_id == SkillDefinition.id)
                .where(SkillDefinition.organization_id == organization_id)
                .order_by(SkillDefinition.name, SkillVersion.version.desc())
            )
        ).all()
        return [self._skill_view(definition, version) for definition, version in rows]

    @staticmethod
    def _preview(kind: str, name: str, spec: dict[str, Any]) -> dict[str, Any]:
        preview: dict[str, Any] = {
            "capabilities": spec.get("capabilities"),
            "skills": spec.get("skills"),
            "riskPolicy": spec.get("riskPolicy"),
            "sandbox": spec.get("sandbox"),
            "modelPolicy": spec.get("modelPolicy"),
            "requiredEvidence": spec.get("requiredEvidence"),
            "verification": spec.get("verification"),
        }
        if kind == "Workflow":
            steps = spec.get("steps")
            preview = {
                "steps": [
                    {"id": item.get("id"), "type": item.get("type"), "name": item.get("name")}
                    for item in steps
                    if isinstance(item, dict)
                ]
                if isinstance(steps, list)
                else [],
            }
        if kind == "Connector":
            preview = {
                "type": spec.get("type"),
                "environment": spec.get("environment"),
                "transport": spec.get("transport"),
                "grants": spec.get("grants"),
                "allowedEgress": spec.get("allowedEgress"),
                "hasCredentialRef": bool(spec.get("credentialRef")),
            }
        return {
            "kind": kind,
            "name": name,
            "checksum_sha256": spec_checksum(spec),
            "preview": preview,
        }

    @staticmethod
    def _agent_view(definition: AgentDefinition, version: AgentVersion) -> dict[str, Any]:
        return {
            "kind": "Agent",
            "name": definition.name,
            "display_name": definition.display_name,
            "description": definition.description,
            "definition_id": definition.id,
            "version_id": version.id,
            "version": version.version,
            "status": definition.status.value,
            "checksum_sha256": version.checksum_sha256,
            "promoted": definition.active_version == version.version,
            "promoted_at": version.promoted_at,
            "spec": version.spec,
        }

    @staticmethod
    def _skill_view(definition: SkillDefinition, version: SkillVersion) -> dict[str, Any]:
        return {
            "kind": "Skill",
            "name": definition.name,
            "display_name": definition.display_name,
            "description": definition.description,
            "definition_id": definition.id,
            "version_id": version.id,
            "version": version.version,
            "status": definition.status.value,
            "checksum_sha256": version.checksum_sha256,
            "promoted": definition.active_version == version.version,
            "promoted_at": None,
            "spec": version.spec,
        }

    @staticmethod
    def _require_read(principal: Principal) -> None:
        if not principal.can("registry.read"):
            raise AuthorizationError(
                "registry_read_denied", "Registry catalog access is not permitted"
            )

    @staticmethod
    def _require_write(principal: Principal) -> None:
        if not principal.can("registry.write"):
            raise AuthorizationError("registry_write_denied", "Registry changes are not permitted")

    async def _audit(
        self,
        session: AsyncSession,
        principal: Principal,
        action: str,
        resource_type: str,
        resource_id: UUID,
        metadata: dict[str, Any],
    ) -> None:
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=resource_id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action=action,
                resource_type=resource_type,
                resource_id=str(resource_id),
                outcome="SUCCESS",
                metadata=metadata,
            ),
        )
