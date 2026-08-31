import json
from contextlib import suppress
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import ArtifactView
from obsion.artifacts.service import ArtifactService
from obsion.common.errors import ObsionError, ValidationError
from obsion.config import Settings
from obsion.domain.enums import ArtifactKind, Classification
from obsion.security.auth import get_app_settings, get_principal, get_session
from obsion.security.identity import Principal

router = APIRouter(tags=["artifacts"])


def get_artifact_service(
    request: Request,
    settings: Settings = Depends(get_app_settings),
) -> ArtifactService:
    return ArtifactService(
        request.app.state.object_store,
        max_upload_bytes=settings.artifact_max_upload_bytes,
    )


@router.post(
    "/workspaces/{workspace_id}/artifacts",
    response_model=ArtifactView,
    status_code=status.HTTP_201_CREATED,
)
async def upload_artifact(
    workspace_id: UUID,
    file: Annotated[UploadFile, File()],
    title: Annotated[str, Form(min_length=1, max_length=300)],
    kind: Annotated[ArtifactKind, Form()] = ArtifactKind.FILE,
    classification: Annotated[Classification, Form()] = Classification.INTERNAL,
    run_id: Annotated[UUID | None, Form()] = None,
    lineage: Annotated[str, Form(max_length=20_000)] = "{}",
    path: Annotated[str | None, Form(max_length=512)] = None,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ArtifactService = Depends(get_artifact_service),
    settings: Settings = Depends(get_app_settings),
) -> ArtifactView:
    try:
        parsed_lineage: Any = json.loads(lineage)
    except json.JSONDecodeError as exc:
        raise ValidationError("artifact_lineage_invalid", "Artifact lineage must be JSON") from exc
    if not isinstance(parsed_lineage, dict):
        raise ValidationError("artifact_lineage_invalid", "Artifact lineage must be an object")
    content = await file.read(settings.artifact_max_upload_bytes + 1)
    artifact = None
    try:
        async with session.begin():
            artifact = await service.create_file(
                session,
                principal,
                workspace_id=workspace_id,
                run_id=run_id,
                kind=kind,
                title=title,
                media_type=file.content_type or "application/octet-stream",
                content=content,
                classification=classification,
                lineage={**parsed_lineage, "filename": file.filename or title},
                path=path,
            )
    except Exception:
        if artifact is not None and artifact.storage_key is not None:
            with suppress(ObsionError):
                await service.store.delete(artifact.storage_key)
        raise
    assert artifact is not None
    return ArtifactView.model_validate(artifact)


@router.get("/workspaces/{workspace_id}/artifacts", response_model=list[ArtifactView])
async def list_workspace_artifacts(
    workspace_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ArtifactService = Depends(get_artifact_service),
) -> list[ArtifactView]:
    artifacts = await service.list_workspace(session, principal, workspace_id)
    return [ArtifactView.model_validate(item) for item in artifacts]


@router.get("/workspaces/{workspace_id}/files", response_model=list[ArtifactView])
async def list_workspace_files(
    workspace_id: UUID,
    include_superseded: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ArtifactService = Depends(get_artifact_service),
) -> list[ArtifactView]:
    artifacts = await service.list_files(
        session,
        principal,
        workspace_id,
        include_superseded=include_superseded,
    )
    return [ArtifactView.model_validate(item) for item in artifacts]


@router.get("/workspaces/{workspace_id}/reports", response_model=list[ArtifactView])
async def list_workspace_reports(
    workspace_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ArtifactService = Depends(get_artifact_service),
) -> list[ArtifactView]:
    artifacts = await service.list_reports(session, principal, workspace_id)
    return [ArtifactView.model_validate(item) for item in artifacts]


@router.get("/workspaces/{workspace_id}/dashboards", response_model=list[ArtifactView])
async def list_workspace_dashboards(
    workspace_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ArtifactService = Depends(get_artifact_service),
) -> list[ArtifactView]:
    artifacts = await service.list_dashboards(session, principal, workspace_id)
    return [ArtifactView.model_validate(item) for item in artifacts]


@router.get("/workspaces/{workspace_id}/sql", response_model=list[ArtifactView])
async def list_workspace_sql(
    workspace_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ArtifactService = Depends(get_artifact_service),
) -> list[ArtifactView]:
    artifacts = await service.list_sql(session, principal, workspace_id)
    return [ArtifactView.model_validate(item) for item in artifacts]


@router.get("/artifacts/{artifact_id}/content")
async def download_artifact(
    artifact_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: ArtifactService = Depends(get_artifact_service),
) -> Response:
    artifact, stored = await service.content(session, principal, artifact_id)
    return Response(
        content=stored.data,
        media_type=artifact.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="artifact-{artifact.id}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "ETag": f'"{artifact.checksum_sha256}"',
        },
    )
