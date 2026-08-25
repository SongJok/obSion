from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.api.dependencies import get_workspace_service
from obsion.api.schemas import (
    CompiledQueryView,
    CompileSqlRequest,
    CreateTurnRequest,
    DataQueryRequest,
    DataUnderstandingView,
    DataUnderstandRequest,
    LogicalPlanRequest,
    LogicalPlanView,
    MetricView,
    RunView,
    SqlValidationView,
    TurnCreatedView,
    TurnView,
    ValidateSqlRequest,
)
from obsion.application.workspaces import WorkspaceService
from obsion.common.errors import NotFoundError, ValidationError
from obsion.config import Settings
from obsion.data_intelligence.service import DataIntelligenceService
from obsion.data_intelligence.sql_policy import SqlPolicyValidator
from obsion.db.models import DataColumn, DataSource, DataTable, Metric
from obsion.security.auth import get_app_settings, get_principal, get_session
from obsion.security.identity import Principal

router = APIRouter(tags=["data"])


def _service(settings: Settings = Depends(get_app_settings)) -> DataIntelligenceService:
    return DataIntelligenceService(settings)


@router.post("/data/understand", response_model=DataUnderstandingView)
async def understand(
    request: DataUnderstandRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: DataIntelligenceService = Depends(_service),
) -> DataUnderstandingView:
    result = await service.understand(session, principal, request.question)
    return DataUnderstandingView.model_validate(result)


@router.post("/data/plan", response_model=LogicalPlanView)
async def plan(
    request: LogicalPlanRequest,
    service: DataIntelligenceService = Depends(_service),
) -> LogicalPlanView:
    result = service.logical_plan(
        metric_id=request.metric_id,
        dimension_ids=request.dimension_ids,
        time_range=request.time_range,
        filters=request.filters,
        comparison=request.comparison,
    )
    return LogicalPlanView(plan=result)


@router.post("/data/sql/compile", response_model=CompiledQueryView)
async def compile_sql(
    request: CompileSqlRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: DataIntelligenceService = Depends(_service),
) -> CompiledQueryView:
    compiled = await service.compile(session, principal, request.plan)
    return CompiledQueryView.model_validate(compiled)


@router.post("/data/sql/validate", response_model=SqlValidationView)
async def validate_sql(
    request: ValidateSqlRequest,
    settings: Settings = Depends(get_app_settings),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> SqlValidationView:
    source = await session.scalar(
        select(DataSource).where(
            DataSource.id == request.data_source_id,
            DataSource.organization_id == principal.organization_id,
            DataSource.read_only.is_(True),
        )
    )
    if source is None:
        raise NotFoundError("Read-only data source", request.data_source_id)
    tables = list(
        await session.scalars(
            select(DataTable).where(
                DataTable.organization_id == principal.organization_id,
                DataTable.data_source_id == source.id,
            )
        )
    )
    table_ids = [table.id for table in tables]
    columns = (
        list(
            await session.scalars(
                select(DataColumn).where(
                    DataColumn.organization_id == principal.organization_id,
                    DataColumn.table_id.in_(table_ids),
                )
            )
        )
        if table_ids
        else []
    )
    result = SqlPolicyValidator(
        default_limit=settings.sql_default_limit,
        max_limit=settings.sql_max_limit,
    ).validate(
        request.sql,
        dialect=source.dialect,
        allowed_tables={f"{table.schema_name}.{table.table_name}" for table in tables},
        allowed_columns={column.name for column in columns},
    )
    return SqlValidationView.model_validate(result)


@router.post(
    "/data/query",
    response_model=TurnCreatedView,
    status_code=202,
)
async def query_data(
    request: DataQueryRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
    service: DataIntelligenceService = Depends(_service),
    workspace_service: WorkspaceService = Depends(get_workspace_service),
) -> TurnCreatedView:
    async with session.begin():
        understanding = await service.understand(session, principal, request.question)
        if not understanding.metrics:
            raise ValidationError(
                "metric_not_resolved",
                "A governed data query must resolve at least one validated metric",
            )
        turn, run = await workspace_service.create_turn(
            session,
            principal,
            request.thread_id,
            CreateTurnRequest(
                input=request.question,
                context_refs=[{"type": "route_hint", "value": "DATA"}],
                model_profile=request.model_profile,
            ),
        )
    return TurnCreatedView(
        turn=TurnView.model_validate(turn),
        run=RunView.model_validate(run),
    )


@router.get("/data/metrics", response_model=list[MetricView])
async def list_metrics(
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> list[MetricView]:
    metrics = await session.scalars(
        select(Metric)
        .where(
            Metric.organization_id == principal.organization_id,
            Metric.validated.is_(True),
        )
        .order_by(Metric.display_name, Metric.version.desc())
    )
    return [MetricView.model_validate(metric) for metric in metrics]


@router.get("/data/lineage/{metric_id}")
async def metric_lineage(
    metric_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    row = (
        await session.execute(
            select(Metric, DataTable, DataSource)
            .join(DataTable, DataTable.id == Metric.source_table_id)
            .join(DataSource, DataSource.id == DataTable.data_source_id)
            .where(
                Metric.id == metric_id,
                Metric.organization_id == principal.organization_id,
            )
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError("Metric", metric_id)
    metric, table, source = row._tuple()
    return {
        "metric": {"id": str(metric.id), "name": metric.name, "version": metric.version},
        "table": {
            "id": str(table.id),
            "name": f"{table.schema_name}.{table.table_name}",
            "owner": table.owner,
        },
        "data_source": {
            "id": str(source.id),
            "name": source.name,
            "environment": source.environment,
            "read_only": source.read_only,
        },
    }


@router.get("/data/lineage")
async def metric_lineage_query(
    metric_id: UUID,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(get_principal),
) -> dict[str, Any]:
    return await metric_lineage(metric_id, session, principal)
