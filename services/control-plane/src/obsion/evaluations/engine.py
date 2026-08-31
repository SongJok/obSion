import hashlib
import json
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import ConflictError, NotFoundError, ObsionError, ValidationError
from obsion.config import Settings
from obsion.contracts.errors import validate_error_code
from obsion.data_intelligence.sql_policy import SqlPolicyValidator
from obsion.db.models import (
    Artifact,
    Claim,
    ClaimEvidence,
    EvaluationCase,
    Evidence,
    Run,
    RunStep,
)
from obsion.domain.enums import (
    ArtifactKind,
    EvaluationResultStatus,
    EvaluationTarget,
    VerificationStatus,
)
from obsion.domain.run_state import is_terminal
from obsion.harness.understanding import UnderstandingEngine
from obsion.security.redaction import redact


@dataclass(frozen=True, slots=True)
class CaseEvaluation:
    status: EvaluationResultStatus
    checks: dict[str, bool]
    scores: dict[str, float]
    observed: dict[str, Any]
    evidence_refs: list[dict[str, Any]]
    duration_ms: int
    error_code: str | None = None
    error_message: str | None = None

    def __post_init__(self) -> None:
        validate_error_code(self.error_code)


class EvaluationCaseView(Protocol):
    @property
    def evaluator(self) -> EvaluationTarget: ...

    @property
    def input_payload(self) -> dict[str, Any]: ...

    @property
    def expected(self) -> dict[str, Any]: ...

    @property
    def fixtures(self) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class OfflineEvaluationCase:
    evaluator: EvaluationTarget
    input_payload: dict[str, Any]
    expected: dict[str, Any]
    fixtures: dict[str, Any]
    external_id: str = ""


def canonical_sha256(value: Any) -> str:
    serialized = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()


