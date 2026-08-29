from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import NotFoundError
from obsion.db.models import (
    AgentDefinition,
    AgentVersion,
    SkillDefinition,
    SkillVersion,
)
from obsion.domain.enums import RegistryStatus


@dataclass(frozen=True, slots=True)
class RouteSelection:
    agent_version: AgentVersion
    agent_definition: AgentDefinition
    skill_definition: SkillDefinition | None = None
    skill_version: SkillVersion | None = None


class AgentRouter:
    """Resolve internal specialist routes without exposing agent choice to users."""

    _SPECIALISTS: dict[str, tuple[str, str]] = {
        "DATA": ("data-agent", "governed-analytics"),
        "KNOWLEDGE": ("knowledge-agent", "knowledge-qa"),
        "INCIDENT": ("incident-agent", "incident-investigation"),
    }

    async def resolve(
        self,
        session: AsyncSession,
        organization_id: UUID,
        route: str,
        *,
        fallback: RouteSelection,
    ) -> RouteSelection:
        target = self._SPECIALISTS.get(route)
        if target is None:
            return fallback
        agent_name, skill_name = target
        row = (
            await session.execute(
                select(AgentVersion, AgentDefinition)
                .join(AgentDefinition, AgentDefinition.id == AgentVersion.agent_id)
                .where(
                    AgentVersion.organization_id == organization_id,
                    AgentDefinition.organization_id == organization_id,
                    AgentDefinition.name == agent_name,
                    AgentDefinition.status == RegistryStatus.ACTIVE,
                )
                .order_by(AgentVersion.version.desc())
                .limit(1)
            )
        ).one_or_none()
        if row is None:
            raise NotFoundError("Specialist agent", agent_name)
        agent_version, agent_definition = row._tuple()
        skill_row = (
            await session.execute(
                select(SkillVersion, SkillDefinition)
                .join(SkillDefinition, SkillDefinition.id == SkillVersion.skill_id)
                .where(
                    SkillVersion.organization_id == organization_id,
                    SkillDefinition.organization_id == organization_id,
                    SkillDefinition.name == skill_name,
                    SkillDefinition.status == RegistryStatus.ACTIVE,
                )
                .order_by(SkillVersion.version.desc())
                .limit(1)
            )
        ).one_or_none()
        if skill_row is None:
            raise NotFoundError("Specialist skill", skill_name)
        skill_version, skill_definition = skill_row._tuple()
        return RouteSelection(
            agent_version=agent_version,
            agent_definition=agent_definition,
            skill_definition=skill_definition,
            skill_version=skill_version,
        )

    @staticmethod
    def skill_snapshot(selection: RouteSelection) -> dict[str, Any] | None:
        if selection.skill_version is None or selection.skill_definition is None:
            return None
        spec = selection.skill_version.spec
        return {
            "name": selection.skill_definition.name,
            "version": selection.skill_version.version,
            "checksum_sha256": selection.skill_version.checksum_sha256,
            "instructions": list(spec.get("instructions", []))
            if isinstance(spec.get("instructions", []), list)
            else [],
            "required_evidence": list(spec.get("requiredEvidence", []))
            if isinstance(spec.get("requiredEvidence", []), list)
            else [],
            "verification": list(spec.get("verification", []))
            if isinstance(spec.get("verification", []), list)
            else [],
        }
