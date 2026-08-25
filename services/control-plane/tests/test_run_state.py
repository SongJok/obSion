import pytest

from obsion.common.errors import ConflictError
from obsion.domain.enums import RunStatus
from obsion.domain.run_state import is_terminal, validate_run_transition


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (RunStatus.PENDING, RunStatus.RUNNING),
        (RunStatus.PENDING, RunStatus.CANCELLED),
        (RunStatus.RUNNING, RunStatus.WAITING_APPROVAL),
        (RunStatus.RUNNING, RunStatus.REPLANNING),
        (RunStatus.RUNNING, RunStatus.COMPLETED),
        (RunStatus.WAITING_APPROVAL, RunStatus.RUNNING),
        (RunStatus.WAITING_USER, RunStatus.FAILED),
    ],
)
def test_valid_run_transitions(current: RunStatus, target: RunStatus) -> None:
    validate_run_transition(current, target)


@pytest.mark.parametrize("terminal", [RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED])
def test_terminal_runs_cannot_transition(terminal: RunStatus) -> None:
    assert is_terminal(terminal)
    with pytest.raises(ConflictError, match="cannot transition"):
        validate_run_transition(terminal, RunStatus.RUNNING)


def test_waiting_approval_cannot_skip_to_completed() -> None:
    with pytest.raises(ConflictError) as caught:
        validate_run_transition(RunStatus.WAITING_APPROVAL, RunStatus.COMPLETED)
    assert caught.value.code == "invalid_run_transition"
