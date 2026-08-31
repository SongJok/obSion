from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import (
    CreateEvaluationCaseRequest,
    CreateEvaluationDatasetRequest,
    StartEvaluationRunRequest,
)
from obsion.application.evaluations import EvaluationService
from obsion.config import Settings
from obsion.db.models import (
    AgentDefinition,
    AgentVersion,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationRun,
    ModelProfile,
    PromptDefinition,
    PromptVersion,
)
from obsion.domain.enums import RegistryStatus
from obsion.security.identity import Principal
from obsion.telemetry import evaluation_counter


class EvalExperienceService:
    """Workbench Eval console over EvaluationService. Does not run Harness."""

    def __init__(self, settings: Settings) -> None:
        self.evaluations = EvaluationService(settings)

    async def catalog(self, session: AsyncSession, principal: Principal) -> dict[str, Any]:
        datasets = await self.evaluations.list_datasets(session, principal)
        runs = await self.evaluations.list_runs(session, principal, None)
        evaluation_counter.add(1, {"operation": "catalog"})
        return {
            "datasets": datasets,
            "runs": runs,
            "agents": await self._promoted_agents(session, principal.organization_id),
            "prompts": await self._prompt_versions(session, principal.organization_id),
            "model_profiles": await self._model_profiles(session, principal.organization_id),
        }

    async def create_dataset(
        self,
        session: AsyncSession,
        principal: Principal,
        request: CreateEvaluationDatasetRequest,
    ) -> EvaluationDataset:
        return await self.evaluations.create_dataset(session, principal, request)

    async def add_case(
        self,
        session: AsyncSession,
        principal: Principal,
        dataset_id: UUID,
        request: CreateEvaluationCaseRequest,
    ) -> EvaluationCase:
        return await self.evaluations.add_case(session, principal, dataset_id, request)

    async def list_cases(
        self, session: AsyncSession, principal: Principal, dataset_id: UUID
    ) -> list[EvaluationCase]:
        return await self.evaluations.list_cases(session, principal, dataset_id)

    async def start_run(
        self,
        session: AsyncSession,
        principal: Principal,
        dataset_id: UUID,
        request: StartEvaluationRunRequest,
    ) -> EvaluationRun:
        return await self.evaluations.run(session, principal, dataset_id, request)

    async def list_runs(
        self, session: AsyncSession, principal: Principal, dataset_id: UUID | None
    ) -> list[EvaluationRun]:
        return await self.evaluations.list_runs(session, principal, dataset_id)

    async def get_run(
        self, session: AsyncSession, principal: Principal, run_id: UUID
    ) -> EvaluationRun:
        return await self.evaluations.get_run(session, principal, run_id)

    async def list_results(
        self, session: AsyncSession, principal: Principal, run_id: UUID
    ) -> list[EvaluationCaseResult]:
        return await self.evaluations.list_results(session, principal, run_id)

    async def compare(
        self,
        session: AsyncSession,
        principal: Principal,
        baseline_run_id: UUID,
        candidate_run_id: UUID,
    ) -> dict[str, Any]:
        return await self.evaluations.compare_runs(
            session, principal, baseline_run_id, candidate_run_id
        )

    @staticmethod
    async def _promoted_agents(
        session: AsyncSession, organization_id: UUID
    ) -> list[dict[str, Any]]:
        rows = (
            await session.execute(
                select(AgentDefinition, AgentVersion)
                .join(AgentVersion, AgentVersion.agent_id == AgentDefinition.id)
                .where(
                    AgentDefinition.organization_id == organization_id,
                    AgentDefinition.status == RegistryStatus.ACTIVE,
                    AgentDefinition.active_version == AgentVersion.version,
                )
                .order_by(AgentDefinition.name)
            )
        ).all()
        return [
            {
                "name": definition.name,
                "version": version.version,
                "version_id": version.id,
                "checksum_sha256": version.checksum_sha256,
            }
            for definition, version in (row._tuple() for row in rows)
        ]

    @staticmethod
    async def _prompt_versions(
        session: AsyncSession, organization_id: UUID
    ) -> list[dict[str, Any]]:
        rows = (
            await session.execute(
                select(PromptDefinition, PromptVersion)
                .join(PromptVersion, PromptVersion.prompt_id == PromptDefinition.id)
                .where(
                    PromptDefinition.organization_id == organization_id,
                    PromptVersion.organization_id == organization_id,
                )
                .order_by(PromptDefinition.name, PromptVersion.version.desc())
            )
        ).all()
        return [
            {
                "name": definition.name,
                "version": version.version,
                "version_id": version.id,
                "checksum_sha256": version.checksum_sha256,
            }
            for definition, version in (row._tuple() for row in rows)
        ]

    @staticmethod
    async def _model_profiles(session: AsyncSession, organization_id: UUID) -> list[dict[str, Any]]:
        profiles = await session.scalars(
            select(ModelProfile)
            .where(
                ModelProfile.organization_id == organization_id,
                ModelProfile.enabled.is_(True),
            )
            .order_by(ModelProfile.name)
        )
        return [{"id": item.id, "name": item.name} for item in profiles]
