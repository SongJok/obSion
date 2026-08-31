from uuid import uuid4

from obsion.db.models import RunStep
from obsion.domain.enums import StepKind, StepStatus
from obsion.harness.steps import StepExecutor


def _step(
    ordinal: int,
    kind: StepKind,
    status: StepStatus,
    *,
    depends_on: list[int] | None = None,
) -> RunStep:
    return RunStep(
        organization_id=uuid4(),
        run_id=uuid4(),
        ordinal=ordinal,
        name=f"step-{ordinal}",
        kind=kind,
        status=status,
        depends_on=depends_on or [],
        input_payload={},
    )


def test_step_executor_returns_ready_capability_wave() -> None:
    steps = [
        _step(1, StepKind.OBSERVE, StepStatus.COMPLETED),
        _step(2, StepKind.UNDERSTAND, StepStatus.COMPLETED, depends_on=[1]),
        _step(3, StepKind.PLAN, StepStatus.COMPLETED, depends_on=[2]),
        _step(4, StepKind.CAPABILITY, StepStatus.PENDING, depends_on=[3]),
        _step(5, StepKind.VERIFY, StepStatus.PENDING, depends_on=[4]),
    ]

    wave = StepExecutor().next_wave(steps)

    assert [step.ordinal for step in wave.ready] == [4]
    assert wave.blocked == ()
    assert not wave.deadlocked


def test_step_executor_marks_failed_dependencies_before_response_steps() -> None:
    steps = [
        _step(3, StepKind.PLAN, StepStatus.COMPLETED, depends_on=[2]),
        _step(4, StepKind.CAPABILITY, StepStatus.FAILED, depends_on=[3]),
        _step(5, StepKind.VERIFY, StepStatus.PENDING, depends_on=[4]),
        _step(6, StepKind.REFLECT, StepStatus.PENDING, depends_on=[5]),
        _step(7, StepKind.RESPOND, StepStatus.PENDING, depends_on=[6]),
    ]

    wave = StepExecutor().next_wave(steps)

    assert wave.ready == ()
    assert [step.ordinal for step in wave.blocked] == [5]
    assert not wave.deadlocked

    steps[2].status = StepStatus.SKIPPED
    wave = StepExecutor().next_wave(steps)
    assert [step.ordinal for step in wave.blocked] == [6]


def test_step_executor_detects_unresolved_capability_dependencies() -> None:
    wave = StepExecutor().next_wave(
        [_step(4, StepKind.CAPABILITY, StepStatus.PENDING, depends_on=[99])]
    )

    assert wave.ready == ()
    assert wave.blocked == ()
    assert wave.deadlocked
