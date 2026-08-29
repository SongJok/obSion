from obsion.common.errors import ConflictError
from obsion.domain.enums import WorkspaceDecisionStatus, WorkspaceTaskStatus

_TASK_TRANSITIONS: dict[WorkspaceTaskStatus, frozenset[WorkspaceTaskStatus]] = {
    WorkspaceTaskStatus.OPEN: frozenset(
        {
            WorkspaceTaskStatus.IN_PROGRESS,
            WorkspaceTaskStatus.BLOCKED,
            WorkspaceTaskStatus.COMPLETED,
            WorkspaceTaskStatus.CANCELLED,
        }
    ),
    WorkspaceTaskStatus.IN_PROGRESS: frozenset(
        {
            WorkspaceTaskStatus.OPEN,
            WorkspaceTaskStatus.BLOCKED,
            WorkspaceTaskStatus.COMPLETED,
            WorkspaceTaskStatus.CANCELLED,
        }
    ),
    WorkspaceTaskStatus.BLOCKED: frozenset(
        {
            WorkspaceTaskStatus.OPEN,
            WorkspaceTaskStatus.IN_PROGRESS,
            WorkspaceTaskStatus.COMPLETED,
            WorkspaceTaskStatus.CANCELLED,
        }
    ),
    WorkspaceTaskStatus.COMPLETED: frozenset({WorkspaceTaskStatus.OPEN}),
    WorkspaceTaskStatus.CANCELLED: frozenset({WorkspaceTaskStatus.OPEN}),
}


def validate_task_transition(current: WorkspaceTaskStatus, target: WorkspaceTaskStatus) -> None:
    if current == target:
        return
    if target not in _TASK_TRANSITIONS[current]:
        raise ConflictError(
            "workspace_task_transition_invalid",
            "The requested workspace task status transition is not permitted",
            current_status=current,
            requested_status=target,
        )


def validate_decision_transition(
    current: WorkspaceDecisionStatus, target: WorkspaceDecisionStatus
) -> None:
    allowed = {
        WorkspaceDecisionStatus.PROPOSED: {
            WorkspaceDecisionStatus.ACCEPTED,
            WorkspaceDecisionStatus.REJECTED,
        },
        WorkspaceDecisionStatus.ACCEPTED: {WorkspaceDecisionStatus.SUPERSEDED},
        WorkspaceDecisionStatus.REJECTED: set(),
        WorkspaceDecisionStatus.SUPERSEDED: set(),
    }
    if target not in allowed[current]:
        raise ConflictError(
            "workspace_decision_transition_invalid",
            "The requested workspace decision status transition is not permitted",
            current_status=current,
            requested_status=target,
        )
