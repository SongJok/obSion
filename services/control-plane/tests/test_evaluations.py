from obsion.application.evaluations import EvaluationService
from obsion.common.ids import new_id
from obsion.common.time import utc_now
from obsion.db.models import EvaluationCaseResult
from obsion.domain.enums import EvaluationResultStatus, EvaluationTarget


def _result(external_id: str, status: EvaluationResultStatus) -> EvaluationCaseResult:
    return EvaluationCaseResult(
        organization_id=new_id(),
        evaluation_run_id=new_id(),
        evaluation_case_id=new_id(),
        ordinal=1,
        external_id=external_id,
        case_version=1,
        evaluator=EvaluationTarget.RUN_OUTPUT,
        status=status,
        case_snapshot_sha256="a" * 64,
        checks={"route": status == EvaluationResultStatus.PASSED},
        scores={"answer_accuracy": 1.0 if status == EvaluationResultStatus.PASSED else 0.0},
        observed={},
        evidence_refs=[],
        duration_ms=1,
        created_at=utc_now(),
    )


def test_regression_gate_detects_case_regression_and_score_drop() -> None:
    previous = [_result("knowledge-001", EvaluationResultStatus.PASSED)]
    current = [_result("knowledge-001", EvaluationResultStatus.FAILED)]

    metrics, passed = EvaluationService._aggregate(
        current,
        previous,
        gate_configuration={
            "minimum_pass_rate": 0.0,
            "maximum_regression_rate": 0.0,
            "score_thresholds": {"answer_accuracy": 0.8},
        },
    )

    assert passed is False
    assert metrics["baseline"]["regressions"] == ["knowledge-001@1"]
    assert metrics["baseline"]["regression_rate"] == 1.0
    assert metrics["gate"]["reasons"] == [
        "maximum_regression_rate",
        "score:answer_accuracy",
    ]
