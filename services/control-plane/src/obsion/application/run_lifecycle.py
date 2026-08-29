from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.db.models import RunStep
from obsion.domain.enums import StepStatus

ACTIVE_STEP_STATUSES = frozenset(
    {StepStatus.PENDING, StepStatus.RUNNING, StepStatus.WAITING_APPROVAL}
)


async def cancel_active_run_steps(
    session: AsyncSession,
    organization_id: UUID,
    run_id: UUID,
    *,
    completed_at: datetime,
) -> int:
    """Move every not-yet-terminal Step to CANCELLED under row locks.

    The caller must serialize on the parent Run row before invoking this helper. That
    lock order is shared by the API and Harness cancellation paths, preventing a later
    scheduler wave from starting after cancellation commits.
    """

    steps = list(
        await session.scalars(
            select(RunStep)
            .where(
                RunStep.organization_id == organization_id,
                RunStep.run_id == run_id,
                RunStep.status.in_(ACTIVE_STEP_STATUSES),
            )
            .order_by(RunStep.ordinal)
            .with_for_update()
        )
    )
    for step in steps:
        step.status = StepStatus.CANCELLED
        step.completed_at = completed_at
    return len(steps)
