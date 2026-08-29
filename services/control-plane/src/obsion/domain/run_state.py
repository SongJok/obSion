from obsion.common.errors import ConflictError
from obsion.domain.enums import RunStatus

_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.PENDING: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.WAITING_APPROVAL,
            RunStatus.WAITING_USER,
            RunStatus.REPLANNING,
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
    ),
    RunStatus.WAITING_APPROVAL: frozenset(
        {RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}
    ),
    RunStatus.WAITING_USER: frozenset({RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}),
    RunStatus.REPLANNING: frozenset({RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


def allowed_run_transitions(status: RunStatus) -> frozenset[RunStatus]:
    return _TRANSITIONS[status]


def validate_run_transition(current: RunStatus, target: RunStatus) -> None:
    if target not in allowed_run_transitions(current):
        raise ConflictError(
            "invalid_run_transition",
            f"Run cannot transition from {current} to {target}",
            current=current,
            target=target,
        )


def is_terminal(status: RunStatus) -> bool:
    return status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
