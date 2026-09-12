"""Resolve durable platform-created dependencies without trusting caller labels."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.db.models import Artifact, Evidence, Run, RunConversationSnapshot

MAX_LINEAGE_RUNS = 1000
MAX_LINEAGE_EVIDENCE = 10000


async def run_evidence_lineage(
    session: AsyncSession, organization_id: UUID, run_id: UUID
) -> list[Evidence] | None:
    """None means incomplete/invalid lineage, never an authorized empty graph.

    Replays retain their original Run, conversation snapshots retain source Runs,
    and platform attachment evidence retains artifact IDs. User-authored memory
    and newly uploaded independent files do not acquire invented provenance.
    """
    pending = {run_id}
    seen: set[UUID] = set()
    result: list[Evidence] = []
    while pending:
        current = pending - seen
        if not current:
            break
        if len(seen) + len(current) > MAX_LINEAGE_RUNS:
            return None
        rows = (
            await session.execute(
                select(Run.id, Run.replay_of_run_id, Run.plan).where(
                    Run.organization_id == organization_id, Run.id.in_(current)
                )
            )
        ).all()
        if {row.id for row in rows} != current:
            return None
        seen.update(current)
        pending = {row.replay_of_run_id for row in rows if row.replay_of_run_id}
        snapshot_rows = (
            await session.execute(
                select(RunConversationSnapshot.run_id, RunConversationSnapshot.source_run_id).where(
                    RunConversationSnapshot.organization_id == organization_id,
                    RunConversationSnapshot.run_id.in_(current),
                    RunConversationSnapshot.source_run_id.is_not(None),
                )
            )
        ).all()
        for row in rows:
            captured = {item.source_run_id for item in snapshot_rows if item.run_id == row.id}
            plan = row.plan if isinstance(row.plan, dict) else {}
            if "conversation_source_run_ids" in plan:
                recorded = plan["conversation_source_run_ids"]
                if not isinstance(recorded, list):
                    return None
                try:
                    used = {UUID(str(value)) for value in recorded}
                except ValueError:
                    return None
                if not used.issubset(captured):
                    return None
                pending.update(used)
            else:
                pending.update(captured)
        evidence = list(
            await session.scalars(
                select(Evidence)
                .where(Evidence.organization_id == organization_id, Evidence.run_id.in_(current))
                .limit(MAX_LINEAGE_EVIDENCE - len(result) + 1)
            )
        )
        result.extend(evidence)
        if len(result) > MAX_LINEAGE_EVIDENCE:
            return None
        artifacts: set[UUID] = set()
        for item in evidence:
            if item.source != "workspace-artifact":
                continue
            try:
                artifacts.add(UUID(str(item.lineage.get("artifact_id"))))
            except (AttributeError, ValueError):
                return None
        if artifacts:
            origins = (
                await session.execute(
                    select(Artifact.id, Artifact.run_id).where(
                        Artifact.organization_id == organization_id, Artifact.id.in_(artifacts)
                    )
                )
            ).all()
            if {row.id for row in origins} != artifacts:
                return None
            pending.update(row.run_id for row in origins if row.run_id)
    return result
