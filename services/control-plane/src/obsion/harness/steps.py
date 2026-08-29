from collections.abc import Sequence
from dataclasses import dataclass

from obsion.db.models import RunStep
from obsion.domain.enums import StepKind, StepStatus

ACTIVE_STEP_STATUSES = frozenset(
    {StepStatus.PENDING, StepStatus.RUNNING, StepStatus.WAITING_APPROVAL}
)
BLOCKING_STEP_STATUSES = frozenset({StepStatus.FAILED, StepStatus.SKIPPED, StepStatus.CANCELLED})


@dataclass(frozen=True, slots=True)
class StepExecutionWave:
    ready: tuple[RunStep, ...]
    blocked: tuple[RunStep, ...]
    deadlocked: bool


class StepExecutor:
    """Deterministic DAG scheduler for Harness RunSteps."""

    def __init__(self, executable_kinds: frozenset[StepKind] | None = None) -> None:
        self.executable_kinds = executable_kinds or frozenset({StepKind.CAPABILITY})

    def next_wave(self, steps: Sequence[RunStep]) -> StepExecutionWave:
        status_by_ordinal = {step.ordinal: step.status for step in steps}
        active = tuple(step for step in steps if step.status in ACTIVE_STEP_STATUSES)
        blocked = tuple(
            step
            for step in active
            if any(
                status_by_ordinal.get(ordinal) in BLOCKING_STEP_STATUSES
                for ordinal in step.depends_on
            )
        )
        blocked_ordinals = {step.ordinal for step in blocked}
        ready = tuple(
            step
            for step in active
            if step.ordinal not in blocked_ordinals
            and step.kind in self.executable_kinds
            and all(
                status_by_ordinal.get(ordinal) == StepStatus.COMPLETED
                for ordinal in step.depends_on
            )
        )
        waiting_executable = tuple(
            step
            for step in active
            if step.ordinal not in blocked_ordinals and step.kind in self.executable_kinds
        )
        return StepExecutionWave(
            ready=ready,
            blocked=blocked,
            deadlocked=not ready and not blocked and bool(waiting_executable),
        )
