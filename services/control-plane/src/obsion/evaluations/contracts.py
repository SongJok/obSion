import re
from uuid import UUID

from obsion.api.schemas import CreateEvaluationCaseRequest
from obsion.common.errors import ValidationError
from obsion.domain.enums import EvaluationTarget

_SCORE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_RUN_REF = re.compile(r"^[a-z][a-z0-9_.-]{0,119}$")
_EXPECTED_KEYS: dict[EvaluationTarget, set[str]] = {
    EvaluationTarget.ROUTING: {
        "route",
        "domain",
        "intent",
        "need_data",
        "need_root_cause",
        "risk",
    },
    EvaluationTarget.SQL_POLICY: {
        "sql_allowed",
        "error_code",
        "normalized_sql_sha256",
        "tables",
        "columns",
        "applied_limit",
    },
    EvaluationTarget.RUN_OUTPUT: {
        "run_status",
        "route",
        "intent",
        "capability",
        "capabilities",
        "sql",
        "evidence",
        "evidence_types",
        "evidence_sources",
        "answer_contains",
        "answer_sha256",
        "minimum_evidence_coverage",
        "minimum_citation_precision",
        "minimum_answer_faithfulness",
    },
}


def infer_evaluator(request: CreateEvaluationCaseRequest) -> EvaluationTarget:
    if "run_id" in request.input_payload or "run_ref" in request.input_payload:
        return EvaluationTarget.RUN_OUTPUT
    if "sql_allowed" in request.expected:
        return EvaluationTarget.SQL_POLICY
    if request.expected.keys() & _EXPECTED_KEYS[EvaluationTarget.ROUTING]:
        return EvaluationTarget.ROUTING
    raise ValidationError(
        "evaluation_target_required",
        "An evaluation case must declare an evaluator target",
    )


def validate_case_request(
    evaluator: EvaluationTarget,
    request: CreateEvaluationCaseRequest,
) -> None:
    if not request.expected:
        raise ValidationError(
            "evaluation_expected_required", "An evaluation case requires expectations"
        )
    unsupported = sorted(set(request.expected) - _EXPECTED_KEYS[evaluator])
    if unsupported:
        raise ValidationError(
            "evaluation_expectation_unsupported",
            "The evaluator does not support one or more expectation fields",
            evaluator=evaluator,
            fields=unsupported,
        )
    if evaluator == EvaluationTarget.ROUTING and not request.input_payload.get("question"):
        raise ValidationError(
            "evaluation_question_required", "A routing case requires a question"
        )
    if evaluator == EvaluationTarget.SQL_POLICY and (
        not request.input_payload.get("sql") or "sql_allowed" not in request.expected
    ):
        raise ValidationError(
            "evaluation_sql_contract_required",
            "A SQL policy case requires sql and expected.sql_allowed",
        )
    if evaluator == EvaluationTarget.RUN_OUTPUT:
        run_ref = request.input_payload.get("run_ref")
        if isinstance(run_ref, str) and _RUN_REF.fullmatch(run_ref):
            return
        try:
            UUID(str(request.input_payload["run_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError(
                "evaluation_run_id_required",
                "A Run output case requires a valid run_id or run_ref",
            ) from exc


def validate_score_thresholds(values: dict[str, float]) -> dict[str, float]:
    normalized: dict[str, float] = {}
    for name, value in values.items():
        if _SCORE_NAME.fullmatch(name) is None or not 0 <= value <= 1:
            raise ValidationError(
                "evaluation_score_threshold_invalid",
                "Score thresholds require safe names and values between zero and one",
                score=name,
            )
        normalized[name] = float(value)
    return dict(sorted(normalized.items()))


def validate_run_bindings(values: dict[str, UUID]) -> dict[str, UUID]:
    for name in values:
        if _RUN_REF.fullmatch(name) is None:
            raise ValidationError(
                "evaluation_run_binding_invalid",
                "Run binding names must be stable lowercase identifiers",
                run_ref=name,
            )
    return dict(sorted(values.items()))