class EvaluationEngine:
    """Execute explicit evaluators against control-plane behavior or a recorded Run."""

    def __init__(self, settings: Settings) -> None:
        self.sql = SqlPolicyValidator(
            default_limit=settings.sql_default_limit,
            max_limit=settings.sql_max_limit,
        )

    async def evaluate(
        self,
        session: AsyncSession,
        organization_id: UUID,
        case: EvaluationCase,
        *,
        agent_version_id: UUID,
        model_profile_id: UUID,
        run_bindings: dict[str, UUID],
    ) -> CaseEvaluation:
        started = perf_counter()
        try:
            if case.evaluator == EvaluationTarget.ROUTING:
                checks, scores, observed, evidence_refs = self._evaluate_routing(case)
            elif case.evaluator == EvaluationTarget.SQL_POLICY:
                checks, scores, observed, evidence_refs = self._evaluate_sql(case)
            elif case.evaluator == EvaluationTarget.RUN_OUTPUT:
                checks, scores, observed, evidence_refs = await self._evaluate_run_output(
                    session,
                    organization_id,
                    case,
                    agent_version_id=agent_version_id,
                    model_profile_id=model_profile_id,
                    run_bindings=run_bindings,
                )
            else:  # pragma: no cover - enum validation prevents this branch
                raise ValidationError(
                    "evaluation_target_unsupported", "The evaluation target is not supported"
                )
            return CaseEvaluation(
                status=(
                    EvaluationResultStatus.PASSED
                    if checks and all(checks.values())
                    else EvaluationResultStatus.FAILED
                ),
                checks=checks,
                scores=scores,
                observed=redact(observed),
                evidence_refs=evidence_refs,
                duration_ms=max(0, int((perf_counter() - started) * 1000)),
            )
        except ObsionError as exc:
            return CaseEvaluation(
                status=EvaluationResultStatus.ERROR,
                checks={},
                scores={},
                observed={},
                evidence_refs=[],
                duration_ms=max(0, int((perf_counter() - started) * 1000)),
                error_code=exc.code,
                error_message=exc.message,
            )
        except Exception:
            return CaseEvaluation(
                status=EvaluationResultStatus.ERROR,
                checks={},
                scores={},
                observed={},
                evidence_refs=[],
                duration_ms=max(0, int((perf_counter() - started) * 1000)),
                error_code="evaluation_executor_failed",
                error_message="The evaluation executor could not complete the case",
            )

    def evaluate_offline(self, case: EvaluationCaseView) -> CaseEvaluation:
        started = perf_counter()
        try:
            if case.evaluator == EvaluationTarget.ROUTING:
                checks, scores, observed, evidence_refs = self._evaluate_routing(case)
            elif case.evaluator == EvaluationTarget.SQL_POLICY:
                checks, scores, observed, evidence_refs = self._evaluate_sql(case)
            else:
                return CaseEvaluation(
                    status=EvaluationResultStatus.FAILED,
                    checks={"offline_evaluator": False},
                    scores={"offline_evaluator_accuracy": 0.0},
                    observed={"evaluator": getattr(case.evaluator, "value", str(case.evaluator))},
                    evidence_refs=[],
                    duration_ms=max(0, int((perf_counter() - started) * 1000)),
                )
            return CaseEvaluation(
                status=(
                    EvaluationResultStatus.PASSED
                    if checks and all(checks.values())
                    else EvaluationResultStatus.FAILED
                ),
                checks=checks,
                scores=scores,
                observed=redact(observed),
                evidence_refs=evidence_refs,
                duration_ms=max(0, int((perf_counter() - started) * 1000)),
            )
        except ObsionError as exc:
            return CaseEvaluation(
                status=EvaluationResultStatus.ERROR,
                checks={},
                scores={},
                observed={},
                evidence_refs=[],
                duration_ms=max(0, int((perf_counter() - started) * 1000)),
                error_code=exc.code,
                error_message=exc.message,
            )

    @staticmethod
    def _evaluate_routing(
        case: EvaluationCaseView,
    ) -> tuple[dict[str, bool], dict[str, float], dict[str, Any], list[dict[str, Any]]]:
        question = str(case.input_payload.get("question", ""))
        if not question:
            raise ValidationError(
                "evaluation_question_required", "A routing case requires a question"
            )
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
        if not isinstance(data_understanding, dict):
            raise ValidationError(
                "evaluation_understanding_invalid",
                "Routing data_understanding must be an object",
            )
        actual = UnderstandingEngine().route(question, data_understanding)
        comparable = {key: actual.get(key) for key in case.expected}
        checks = {
            key: EvaluationEngine._matches_expected(actual.get(key), expected)
            for key, expected in case.expected.items()
        }
        scores = {f"{key}_accuracy": 1.0 if passed else 0.0 for key, passed in checks.items()}
        return checks, scores, comparable, []

    def _evaluate_sql(
        self,
        case: EvaluationCaseView,
    ) -> tuple[dict[str, bool], dict[str, float], dict[str, Any], list[dict[str, Any]]]:
        sql = str(case.input_payload.get("sql", ""))
        if not sql:
            raise ValidationError("evaluation_sql_required", "A SQL policy case requires SQL")
        actual: dict[str, Any] = {
            "sql_allowed": False,
            "error_code": None,
            "normalized_sql_sha256": None,
            "tables": [],
            "columns": [],
            "applied_limit": None,
        }
        try:
            result = self.sql.validate(
                sql,
                dialect=str(case.input_payload.get("dialect", "postgres")),
                allowed_tables=set(self._string_list(case.fixtures.get("allowed_tables", []))),
                allowed_columns=(
                    set(self._string_list(case.fixtures["allowed_columns"]))
                    if "allowed_columns" in case.fixtures
                    else None
                ),
            )
            actual.update(
                {
                    "sql_allowed": True,
                    "normalized_sql_sha256": canonical_sha256(result.normalized_sql),
                    "tables": list(result.tables),
                    "columns": list(result.columns),
                    "applied_limit": result.applied_limit,
                }
            )
        except ValidationError as exc:
            actual["error_code"] = exc.code
        checks = {
            key: self._matches_expected(actual.get(key), expected)
            for key, expected in case.expected.items()
        }
        scores = {f"{key}_accuracy": 1.0 if passed else 0.0 for key, passed in checks.items()}
        return checks, scores, actual, []

    async def _evaluate_run_output(
        self,
        session: AsyncSession,
        organization_id: UUID,
        case: EvaluationCase,
        *,
        agent_version_id: UUID,
        model_profile_id: UUID,
        run_bindings: dict[str, UUID],
    ) -> tuple[dict[str, bool], dict[str, float], dict[str, Any], list[dict[str, Any]]]:
        run_ref = case.input_payload.get("run_ref")
        if isinstance(run_ref, str):
            run_id = run_bindings.get(run_ref)
            if run_id is None:
                raise ValidationError(
                    "evaluation_run_binding_required",
                    "The evaluation request does not bind a required Golden Dataset run_ref",
                    run_ref=run_ref,
                )
        else:
            try:
                run_id = UUID(str(case.input_payload["run_id"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise ValidationError(
                    "evaluation_run_id_required",
                    "A Run output case requires a valid run_id or run_ref",
                ) from exc
        run = await session.scalar(
            select(Run).where(Run.id == run_id, Run.organization_id == organization_id)
        )
        if run is None:
            raise NotFoundError("Evaluation source run", run_id)
        if not is_terminal(run.status):
            raise ConflictError(
                "evaluation_run_not_terminal",
                "A Run output case requires a terminal source Run",
                status=run.status,
            )
        if run.agent_version_id != agent_version_id:
            raise ConflictError(
                "evaluation_agent_version_mismatch",
                "The source Run does not use the evaluation's pinned agent version",
            )
        if run.model_profile_id != model_profile_id:
            raise ConflictError(
                "evaluation_model_profile_mismatch",
                "The source Run does not use the evaluation's pinned model profile",
            )

        steps = list(
            await session.scalars(
                select(RunStep)
                .where(RunStep.organization_id == organization_id, RunStep.run_id == run.id)
                .order_by(RunStep.ordinal)
            )
        )
        evidence = list(
            await session.scalars(
                select(Evidence).where(
                    Evidence.organization_id == organization_id,
                    Evidence.run_id == run.id,
                )
            )
        )
        claims = list(
            await session.scalars(
                select(Claim)
                .where(Claim.organization_id == organization_id, Claim.run_id == run.id)
                .order_by(Claim.ordinal)
            )
        )
        artifacts = list(
            await session.scalars(
                select(Artifact).where(
                    Artifact.organization_id == organization_id,
                    Artifact.run_id == run.id,
                )
            )
        )
        claim_ids = [item.id for item in claims]
        links = (
            list(
                (
                    await session.execute(
                        select(ClaimEvidence.claim_id, ClaimEvidence.evidence_id).where(
                            ClaimEvidence.claim_id.in_(claim_ids)
                        )
                    )
                ).all()
            )
            if claim_ids
            else []
        )
        capabilities = [
            str(item.input_payload["capability"])
            for item in steps
            if isinstance(item.input_payload.get("capability"), str)
        ]
        sql_values = [
            str(item.inline_content["sql"])
            for item in artifacts
            if item.kind == ArtifactKind.SQL
            and isinstance(item.inline_content, dict)
            and isinstance(item.inline_content.get("sql"), str)
        ]
        answers = [
            str(item.inline_content["markdown"])
            for item in artifacts
            if item.kind == ArtifactKind.TEXT
            and isinstance(item.inline_content, dict)
            and isinstance(item.inline_content.get("markdown"), str)
        ]
        answer = "\n".join(answers)
        incident_fusion: dict[str, Any] = {}
        for artifact in artifacts:
            if artifact.kind != ArtifactKind.TEXT or not isinstance(artifact.inline_content, dict):
                continue
            candidate_fusion = artifact.inline_content.get("incident_fusion")
            if isinstance(candidate_fusion, dict):
                incident_fusion = candidate_fusion
                break
        top1 = incident_fusion.get("top1", {})
        top1_types = self._string_list(
            top1.get("evidence_types", []) if isinstance(top1, dict) else []
        )
        evidence_ids = {item.id for item in evidence}
        valid_links = [
            (claim_id, evidence_id)
            for claim_id, evidence_id in links
            if evidence_id in evidence_ids
        ]
        claim_evidence_types: dict[UUID, set[str]] = {}
        evidence_by_id = {item.id: item for item in evidence}
        for claim_id, evidence_id in valid_links:
            evidence_item = evidence_by_id.get(evidence_id)
            if evidence_item is not None:
                claim_evidence_types.setdefault(claim_id, set()).add(
                    evidence_item.evidence_type.value
                )
        cross_type_claims: int = sum(
            1 for types in claim_evidence_types.values() if len(types) >= 2
        )
        incident_candidate_count = int(incident_fusion.get("candidate_count", 0))
        linked_claim_ids = {claim_id for claim_id, _ in valid_links}
        citation_precision = len(valid_links) / len(links) if links else 0.0
        evidence_coverage = len(linked_claim_ids) / len(claims) if claims else 0.0
        verified_claims = sum(
            1 for item in claims if item.verification_status == VerificationStatus.VERIFIED
        )
        faithfulness = verified_claims / len(claims) if claims else 0.0
        evidence_types = sorted({item.evidence_type.value for item in evidence})
        evidence_sources = sorted({item.source for item in evidence})
        actual = {
            "run_status": run.status.value,
            "route": run.plan.get("route"),
            "intent": run.intent,
            "capabilities": capabilities,
            "sql": sql_values,
            "evidence_types": evidence_types,
            "evidence_sources": evidence_sources,
            "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
            "evidence_coverage": round(evidence_coverage, 4),
            "citation_precision": round(citation_precision, 4),
            "answer_faithfulness": round(faithfulness, 4),
            "incident_candidate_count": incident_candidate_count,
            "incident_top1_evidence_types": top1_types,
            "cross_type_claims": cross_type_claims,
        }
        checks: dict[str, bool] = {}
        scores: dict[str, float] = {
            "evidence_coverage": round(evidence_coverage, 4),
            "citation_precision": round(citation_precision, 4),
            "answer_faithfulness": round(faithfulness, 4),
        }
        for key, expected in case.expected.items():
            if key == "intent":
                value = run.intent if isinstance(expected, dict) else run.intent.get("intent")
                checks[key] = self._matches_expected(value, expected)
            elif key in {"capability", "capabilities"}:
                required = self._string_list(expected)
                checks[key] = set(required).issubset(capabilities)
            elif key == "sql":
                required = self._string_list(expected)
                normalized_actual = {self._normalize_sql(item) for item in sql_values}
                checks[key] = all(
                    self._normalize_sql(item) in normalized_actual for item in required
                )
            elif key in {"evidence", "evidence_types"}:
                required = self._string_list(expected)
                checks[key] = set(required).issubset(evidence_types)
            elif key == "evidence_sources":
                required = self._string_list(expected)
                checks[key] = set(required).issubset(evidence_sources)
            elif key == "answer_contains":
                required = self._string_list(expected)
                matched = sum(1 for term in required if term.casefold() in answer.casefold())
                checks[key] = bool(required) and matched == len(required)
                scores["answer_accuracy"] = round(matched / len(required), 4) if required else 0.0
            elif key == "answer_sha256":
                checks[key] = actual["answer_sha256"] == expected
            elif key == "incident_top1_evidence_types":
                required = self._string_list(expected)
                checks[key] = bool(top1_types) and set(required).issubset(set(top1_types))
            elif key in {"minimum_incident_candidates", "minimum_cross_type_claims"}:
                metric: int = (
                    incident_candidate_count
                    if key == "minimum_incident_candidates"
                    else cross_type_claims
                )
                if not isinstance(expected, int) or isinstance(expected, bool):
                    checks[key] = False
                else:
                    checks[key] = metric >= expected
                    scores[key.removeprefix("minimum_")] = float(metric)
            elif key.startswith("minimum_"):
                score_name = key.removeprefix("minimum_")
                actual_score = actual.get(score_name, 0)
                if not isinstance(actual_score, int | float) or not isinstance(
                    expected, int | float
                ):
                    raise ValidationError(
                        "evaluation_score_expectation_invalid",
                        "Minimum score expectations must be numeric",
                    )
                checks[key] = float(actual_score) >= float(expected)
            else:
                checks[key] = self._matches_expected(actual.get(key), expected)
            if key not in {"answer_contains"} and not key.startswith("minimum_"):
                scores[f"{key}_accuracy"] = 1.0 if checks[key] else 0.0

        observed = {
            "run_id": str(run.id),
            "run_status": actual["run_status"],
            "route": actual["route"],
            "capabilities": capabilities,
            "sql_sha256": [hashlib.sha256(item.encode()).hexdigest() for item in sql_values],
            "evidence_types": evidence_types,
            "evidence_count": len(evidence),
            "claim_count": len(claims),
            "answer_sha256": actual["answer_sha256"],
            "answer_length": len(answer),
            "evidence_coverage": actual["evidence_coverage"],
            "citation_precision": actual["citation_precision"],
            "answer_faithfulness": actual["answer_faithfulness"],
            "incident_candidate_count": actual["incident_candidate_count"],
            "incident_top1_evidence_types": actual["incident_top1_evidence_types"],
            "cross_type_claims": actual["cross_type_claims"],
        }
        evidence_refs = [
            {
                "evidence_id": str(item.id),
                "type": item.evidence_type.value,
                "content_fingerprint": item.content_fingerprint,
            }
            for item in evidence
        ]
        return checks, scores, observed, evidence_refs

    @staticmethod
    def _matches_expected(actual: Any, expected: Any) -> bool:
        if isinstance(expected, dict):
            return isinstance(actual, dict) and all(
                key in actual and EvaluationEngine._matches_expected(actual[key], value)
                for key, value in expected.items()
            )
        if isinstance(expected, list):
            return isinstance(actual, (list, tuple, set)) and set(map(str, expected)).issubset(
                set(map(str, actual))
            )
        return bool(actual == expected)

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if isinstance(value, tuple):
            return [str(item) for item in value]
        if value is None:
            return []
        return [str(value)]

    @staticmethod
    def _normalize_sql(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip().rstrip(";").casefold()
