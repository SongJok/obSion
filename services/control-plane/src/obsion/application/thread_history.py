from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import ConflictError
from obsion.db.models import Thread, Turn
from obsion.security.identity import Principal
from obsion.security.workspace_access import require_thread_access


class ThreadHistoryResolver:
    """Resolve the immutable effective Turn prefix of a Thread fork lineage."""

    max_lineage_depth = 32

    async def effective_turns(
        self,
        session: AsyncSession,
        principal: Principal,
        thread: Thread,
        *,
        lineage: frozenset[UUID] = frozenset(),
    ) -> list[Turn]:
        if thread.id in lineage or len(lineage) >= self.max_lineage_depth:
            raise ConflictError(
                "thread_lineage_invalid",
                "Thread fork lineage contains a cycle or exceeds the supported depth",
            )
        lineage = lineage | {thread.id}
        inherited: list[Turn] = []
        if thread.parent_thread_id is not None:
            parent = await require_thread_access(
                session,
                principal,
                thread.parent_thread_id,
            )
            parent_turns = await self.effective_turns(
                session,
                principal,
                parent,
                lineage=lineage,
            )
            if thread.forked_from_turn_id is not None:
                fork_index = next(
                    (
                        index
                        for index, item in enumerate(parent_turns)
                        if item.id == thread.forked_from_turn_id
                    ),
                    None,
                )
                if fork_index is None:
                    raise ConflictError(
                        "thread_fork_point_missing",
                        "The persisted Thread fork point is no longer available",
                    )
                inherited = parent_turns[: fork_index + 1]
        local_turns = list(
            await session.scalars(
                select(Turn)
                .where(
                    Turn.organization_id == principal.organization_id,
                    Turn.thread_id == thread.id,
                )
                .order_by(Turn.ordinal)
            )
        )
        return [*inherited, *local_turns]
