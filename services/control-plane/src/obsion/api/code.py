from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import (
    APIModel,
    CodeRepositoryIngestedView,
    CodeRepositoryView,
    CodeSnapshotView,
    CodeSymbolHitView,
    CodeSymbolQuery,
)
from obsion.code_intelligence.service import CodeIntelligenceService, SourceFileInput
from obsion.config import Settings
from obsion.domain.enums import Classification
from obsion.security.auth import get_app_settings, get_principal, get_session
from obsion.security.identity import Principal

router = APIRouter(tags=["code"])


def _service(settings: Settings = Depends(get_app_settings)) -> CodeIntelligenceService:
    return CodeIntelligenceService(settings)


class CreateCodeRepositoryRequest(APIModel):
    name: str = Field(min_length=1, max_length=240)
    default_branch: str = Field(default="main", min_length=1, max_length=200)
    classification: Classification = Classification.INTERNAL
    acl: dict[str, Any] = Field(default_factory=lambda: {"organization": True})


class CodeFileIngest(APIModel):
    path: str = Field(min_length=1, max_length=1024)
    content: str = Field(min_length=0, max_length=262_144)


class IndexCodeSnapshotRequest(APIModel):
    commit_id: str = Field(min_length=1, max_length=200)
    files: list[CodeFileIngest] = Field(min_length=1, max_length=500)


@router.post(
    "/code/repositories",
    response_model=CodeRepositoryView,
    status_code=status.HTTP_201_CREATED,
)
async def create_repository(
    request: CreateCodeRepositoryRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: CodeIntelligenceService = Depends(_service),
) -> CodeRepositoryView:
    async with session.begin():
        repository = await service.upsert_repository(
            session,
            principal,
            name=request.name,
            classification=request.classification,
            acl=request.acl,
            default_branch=request.default_branch,
        )
    return CodeRepositoryView.model_validate(repository)


@router.get("/code/repositories", response_model=list[CodeRepositoryView])
async def list_repositories(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: CodeIntelligenceService = Depends(_service),
) -> list[CodeRepositoryView]:
    repositories = await service.list_repositories(session, principal)
    return [CodeRepositoryView.model_validate(item) for item in repositories]


@router.post(
    "/code/repositories/{repository_id}/snapshots",
    response_model=CodeRepositoryIngestedView,
    status_code=status.HTTP_201_CREATED,
)
async def index_snapshot(
    repository_id: UUID,
    request: IndexCodeSnapshotRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: CodeIntelligenceService = Depends(_service),
) -> CodeRepositoryIngestedView:
    async with session.begin():
        repository, snapshot = await service.index_snapshot(
            session,
            principal,
            repository_id=repository_id,
            commit_id=request.commit_id,
            files=[
                SourceFileInput(path=item.path, content=item.content.encode("utf-8"))
                for item in request.files
            ],
        )
    return CodeRepositoryIngestedView(
        repository=CodeRepositoryView.model_validate(repository),
        snapshot=CodeSnapshotView.model_validate(snapshot),
    )


@router.post("/code/symbols/search", response_model=list[CodeSymbolHitView])
async def search_symbols(
    request: CodeSymbolQuery,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: CodeIntelligenceService = Depends(_service),
) -> list[CodeSymbolHitView]:
    hits = await service.search_symbols(
        session,
        principal,
        query=request.query,
        repository=request.repository,
        limit=request.limit,
    )
    return [CodeSymbolHitView.model_validate(hit.as_dict()) for hit in hits]
