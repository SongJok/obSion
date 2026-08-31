from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.schemas import (
    CreateEvaluationCaseRequest,
    CreateEvaluationDatasetRequest,
    EvalAgentPinView,
    EvalCatalogView,
    EvalCompareRequest,
    EvalCompareView,
    EvalProfilePinView,
    EvaluationCaseResultView,
    EvaluationCaseView,
    EvaluationDatasetView,
    EvaluationRunView,
    StartEvaluationRunRequest,
)
from obsion.application.eval import EvalExperienceService
from obsion.config import Settings
from obsion.security.auth import get_app_settings, get_principal, get_session
from obsion.security.identity import Principal

router = APIRouter(prefix="/eval", tags=["eval"])


def get_eval_service(
    settings: Settings = Depends(get_app_settings),
) -> EvalExperienceService:
    return EvalExperienceService(settings)


@router.get("/catalog", response_model=EvalCatalogView)
async def list_eval_catalog(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: EvalExperienceService = Depends(get_eval_service),
) -> EvalCatalogView:
    payload = await service.catalog(session, principal)
    return EvalCatalogView(
        datasets=[EvaluationDatasetView.model_validate(item) for item in payload["datasets"]],
        runs=[EvaluationRunView.model_validate(item) for item in payload["runs"]],
        agents=[EvalAgentPinView.model_validate(item) for item in payload["agents"]],
        prompts=[EvalAgentPinView.model_validate(item) for item in payload["prompts"]],
        model_profiles=[
            EvalProfilePinView.model_validate(item) for item in payload["model_profiles"]
        ],
    )


@router.post(
    "/datasets",
    response_model=EvaluationDatasetView,
    status_code=status.HTTP_201_CREATED,
)
async def create_eval_dataset(
    request: CreateEvaluationDatasetRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: EvalExperienceService = Depends(get_eval_service),
) -> EvaluationDatasetView:
    async with session.begin():
        dataset = await service.create_dataset(session, principal, request)
    return EvaluationDatasetView.model_validate(dataset)


@router.get("/datasets/{dataset_id}/cases", response_model=list[EvaluationCaseView])
async def list_eval_cases(
    dataset_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: EvalExperienceService = Depends(get_eval_service),
) -> list[EvaluationCaseView]:
    cases = await service.list_cases(session, principal, dataset_id)
    return [EvaluationCaseView.model_validate(item) for item in cases]


@router.post(
    "/datasets/{dataset_id}/cases",
    response_model=EvaluationCaseView,
    status_code=status.HTTP_201_CREATED,
)
async def add_eval_case(
    dataset_id: UUID,
    request: CreateEvaluationCaseRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: EvalExperienceService = Depends(get_eval_service),
) -> EvaluationCaseView:
    async with session.begin():
        case = await service.add_case(session, principal, dataset_id, request)
    return EvaluationCaseView.model_validate(case)


@router.post(
    "/datasets/{dataset_id}/runs",
    response_model=EvaluationRunView,
    status_code=status.HTTP_201_CREATED,
)
async def start_eval_run(
    dataset_id: UUID,
    request: StartEvaluationRunRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: EvalExperienceService = Depends(get_eval_service),
) -> EvaluationRunView:
    async with session.begin():
        evaluation = await service.start_run(session, principal, dataset_id, request)
    return EvaluationRunView.model_validate(evaluation)


@router.get("/runs", response_model=list[EvaluationRunView])
async def list_eval_runs(
    dataset_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: EvalExperienceService = Depends(get_eval_service),
) -> list[EvaluationRunView]:
    runs = await service.list_runs(session, principal, dataset_id)
    return [EvaluationRunView.model_validate(item) for item in runs]


@router.get("/runs/{run_id}", response_model=EvaluationRunView)
async def get_eval_run(
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: EvalExperienceService = Depends(get_eval_service),
) -> EvaluationRunView:
    evaluation = await service.get_run(session, principal, run_id)
    return EvaluationRunView.model_validate(evaluation)


@router.get("/runs/{run_id}/results", response_model=list[EvaluationCaseResultView])
async def list_eval_results(
    run_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: EvalExperienceService = Depends(get_eval_service),
) -> list[EvaluationCaseResultView]:
    results = await service.list_results(session, principal, run_id)
    return [EvaluationCaseResultView.model_validate(item) for item in results]


@router.post("/compare", response_model=EvalCompareView)
async def compare_eval_runs(
    request: EvalCompareRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: EvalExperienceService = Depends(get_eval_service),
) -> EvalCompareView:
    payload = await service.compare(
        session, principal, request.baseline_run_id, request.candidate_run_id
    )
    return EvalCompareView(
        baseline=EvaluationRunView.model_validate(payload["baseline"]),
        candidate=EvaluationRunView.model_validate(payload["candidate"]),
        gate_passed=payload["gate_passed"],
        metrics=payload["metrics"],
        agent_changed=payload["agent_changed"],
        prompt_changed=payload["prompt_changed"],
    )
