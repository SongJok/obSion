from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import (
    CreateEvaluationCaseRequest,
    CreateEvaluationDatasetRequest,
    StartEvaluationRunRequest,
)
from obsion.common.errors import AuthorizationError, NotFoundError, ValidationError
from obsion.common.time import utc_now
from obsion.config import Settings
from obsion.data_intelligence.sql_policy import SqlPolicyValidator
from obsion.db.models import (
    AgentVersion,
    EvaluationCase,
    EvaluationDataset,
    EvaluationRun,
    ModelProfile,
)
from obsion.domain.enums import ActorType
from obsion.harness.understanding import UnderstandingEngine
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.security.identity import Principal


class EvaluationService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.audit = AuditWriter()

    async def create_dataset(
        self,
        session: AsyncSession,
        principal: Principal,
        request: CreateEvaluationDatasetRequest,
    ) -> EvaluationDataset:
        self._require_write(principal)
        dataset = EvaluationDataset(
            organization_id=principal.organization_id,
            name=request.name,
            description=request.description,
            domain=request.domain,
        )
        session.add(dataset)
        await session.flush()
        await self._audit(session, principal, "evaluation.dataset.create", dataset.id)
        return dataset

    async def list_datasets(
        self, session: AsyncSession, principal: Principal
    ) -> list[EvaluationDataset]:
        self._require_read(principal)
        return list(
            await session.scalars(
                select(EvaluationDataset)
                .where(EvaluationDataset.organization_id == principal.organization_id)
                .order_by(EvaluationDataset.name)
            )
        )

    async def add_case(
        self,
        session: AsyncSession,
        principal: Principal,
        dataset_id: UUID,
        request: CreateEvaluationCaseRequest,
    ) -> EvaluationCase:
        self._require_write(principal)
        await self._require_dataset(session, principal, dataset_id)
        case = EvaluationCase(
            organization_id=principal.organization_id,
            dataset_id=dataset_id,
            external_id=request.external_id,
            version=request.version,
            input_payload=request.input_payload,
            expected=request.expected,
            fixtures=request.fixtures,
            created_at=utc_now(),
        )
        session.add(case)
        await session.flush()
        await self._audit(session, principal, "evaluation.case.create", case.id)
        return case

    async def list_cases(
        self, session: AsyncSession, principal: Principal, dataset_id: UUID
    ) -> list[EvaluationCase]:
        self._require_read(principal)
        await self._require_dataset(session, principal, dataset_id)
        return list(
            await session.scalars(
                select(EvaluationCase)
                .where(
                    EvaluationCase.organization_id == principal.organization_id,
                    EvaluationCase.dataset_id == dataset_id,
                )
                .order_by(EvaluationCase.external_id, EvaluationCase.version)
            )
        )

    async def run(
        self,
        session: AsyncSession,
        principal: Principal,
        dataset_id: UUID,
        request: StartEvaluationRunRequest,
    ) -> EvaluationRun:
        self._require_write(principal)
        await self._require_dataset(session, principal, dataset_id)
        agent_exists = await session.scalar(
            select(AgentVersion.id).where(
                AgentVersion.id == request.agent_version_id,
                AgentVersion.organization_id == principal.organization_id,
            )
        )
        profile_exists = await session.scalar(
            select(ModelProfile.id).where(
                ModelProfile.id == request.model_profile_id,
                ModelProfile.organization_id == principal.organization_id,
            )
        )
        if agent_exists is None or profile_exists is None:
            raise NotFoundError("Agent version or model profile", request.agent_version_id)
        cases = await self.list_cases(session, principal, dataset_id)
        if not cases:
            raise ValidationError(
                "evaluation_dataset_empty", "An evaluation dataset must contain at least one case"
            )
        evaluation = EvaluationRun(
            organization_id=principal.organization_id,
            dataset_id=dataset_id,
            agent_version_id=request.agent_version_id,
            model_profile_id=request.model_profile_id,
            application_revision=request.application_revision,
            status="RUNNING",
            metrics={},
        )
        session.add(evaluation)
        await session.flush()
        results = [self._evaluate_case(case) for case in cases]
        passed = sum(1 for item in results if item["passed"])
        evaluation.status = "COMPLETED"
        evaluation.metrics = {
            "total": len(results),
            "passed": passed,
            "failed": len(results) - passed,
            "pass_rate": round(passed / len(results), 4),
            "results": results,
        }
        await self._audit(
            session,
            principal,
            "evaluation.run.complete",
            evaluation.id,
            outcome="SUCCESS" if passed == len(results) else "REGRESSION",
        )
        return evaluation

    async def list_runs(
        self, session: AsyncSession, principal: Principal, dataset_id: UUID | None
    ) -> list[EvaluationRun]:
        self._require_read(principal)
        statement = select(EvaluationRun).where(
            EvaluationRun.organization_id == principal.organization_id
        )
        if dataset_id is not None:
            statement = statement.where(EvaluationRun.dataset_id == dataset_id)
        return list(
            await session.scalars(statement.order_by(EvaluationRun.created_at.desc()).limit(200))
        )

    def _evaluate_case(self, case: EvaluationCase) -> dict[str, Any]:
        expected = case.expected
        checks: dict[str, bool] = {}
        if "route" in expected:
            question = str(case.input_payload.get("question", ""))
            data_understanding = case.input_payload.get(
                "data_understanding",
                {
                    "domain": "DATA",
                    "intent": "ANALYTICS_QUERY",
                    "metrics": [],
                    "dimensions": [],
                    "time_range": {},
                    "comparison": None,
                    "need_root_cause": False,
                    "risk": "L1",
                },
            )
            actual_route = UnderstandingEngine().route(question, data_understanding)["route"]
            checks["route"] = actual_route == expected["route"]
        if "sql_allowed" in expected:
            validator = SqlPolicyValidator(
                default_limit=self.settings.sql_default_limit,
                max_limit=self.settings.sql_max_limit,
            )
            allowed = True
            try:
                validator.validate(
                    str(case.input_payload.get("sql", "")),
                    dialect=str(case.input_payload.get("dialect", "postgres")),
                    allowed_tables=set(case.fixtures.get("allowed_tables", [])),
                    allowed_columns=set(case.fixtures.get("allowed_columns", [])),
                )
            except ValidationError:
                allowed = False
            checks["sql_allowed"] = allowed is bool(expected["sql_allowed"])
        if "contains" in expected:
            actual = str(case.fixtures.get("actual", ""))
            required = expected["contains"]
            terms = required if isinstance(required, list) else [required]
            checks["contains"] = all(str(term).casefold() in actual.casefold() for term in terms)
        if not checks:
            checks["schema"] = bool(case.input_payload) and bool(case.expected)
        return {
            "case_id": str(case.id),
            "external_id": case.external_id,
            "version": case.version,
            "passed": all(checks.values()),
            "checks": checks,
        }

    async def _require_dataset(
        self, session: AsyncSession, principal: Principal, dataset_id: UUID
    ) -> EvaluationDataset:
        dataset = await session.scalar(
            select(EvaluationDataset).where(
                EvaluationDataset.id == dataset_id,
                EvaluationDataset.organization_id == principal.organization_id,
            )
        )
        if dataset is None:
            raise NotFoundError("Evaluation dataset", dataset_id)
        return dataset

    @staticmethod
    def _require_read(principal: Principal) -> None:
        if not principal.can("evaluations.read"):
            raise AuthorizationError("evaluation_read_denied", "Evaluation access is not permitted")

    @staticmethod
    def _require_write(principal: Principal) -> None:
        if not principal.can("evaluations.write"):
            raise AuthorizationError(
                "evaluation_write_denied", "Evaluation changes are not permitted"
            )

    async def _audit(
        self,
        session: AsyncSession,
        principal: Principal,
        action: str,
        resource_id: UUID,
        *,
        outcome: str = "SUCCESS",
    ) -> None:
        await self.audit.write(
            session,
            AuditDraft(
                organization_id=principal.organization_id,
                correlation_id=resource_id,
                actor_type=ActorType.USER,
                actor_id=principal.id,
                action=action,
                resource_type="evaluation",
                resource_id=str(resource_id),
                outcome=outcome,
            ),
        )
