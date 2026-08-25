from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import (
    CreateEvaluationCaseRequest,
    CreateEvaluationDatasetRequest,
    EvaluationCaseResultView,
    EvaluationCaseView,
    EvaluationDatasetView,
    EvaluationRunView,
    StartEvaluationRunRequest,
)
from obsion.application.evaluations import EvaluationService
from obsion.config import Settings
from obsion.security.auth import get_app_settings, get_principal, get_session
from obsion.security.identity import Principal

router = APIRouter(prefix="/admin/evaluations", tags=["administration", "evaluations"])


def get_evaluation_service(
    settings: Settings = Depends(get_app_settings),
) -> EvaluationService:
    return EvaluationService(settings)


@router.post("/datasets", response_model=EvaluationDatasetView, status_code=status.HTTP_201_CREATED)
async def create_dataset(
    request: CreateEvaluationDatasetRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: EvaluationService = Depends(get_evaluation_service),
) -> EvaluationDatasetView:
    async with session.begin():
        dataset = await service.create_dataset(session, principal, request)
    return EvaluationDatasetView.model_validate(dataset)


@router.get("/datasets", response_model=list[EvaluationDatasetView])
async def list_datasets(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: EvaluationService = Depends(get_evaluation_service),
) -> list[EvaluationDatasetView]:
    datasets = await service.list_datasets(session, principal)
    return [EvaluationDatasetView.model_validate(item) for item in datasets]


@router.post(
    "/datasets/{dataset_id}/cases",
    response_model=EvaluationCaseView,
    status_code=status.HTTP_201_CREATED,
)
async def add_case(
    dataset_id: UUID,
    request: CreateEvaluationCaseRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: EvaluationService = Depends(get_evaluation_service),
) -> EvaluationCaseView:
    async with session.begin():
        case = await service.add_case(session, principal, dataset_id, request)
    return EvaluationCaseView.model_validate(case)


@router.get("/datasets/{dataset_id}/cases", response_model=list[EvaluationCaseView])
async def list_cases(
    dataset_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: EvaluationService = Depends(get_evaluation_service),
) -> list[EvaluationCaseView]:
    cases = await service.list_cases(session, principal, dataset_id)
    return [EvaluationCaseView.model_validate(item) for item in cases]


@router.post(
    "/datasets/{dataset_id}/runs",
    response_model=EvaluationRunView,
    status_code=status.HTTP_201_CREATED,
)
async def run_evaluation(
    dataset_id: UUID,
    request: StartEvaluationRunRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: EvaluationService = Depends(get_evaluation_service),
) -> EvaluationRunView:
    async with session.begin():
        evaluation = await service.run(session, principal, dataset_id, request)
    return EvaluationRunView.model_validate(evaluation)


@router.get("/runs", response_model=list[EvaluationRunView])
async def list_runs(
    dataset_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: EvaluationService = Depends(get_evaluation_service),
) -> list[EvaluationRunView]:
    runs = await service.list_runs(session, principal, dataset_id)
    return [EvaluationRunView.model_validate(item) for item in runs]


@router.get("/runs/{run_id}", response_model=EvaluationRunView)
async def get_evaluation_run(
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: EvaluationService = Depends(get_evaluation_service),
) -> EvaluationRunView:
    evaluation = await service.get_run(session, principal, run_id)
    return EvaluationRunView.model_validate(evaluation)


@router.get("/runs/{run_id}/results", response_model=list[EvaluationCaseResultView])
async def list_evaluation_results(
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: EvaluationService = Depends(get_evaluation_service),
) -> list[EvaluationCaseResultView]:
    results = await service.list_results(session, principal, run_id)
    return [EvaluationCaseResultView.model_validate(item) for item in results]
