from collections import defaultdict
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import (
    CreateEvaluationCaseRequest,
    CreateEvaluationDatasetRequest,
    StartEvaluationRunRequest,
)
from obsion.common.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from obsion.common.time import utc_now
from obsion.config import Settings
from obsion.db.models import (
    AgentDefinition,
    AgentVersion,
    CapabilityDefinition,
    CapabilityVersion,
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationDataset,
    EvaluationRun,
    ModelCall,
    ModelEndpoint,
    ModelProfile,
    ModelProfileEndpoint,
    PromptDefinition,
    PromptVersion,
    Run,
    RunStep,
    SkillDefinition,
    SkillVersion,
)
from obsion.domain.enums import ActorType, EvaluationResultStatus
from obsion.evaluations.contracts import (
    infer_evaluator,
    validate_case_request,
    validate_run_bindings,
    validate_score_thresholds,
)
from obsion.evaluations.engine import EvaluationEngine, canonical_sha256
from obsion.persistence.audit import AuditDraft, AuditWriter
from obsion.security.identity import Principal
from obsion.security.redaction import redact
from obsion.telemetry import evaluation_case_duration, evaluation_counter


class EvaluationService:
    def __init__(self, settings: Settings) -> None:
        self.audit = AuditWriter()
        self.engine = EvaluationEngine(settings)

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
        evaluator = request.evaluator or infer_evaluator(request)
        validate_case_request(evaluator, request)
        case = EvaluationCase(
            organization_id=principal.organization_id,
            dataset_id=dataset_id,
            external_id=request.external_id,
            version=request.version,
            evaluator=evaluator,
            input_payload=cast(dict[str, Any], redact(request.input_payload)),
            expected=cast(dict[str, Any], redact(request.expected)),
            fixtures=cast(dict[str, Any], redact(request.fixtures)),
            created_at=utc_now(),
        )
        session.add(case)
        await session.flush()
        await self._audit(
            session,
            principal,
            "evaluation.case.create",
            case.id,
            metadata={"evaluator": evaluator, "external_id": case.external_id},
        )
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
        dataset = await self._require_dataset(session, principal, dataset_id)
        agent, agent_definition = await self._require_agent(
            session, principal, request.agent_version_id
        )
        profile = await session.scalar(
            select(ModelProfile).where(
                ModelProfile.id == request.model_profile_id,
                ModelProfile.organization_id == principal.organization_id,
            )
        )
        if profile is None:
            raise NotFoundError("Model profile", request.model_profile_id)
        cases = await self.list_cases(session, principal, dataset_id)
        if not cases:
            raise ValidationError(
                "evaluation_dataset_empty", "An evaluation dataset must contain at least one case"
            )
        score_thresholds = validate_score_thresholds(request.score_thresholds)
        run_bindings = validate_run_bindings(request.run_bindings)
        required_run_refs = {
            str(item.input_payload["run_ref"])
            for item in cases
            if isinstance(item.input_payload.get("run_ref"), str)
        }
        missing_run_refs = sorted(required_run_refs - set(run_bindings))
        if missing_run_refs:
            raise ValidationError(
                "evaluation_run_binding_required",
                "Every Golden Dataset run_ref must be bound to a terminal Run",
                run_refs=missing_run_refs,
            )
        dataset_snapshot_sha256 = self._dataset_snapshot(dataset, cases)
        configuration = await self._configuration_snapshot(
            session,
            principal,
            agent,
            agent_definition,
            profile,
            request.application_revision,
            run_bindings,
        )
        baseline = await self._require_baseline(
            session,
            principal,
            request.baseline_run_id,
            dataset_id=dataset_id,
            dataset_snapshot_sha256=dataset_snapshot_sha256,
        )
        gate_configuration = {
            "minimum_pass_rate": request.minimum_pass_rate,
            "maximum_regression_rate": request.maximum_regression_rate,
            "score_thresholds": score_thresholds,
        }
        snapshot_sha256 = canonical_sha256(
            {
                "dataset_snapshot_sha256": dataset_snapshot_sha256,
                "configuration": configuration,
                "gate": gate_configuration,
            }
        )
        evaluation = EvaluationRun(
            organization_id=principal.organization_id,
            dataset_id=dataset_id,
            agent_version_id=request.agent_version_id,
            model_profile_id=request.model_profile_id,
            application_revision=request.application_revision,
            status="RUNNING",
            requested_by=principal.id,
            baseline_run_id=baseline.id if baseline else None,
            dataset_snapshot_sha256=dataset_snapshot_sha256,
            snapshot_sha256=snapshot_sha256,
            configuration_snapshot=configuration,
            gate_passed=None,
            metrics={},
            started_at=utc_now(),
        )
        session.add(evaluation)
        await session.flush()

        results: list[EvaluationCaseResult] = []
        for ordinal, case in enumerate(cases, start=1):
            outcome = await self.engine.evaluate(
                session,
                principal.organization_id,
                case,
                agent_version_id=request.agent_version_id,
                model_profile_id=request.model_profile_id,
                run_bindings=run_bindings,
            )
            result = EvaluationCaseResult(
                organization_id=principal.organization_id,
                evaluation_run_id=evaluation.id,
                evaluation_case_id=case.id,
                ordinal=ordinal,
                external_id=case.external_id,
                case_version=case.version,
                evaluator=case.evaluator,
                status=outcome.status,
                case_snapshot_sha256=self._case_snapshot(case),
                checks=outcome.checks,
                scores=outcome.scores,
                observed=outcome.observed,
                evidence_refs=outcome.evidence_refs,
                error_code=outcome.error_code,
                error_message=outcome.error_message,
                duration_ms=outcome.duration_ms,
                created_at=utc_now(),
            )
            session.add(result)
            results.append(result)
        await session.flush()

        baseline_results = (
            list(
                await session.scalars(
                    select(EvaluationCaseResult).where(
                        EvaluationCaseResult.organization_id == principal.organization_id,
                        EvaluationCaseResult.evaluation_run_id == baseline.id,
                    )
                )
            )
            if baseline
            else []
        )
        metrics, gate_passed = self._aggregate(
            results,
            baseline_results,
            gate_configuration=gate_configuration,
        )
        evaluation.status = "COMPLETED"
        evaluation.gate_passed = gate_passed
        evaluation.metrics = metrics
        evaluation.completed_at = utc_now()
        evaluation_counter.add(1, {"gate": "PASSED" if gate_passed else "FAILED"})
        for result in results:
            evaluation_case_duration.record(
                result.duration_ms,
                {"evaluator": result.evaluator.value, "status": result.status.value},
            )
        await self._audit(
            session,
            principal,
            "evaluation.run.complete",
            evaluation.id,
            outcome="SUCCESS" if gate_passed else "REGRESSION",
            metadata={
                "dataset_snapshot_sha256": dataset_snapshot_sha256,
                "snapshot_sha256": snapshot_sha256,
                "gate_passed": gate_passed,
                "baseline_run_id": str(baseline.id) if baseline else None,
            },
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

    async def get_run(
        self, session: AsyncSession, principal: Principal, run_id: UUID
    ) -> EvaluationRun:
        self._require_read(principal)
        run = await session.scalar(
            select(EvaluationRun).where(
                EvaluationRun.id == run_id,
                EvaluationRun.organization_id == principal.organization_id,
            )
        )
        if run is None:
            raise NotFoundError("Evaluation run", run_id)
        return run

    async def list_results(
        self, session: AsyncSession, principal: Principal, run_id: UUID
    ) -> list[EvaluationCaseResult]:
        await self.get_run(session, principal, run_id)
        return list(
            await session.scalars(
                select(EvaluationCaseResult)
                .where(
                    EvaluationCaseResult.organization_id == principal.organization_id,
                    EvaluationCaseResult.evaluation_run_id == run_id,
                )
                .order_by(EvaluationCaseResult.ordinal)
            )
        )

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
    async def _require_agent(
        session: AsyncSession,
        principal: Principal,
        agent_version_id: UUID,
    ) -> tuple[AgentVersion, AgentDefinition]:
        row = (
            await session.execute(
                select(AgentVersion, AgentDefinition)
                .join(AgentDefinition, AgentDefinition.id == AgentVersion.agent_id)
                .where(
                    AgentVersion.id == agent_version_id,
                    AgentVersion.organization_id == principal.organization_id,
                    AgentDefinition.organization_id == principal.organization_id,
                )
            )
        ).one_or_none()
        if row is None:
            raise NotFoundError("Agent version", agent_version_id)
        return row._tuple()

    async def _configuration_snapshot(
        self,
        session: AsyncSession,
        principal: Principal,
        agent: AgentVersion,
        definition: AgentDefinition,
        profile: ModelProfile,
        application_revision: str,
        run_bindings: dict[str, UUID],
    ) -> dict[str, Any]:
        skill_names = self._string_list(agent.spec.get("skills", []))
        capability_names = self._string_list(agent.spec.get("capabilities", []))
        prompt_names = self._string_list(
            agent.spec.get("prompts", agent.spec.get("prompt", []))
        )
        skills = await self._latest_versions(
            session,
            SkillDefinition,
            SkillVersion,
            SkillVersion.skill_id,
            principal.organization_id,
            skill_names,
        )
        capabilities = await self._latest_versions(
            session,
            CapabilityDefinition,
            CapabilityVersion,
            CapabilityVersion.capability_id,
            principal.organization_id,
            capability_names,
        )
        prompts = await self._latest_versions(
            session,
            PromptDefinition,
            PromptVersion,
            PromptVersion.prompt_id,
            principal.organization_id,
            prompt_names,
        )
        endpoint_rows = (
            await session.execute(
                select(ModelProfileEndpoint, ModelEndpoint)
                .join(ModelEndpoint, ModelEndpoint.id == ModelProfileEndpoint.endpoint_id)
                .where(
                    ModelProfileEndpoint.profile_id == profile.id,
                    ModelEndpoint.organization_id == principal.organization_id,
                )
                .order_by(ModelProfileEndpoint.priority, ModelEndpoint.id)
            )
        ).all()
        bound_runs = await self._bound_run_snapshot(
            session, principal.organization_id, run_bindings
        )
        return cast(
            dict[str, Any],
            redact(
                {
                    "application_revision": application_revision,
                    "run_bindings": {
                        name: str(run_id) for name, run_id in run_bindings.items()
                    },
                    "bound_runs": bound_runs,
                    "agent": {
                        "definition_id": str(definition.id),
                        "name": definition.name,
                        "version_id": str(agent.id),
                        "version": agent.version,
                        "checksum_sha256": agent.checksum_sha256,
                    },
                    "skills": skills,
                    "capabilities": capabilities,
                    "prompts": prompts,
                    "model_profile": {
                        "id": str(profile.id),
                        "name": profile.name,
                        "requirements": profile.requirements,
                        "routing_policy": profile.routing_policy,
                        "endpoints": [
                            {
                                "id": str(endpoint.id),
                                "provider": endpoint.provider,
                                "model_id": endpoint.model_id,
                                "region": endpoint.region,
                                "priority": binding.priority,
                            }
                            for binding, endpoint in (row._tuple() for row in endpoint_rows)
                        ],
                    },
                }
            ),
        )

    @staticmethod
    async def _bound_run_snapshot(
        session: AsyncSession,
        organization_id: UUID,
        run_bindings: dict[str, UUID],
    ) -> list[dict[str, Any]]:
        if not run_bindings:
            return []
        run_ids = list(run_bindings.values())
        runs = list(
            await session.scalars(
                select(Run).where(
                    Run.organization_id == organization_id,
                    Run.id.in_(run_ids),
                )
            )
        )
        run_by_id = {item.id: item for item in runs}
        missing = [str(run_id) for run_id in run_ids if run_id not in run_by_id]
        if missing:
            raise NotFoundError("Evaluation source Run", missing[0])
        steps = list(
            await session.scalars(
                select(RunStep).where(
                    RunStep.organization_id == organization_id,
                    RunStep.run_id.in_(run_ids),
                ).order_by(RunStep.run_id, RunStep.ordinal)
            )
        )
        capability_ids = {
            item.capability_version_id
            for item in steps
            if item.capability_version_id is not None
        }
        capability_rows = (
            (
                await session.execute(
                    select(CapabilityVersion, CapabilityDefinition)
                    .join(
                        CapabilityDefinition,
                        CapabilityDefinition.id == CapabilityVersion.capability_id,
                    )
                    .where(
                        CapabilityVersion.organization_id == organization_id,
                        CapabilityVersion.id.in_(capability_ids),
                    )
                )
            ).all()
            if capability_ids
            else []
        )
        capability_by_id = {
            version.id: {
                "version_id": str(version.id),
                "name": definition.name,
                "version": version.version,
                "checksum_sha256": version.checksum_sha256,
            }
            for version, definition in (row._tuple() for row in capability_rows)
        }
        model_rows = (
            await session.execute(
                select(ModelCall, ModelEndpoint)
                .join(ModelEndpoint, ModelEndpoint.id == ModelCall.endpoint_id)
                .where(
                    ModelCall.organization_id == organization_id,
                    ModelCall.run_id.in_(run_ids),
                )
                .order_by(ModelCall.run_id, ModelCall.created_at, ModelCall.id)
            )
        ).all()
        model_by_run: dict[UUID, list[dict[str, Any]]] = defaultdict(list)
        for row in model_rows:
            call, endpoint = row._tuple()
            if call.run_id is not None:
                model_by_run[call.run_id].append(
                    {
                        "model_call_id": str(call.id),
                        "endpoint_id": str(endpoint.id),
                        "provider": endpoint.provider,
                        "model_id": endpoint.model_id,
                        "operation": call.operation,
                    }
                )
        snapshots: list[dict[str, Any]] = []
        steps_by_run: dict[UUID, list[RunStep]] = defaultdict(list)
        for step in steps:
            steps_by_run[step.run_id].append(step)
        for run_ref, run_id in sorted(run_bindings.items()):
            run = run_by_id[run_id]
            versions = [
                capability_by_id[step.capability_version_id]
                for step in steps_by_run[run_id]
                if step.capability_version_id in capability_by_id
            ]
            snapshots.append(
                {
                    "run_ref": run_ref,
                    "run_id": str(run.id),
                    "status": run.status,
                    "agent_version_id": str(run.agent_version_id)
                    if run.agent_version_id
                    else None,
                    "model_profile_id": str(run.model_profile_id)
                    if run.model_profile_id
                    else None,
                    "capability_versions": versions,
                    "model_calls": sorted(
                        model_by_run.get(run.id, []), key=lambda item: item["model_call_id"]
                    ),
                }
            )
        return snapshots

    @staticmethod
    async def _latest_versions(
        session: AsyncSession,
        definition_model: Any,
        version_model: Any,
        version_foreign_key: Any,
        organization_id: UUID,
        names: list[str],
    ) -> list[dict[str, Any]]:
        if not names:
            return []
        rows = (
            await session.execute(
                select(definition_model, version_model)
                .join(version_model, version_foreign_key == definition_model.id)
                .where(
                    definition_model.organization_id == organization_id,
                    version_model.organization_id == organization_id,
                    definition_model.name.in_(names),
                )
                .order_by(definition_model.name, version_model.version.desc())
            )
        ).all()
        latest: dict[str, dict[str, Any]] = {}
        for row in rows:
            definition, version = row._tuple()
            latest.setdefault(
                definition.name,
                {
                    "definition_id": str(definition.id),
                    "name": definition.name,
                    "version_id": str(version.id),
                    "version": version.version,
                    "checksum_sha256": version.checksum_sha256,
                },
            )
        unresolved = sorted(set(names) - set(latest))
        if unresolved:
            raise ConflictError(
                "evaluation_registry_dependency_unresolved",
                "The pinned agent references registry dependencies that cannot be resolved",
                names=unresolved,
            )
        return [latest[name] for name in sorted(latest)]

    @staticmethod
    async def _require_baseline(
        session: AsyncSession,
        principal: Principal,
        baseline_run_id: UUID | None,
        *,
        dataset_id: UUID,
        dataset_snapshot_sha256: str,
    ) -> EvaluationRun | None:
        if baseline_run_id is None:
            return None
        baseline = await session.scalar(
            select(EvaluationRun).where(
                EvaluationRun.id == baseline_run_id,
                EvaluationRun.organization_id == principal.organization_id,
            )
        )
        if baseline is None:
            raise NotFoundError("Evaluation baseline", baseline_run_id)
        if baseline.status != "COMPLETED":
            raise ConflictError(
                "evaluation_baseline_not_completed",
                "An evaluation baseline must be completed",
            )
        if (
            baseline.dataset_id != dataset_id
            or baseline.dataset_snapshot_sha256 != dataset_snapshot_sha256
        ):
            raise ConflictError(
                "evaluation_baseline_snapshot_mismatch",
                "A baseline must use the exact same immutable dataset snapshot",
            )
        return baseline

    @staticmethod
    def _aggregate(
        results: list[EvaluationCaseResult],
        baseline_results: list[EvaluationCaseResult],
        *,
        gate_configuration: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        total = len(results)
        passed = sum(item.status == EvaluationResultStatus.PASSED for item in results)
        failed = sum(item.status == EvaluationResultStatus.FAILED for item in results)
        errors = sum(item.status == EvaluationResultStatus.ERROR for item in results)
        pass_rate = round(passed / total, 4) if total else 0.0
        score_values: dict[str, list[float]] = defaultdict(list)
        evaluator_counts: dict[str, int] = defaultdict(int)
        for item in results:
            evaluator_counts[item.evaluator.value] += 1
            for name, value in item.scores.items():
                if isinstance(value, int | float):
                    score_values[name].append(float(value))
        score_averages = {
            name: round(sum(values) / len(values), 4)
            for name, values in sorted(score_values.items())
            if values
        }
        baseline_by_case = {
            (item.external_id, item.case_version): item for item in baseline_results
        }
        regressions: list[str] = []
        improvements: list[str] = []
        for item in results:
            previous = baseline_by_case.get((item.external_id, item.case_version))
            if previous is None:
                continue
            name = f"{item.external_id}@{item.case_version}"
            if (
                previous.status == EvaluationResultStatus.PASSED
                and item.status != EvaluationResultStatus.PASSED
            ):
                regressions.append(name)
            elif (
                previous.status != EvaluationResultStatus.PASSED
                and item.status == EvaluationResultStatus.PASSED
            ):
                improvements.append(name)
        regression_rate = round(len(regressions) / total, 4) if total else 0.0
        reasons: list[str] = []
        minimum_pass_rate = float(gate_configuration["minimum_pass_rate"])
        maximum_regression_rate = float(gate_configuration["maximum_regression_rate"])
        if pass_rate < minimum_pass_rate:
            reasons.append("minimum_pass_rate")
        if regression_rate > maximum_regression_rate:
            reasons.append("maximum_regression_rate")
        if errors:
            reasons.append("case_errors")
        for name, threshold in gate_configuration["score_thresholds"].items():
            if score_averages.get(name, 0.0) < threshold:
                reasons.append(f"score:{name}")
        gate_passed = not reasons
        metrics = {
            "total": total,
            "passed": passed,
            "failed": failed,
            "errors": errors,
            "pass_rate": pass_rate,
            "score_averages": score_averages,
            "evaluator_counts": dict(sorted(evaluator_counts.items())),
            "baseline": {
                "compared": bool(baseline_results),
                "regressions": sorted(regressions),
                "improvements": sorted(improvements),
                "regression_rate": regression_rate,
            },
            "gate": {
                **gate_configuration,
                "passed": gate_passed,
                "reasons": reasons,
            },
        }
        return metrics, gate_passed

    @staticmethod
    def _dataset_snapshot(dataset: EvaluationDataset, cases: list[EvaluationCase]) -> str:
        return canonical_sha256(
            {
                "dataset": {
                    "name": dataset.name,
                    "domain": dataset.domain,
                },
                "cases": [EvaluationService._case_contract(item) for item in cases],
            }
        )

    @staticmethod
    def _case_snapshot(case: EvaluationCase) -> str:
        return canonical_sha256(EvaluationService._case_contract(case))

    @staticmethod
    def _case_contract(case: EvaluationCase) -> dict[str, Any]:
        return {
            "external_id": case.external_id,
            "version": case.version,
            "evaluator": case.evaluator,
            "input_payload": case.input_payload,
            "expected": case.expected,
            "fixtures": case.fixtures,
        }

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if value is None:
            return []
        return [str(value)]

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
        metadata: dict[str, Any] | None = None,
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
                metadata=metadata or {},
            ),
        )
