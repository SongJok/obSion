from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import (
    ArtifactView,
    ClaimView,
    EvidenceView,
    RunConversationSnapshotView,
    RunMemorySnapshotView,
    RunSourcePinView,
    RunStepView,
)
from obsion.code_intelligence.service import repository_access_clause
from obsion.common.errors import NotFoundError
from obsion.db.models import (
    Artifact,
    Claim,
    ClaimEvidence,
    CodeRepository,
    Evidence,
    Run,
    RunConversationSnapshot,
    RunMemorySnapshot,
    RunStep,
    Thread,
    Turn,
)
from obsion.db.project_source_models import ConnectorConfigurationVersion, RunSourcePin
from obsion.sandbox.project import ProjectRejected, ProjectRevision
from obsion.sandbox.source_state import check_project_source_state
from obsion.security.auth import get_principal, get_session
from obsion.security.identity import Principal
from obsion.security.workspace_access import require_run_access, require_workspace_access

router = APIRouter(tags=["runs"])


async def _require_run(session: AsyncSession, principal: Principal, run_id: UUID) -> None:
    await require_run_access(session, principal, run_id)


@router.get("/runs/{run_id}/steps", response_model=list[RunStepView])
async def list_steps(
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[RunStepView]:
    await _require_run(session, principal, run_id)
    steps = await session.scalars(
        select(RunStep)
        .where(
            RunStep.organization_id == principal.organization_id,
            RunStep.run_id == run_id,
        )
        .order_by(RunStep.ordinal)
    )
    return [RunStepView.model_validate(step) for step in steps]


@router.get("/runs/{run_id}/evidence", response_model=list[EvidenceView])
async def list_evidence(
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[EvidenceView]:
    await _require_run(session, principal, run_id)
    evidence = await session.scalars(
        select(Evidence)
        .where(
            Evidence.organization_id == principal.organization_id,
            Evidence.run_id == run_id,
        )
        .order_by(Evidence.ingested_at)
    )
    return [EvidenceView.model_validate(item) for item in evidence]


@router.get("/workspaces/{workspace_id}/evidence", response_model=list[EvidenceView])
async def list_workspace_evidence(
    workspace_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[EvidenceView]:
    await require_workspace_access(session, principal, workspace_id)
    evidence = await session.scalars(
        select(Evidence)
        .join(Run, Run.id == Evidence.run_id)
        .join(Turn, Turn.id == Run.turn_id)
        .join(Thread, Thread.id == Turn.thread_id)
        .where(
            Evidence.organization_id == principal.organization_id,
            Run.organization_id == principal.organization_id,
            Thread.workspace_id == workspace_id,
        )
        .order_by(Evidence.ingested_at.desc())
        .limit(500)
    )
    return [EvidenceView.model_validate(item) for item in evidence]


@router.get("/runs/{run_id}/claims", response_model=list[ClaimView])
async def list_claims(
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[ClaimView]:
    await _require_run(session, principal, run_id)
    claims = list(
        await session.scalars(
            select(Claim)
            .where(
                Claim.organization_id == principal.organization_id,
                Claim.run_id == run_id,
            )
            .order_by(Claim.ordinal)
        )
    )
    links = (
        (
            await session.execute(
                select(ClaimEvidence.claim_id, ClaimEvidence.evidence_id).where(
                    ClaimEvidence.claim_id.in_([claim.id for claim in claims])
                )
            )
        ).all()
        if claims
        else []
    )
    evidence_by_claim: dict[UUID, list[UUID]] = {}
    for claim_id, evidence_id in links:
        evidence_by_claim.setdefault(claim_id, []).append(evidence_id)
    return [
        ClaimView(
            id=claim.id,
            run_id=claim.run_id,
            ordinal=claim.ordinal,
            statement=claim.statement,
            confidence=claim.confidence,
            verification_status=claim.verification_status,
            critic_notes=claim.critic_notes,
            evidence_ids=evidence_by_claim.get(claim.id, []),
        )
        for claim in claims
    ]


@router.get("/runs/{run_id}/artifacts", response_model=list[ArtifactView])
async def list_artifacts(
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[ArtifactView]:
    await _require_run(session, principal, run_id)
    artifacts = await session.scalars(
        select(Artifact)
        .where(
            Artifact.organization_id == principal.organization_id,
            Artifact.run_id == run_id,
        )
        .order_by(Artifact.created_at)
    )
    return [ArtifactView.model_validate(item) for item in artifacts]


@router.get("/runs/{run_id}/source-pins", response_model=list[RunSourcePinView])
async def list_source_pins(
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[RunSourcePinView]:
    """Inspect immutable source pins while rechecking current source usability."""

    await _require_run(session, principal, run_id)
    rows = (
        await session.execute(
            select(RunSourcePin, ConnectorConfigurationVersion.environment)
            .join(
                ConnectorConfigurationVersion,
                (ConnectorConfigurationVersion.id == RunSourcePin.connector_version_id)
                & (ConnectorConfigurationVersion.organization_id == RunSourcePin.organization_id),
            )
            .where(
                RunSourcePin.organization_id == principal.organization_id,
                RunSourcePin.run_id == run_id,
            )
            .order_by(RunSourcePin.created_at, RunSourcePin.id)
        )
    ).all()
    views: list[RunSourcePinView] = []
    for pin, environment in rows:
        available = bool(
            await session.scalar(
                select(CodeRepository.id).where(
                    CodeRepository.id == pin.repository_id,
                    CodeRepository.organization_id == principal.organization_id,
                    repository_access_clause(principal),
                )
            )
        )
        try:
            revision = ProjectRevision(
                pin.organization_id,
                pin.workspace_id,
                pin.repository_id,
                pin.connector_version_id,
                pin.commit_id,
                pin.tree_id,
            )
            await check_project_source_state(session, revision, environment=environment)
        except ProjectRejected:
            available = False
        views.append(
            RunSourcePinView(
                id=pin.id,
                run_id=pin.run_id,
                source_id=pin.source_id,
                connector_version_id=pin.connector_version_id,
                workspace_id=pin.workspace_id,
                repository_id=pin.repository_id,
                commit_id=pin.commit_id,
                tree_id=pin.tree_id,
                snapshot_fingerprint=pin.snapshot_fingerprint,
                file_count=pin.file_count,
                snapshot_bytes=pin.snapshot_bytes,
                pinned_by=pin.pinned_by,
                created_at=pin.created_at,
                source_available=available,
            )
        )
    return views


@router.get("/runs/{run_id}/memories", response_model=list[RunMemorySnapshotView])
async def list_run_memories(
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[RunMemorySnapshotView]:
    await _require_run(session, principal, run_id)
    snapshots = await session.scalars(
        select(RunMemorySnapshot)
        .where(
            RunMemorySnapshot.organization_id == principal.organization_id,
            RunMemorySnapshot.run_id == run_id,
        )
        .order_by(RunMemorySnapshot.ordinal)
    )
    return [RunMemorySnapshotView.model_validate(item) for item in snapshots]


@router.get(
    "/runs/{run_id}/conversation",
    response_model=list[RunConversationSnapshotView],
)
async def list_run_conversation(
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[RunConversationSnapshotView]:
    await _require_run(session, principal, run_id)
    snapshots = await session.scalars(
        select(RunConversationSnapshot)
        .where(
            RunConversationSnapshot.organization_id == principal.organization_id,
            RunConversationSnapshot.run_id == run_id,
        )
        .order_by(RunConversationSnapshot.ordinal)
    )
    return [RunConversationSnapshotView.model_validate(item) for item in snapshots]


@router.get("/artifacts/{artifact_id}", response_model=ArtifactView)
async def get_artifact(
    artifact_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> ArtifactView:
    artifact = await session.scalar(
        select(Artifact).where(
            Artifact.id == artifact_id,
            Artifact.organization_id == principal.organization_id,
        )
    )
    if artifact is None:
        raise NotFoundError("Artifact", artifact_id)
    await require_workspace_access(session, principal, artifact.workspace_id)
    return ArtifactView.model_validate(artifact)


@router.get("/evidence/{evidence_id}", response_model=EvidenceView)
async def get_evidence(
    evidence_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> EvidenceView:
    evidence = await session.scalar(
        select(Evidence).where(
            Evidence.id == evidence_id,
            Evidence.organization_id == principal.organization_id,
        )
    )
    if evidence is None:
        raise NotFoundError("Evidence", evidence_id)
    await require_run_access(session, principal, evidence.run_id)
    return EvidenceView.model_validate(evidence)
