import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from obsion.common.errors import NotFoundError, ValidationError
from obsion.config import Settings
from obsion.data_intelligence.sql_policy import SqlPolicyValidator, SqlValidationResult
from obsion.db.models import (
    DataColumn,
    DataSource,
    DataTable,
    Dimension,
    Metric,
    SemanticSynonym,
)
from obsion.security.identity import Principal


@dataclass(frozen=True, slots=True)
class Understanding:
    domain: str
    intent: str
    metrics: list[dict[str, Any]]
    dimensions: list[dict[str, Any]]
    time_range: dict[str, str]
    comparison: str | None
    need_root_cause: bool
    risk: str


@dataclass(frozen=True, slots=True)
class CompiledQuery:
    sql: str
    parameters: list[Any]
    parameter_types: list[str]
    metric: dict[str, Any]
    dimensions: list[dict[str, Any]]
    lineage: dict[str, Any]
    validation: SqlValidationResult
    column_masks: dict[str, dict[str, Any]]


class DataIntelligenceService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.validator = SqlPolicyValidator(
            default_limit=settings.sql_default_limit,
            max_limit=settings.sql_max_limit,
        )

    async def understand(
        self, session: AsyncSession, principal: Principal, question: str
    ) -> Understanding:
        metrics = list(
            await session.scalars(
                select(Metric)
                .where(
                    Metric.organization_id == principal.organization_id,
                    Metric.validated.is_(True),
                )
                .order_by(Metric.name, Metric.version.desc(), Metric.id)
            )
        )
        dimensions = list(
            await session.scalars(
                select(Dimension)
                .where(Dimension.organization_id == principal.organization_id)
                .order_by(Dimension.name, Dimension.version.desc(), Dimension.id)
            )
        )
        semantic_synonyms = list(
            await session.scalars(
                select(SemanticSynonym)
                .where(
                    SemanticSynonym.organization_id == principal.organization_id,
                    SemanticSynonym.target_type.in_(["METRIC", "DIMENSION"]),
                )
                .order_by(
                    SemanticSynonym.term,
                    SemanticSynonym.target_type,
                    SemanticSynonym.target_id,
                )
            )
        )
        normalized = question.casefold()
        synonym_metric_ids = {
            item.target_id
            for item in semantic_synonyms
            if item.target_type == "METRIC" and item.term in normalized
        }
        synonym_dimension_ids = {
            item.target_id
            for item in semantic_synonyms
            if item.target_type == "DIMENSION" and item.term in normalized
        }
        matched_metrics = [
            metric
            for metric in metrics
            if metric.id in synonym_metric_ids
            or any(
                term.casefold() in normalized
                for term in [metric.name, metric.display_name, *metric.synonyms]
            )
        ]
        latest_metrics: dict[str, Metric] = {}
        for metric in matched_metrics:
            latest_metrics.setdefault(metric.name, metric)
        matched_metrics = [latest_metrics[name] for name in sorted(latest_metrics)]
        matched_dimensions = [
            dimension
            for dimension in dimensions
            if dimension.id in synonym_dimension_ids
            or any(
                term.casefold() in normalized
                for term in [dimension.name, dimension.display_name, *dimension.synonyms]
            )
        ]
        latest_dimensions: dict[str, Dimension] = {}
        for dimension in matched_dimensions:
            latest_dimensions.setdefault(dimension.name, dimension)
        matched_dimensions = [latest_dimensions[name] for name in sorted(latest_dimensions)]
        time_range = self._time_range(question)
        comparison = None
        if any(term in normalized for term in ["同比", "year over year", "yoy"]):
            comparison = "YEAR_OVER_YEAR"
        elif any(term in normalized for term in ["环比", "previous period", "period over period"]):
            comparison = "PREVIOUS_PERIOD"
        elif any(term in normalized for term in ["前一天", "previous day"]):
            comparison = "PREVIOUS_DAY"
        root_cause = any(
            term in normalized
            for term in ["为什么", "原因", "异常", "下降", "上升", "why", "anomaly", "root cause"]
        )
        intent = "ANOMALY_INVESTIGATION" if root_cause else "ANALYTICS_QUERY"
        return Understanding(
            domain="DATA",
            intent=intent,
            metrics=[
                {"id": str(metric.id), "name": metric.name, "display_name": metric.display_name}
                for metric in matched_metrics
            ],
            dimensions=[
                {
                    "id": str(dimension.id),
                    "name": dimension.name,
                    "display_name": dimension.display_name,
                }
                for dimension in matched_dimensions
            ],
            time_range=time_range,
            comparison=comparison,
            need_root_cause=root_cause,
            risk="L2" if root_cause else "L1",
        )

    def logical_plan(
        self,
        *,
        metric_id: UUID,
        dimension_ids: list[UUID],
        time_range: dict[str, str],
        filters: list[dict[str, Any]],
        comparison: str | None,
    ) -> dict[str, Any]:
        return {
            "version": 1,
            "operation": "AGGREGATE",
            "metric_id": str(metric_id),
            "dimension_ids": [str(value) for value in dimension_ids],
            "time_range": time_range,
            "filters": filters,
            "comparison": comparison,
            "result": {"format": "TABLE", "limit": self.settings.sql_default_limit},
        }

    async def compile(
        self,
        session: AsyncSession,
        principal: Principal,
        plan: dict[str, Any],
    ) -> CompiledQuery:
        try:
            metric_id = UUID(plan["metric_id"])
            dimension_ids = [UUID(value) for value in plan.get("dimension_ids", [])]
            start = datetime.fromisoformat(plan["time_range"]["start"])
            end = datetime.fromisoformat(plan["time_range"]["end"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValidationError("logical_plan_invalid", "Logical query plan is invalid") from exc
        if start >= end:
            raise ValidationError("time_range_invalid", "Query start must be before end")
        metric_row = (
            await session.execute(
                select(Metric, DataTable, DataSource)
                .join(DataTable, DataTable.id == Metric.source_table_id)
                .join(DataSource, DataSource.id == DataTable.data_source_id)
                .where(
                    Metric.id == metric_id,
                    Metric.organization_id == principal.organization_id,
                    Metric.validated.is_(True),
                    DataTable.organization_id == principal.organization_id,
                    DataSource.organization_id == principal.organization_id,
                    DataSource.read_only.is_(True),
                )
            )
        ).one_or_none()
        if metric_row is None:
            raise NotFoundError("Validated metric", metric_id)
        metric, table, data_source = metric_row._tuple()
        if len(dimension_ids) != len(set(dimension_ids)):
            raise ValidationError(
                "dimension_scope_invalid", "A logical plan cannot repeat a dimension"
            )
        dimensions = list(
            await session.scalars(
                select(Dimension).where(
                    Dimension.organization_id == principal.organization_id,
                    Dimension.id.in_(dimension_ids),
                    Dimension.source_table_id == table.id,
                )
            )
        )
        if len(dimensions) != len(set(dimension_ids)):
            raise ValidationError(
                "dimension_scope_invalid", "Every dimension must belong to the metric source table"
            )
        dimensions_by_id = {dimension.id: dimension for dimension in dimensions}
        dimensions = [dimensions_by_id[dimension_id] for dimension_id in dimension_ids]
        columns = list(
            await session.scalars(
                select(DataColumn).where(
                    DataColumn.organization_id == principal.organization_id,
                    DataColumn.table_id == table.id,
                )
            )
        )
        allowed_column_names = {column.name for column in columns}
        column_masks = {
            column.name: column.mask_policy
            for column in columns
            if isinstance(column.mask_policy, dict) and column.mask_policy
        }
        parameters: list[Any] = [start, end]
        parameter_types = ["datetime", "datetime"]
        select_parts = [f'{dimension.expression} AS "{dimension.name}"' for dimension in dimensions]
        select_parts.append(f'{metric.expression} AS "{metric.name}"')
        qualified_table = f'"{table.schema_name}"."{table.table_name}"'
        predicates = [f'"{metric.time_column}" >= $1', f'"{metric.time_column}" < $2']
        for key, value in sorted(metric.filters.items()):
            if key not in allowed_column_names:
                raise ValidationError(
                    "metric_filter_invalid", "Metric definition uses an unknown column"
                )
            parameters.append(value)
            parameter_types.append("scalar")
            predicates.append(f'"{key}" = ${len(parameters)}')
        for item in plan.get("filters", []):
            if not isinstance(item, dict):
                raise ValidationError("query_filter_invalid", "Query filter is not allowed")
            column = item.get("column")
            operator = str(item.get("operator", "=")).upper()
            if column not in allowed_column_names or operator not in {
                "=",
                "!=",
                ">",
                ">=",
                "<",
                "<=",
            }:
                raise ValidationError("query_filter_invalid", "Query filter is not allowed")
            parameters.append(item.get("value"))
            parameter_types.append("scalar")
            predicates.append(f'"{column}" {operator} ${len(parameters)}')
        self._append_row_policy(
            table.row_policy,
            allowed_column_names=allowed_column_names,
            predicates=predicates,
            parameters=parameters,
            parameter_types=parameter_types,
        )
        group_by = ", ".join(dimension.expression for dimension in dimensions)
        sql = (  # noqa: S608 -- governed identifiers are parsed and allowlisted below
            f"SELECT {', '.join(select_parts)} FROM {qualified_table} "  # noqa: S608
            f"WHERE {' AND '.join(predicates)}"
        )
        if group_by:
            sql = f"{sql} GROUP BY {group_by}"
        allowed_table = f"{table.schema_name}.{table.table_name}".lower()
        query_policy = (
            data_source.query_policy if isinstance(data_source.query_policy, dict) else {}
        )
        configured_max_rows = query_policy.get("max_rows")
        max_limit = (
            min(self.settings.sql_max_limit, configured_max_rows)
            if isinstance(configured_max_rows, int)
            and not isinstance(configured_max_rows, bool)
            and configured_max_rows > 0
            else self.settings.sql_max_limit
        )
        default_limit = min(self.settings.sql_default_limit, max_limit)
        configured_budget = query_policy.get("scan_budget", self.settings.sql_scan_budget)
        scan_budget = (
            configured_budget
            if isinstance(configured_budget, int) and not isinstance(configured_budget, bool)
            else self.settings.sql_scan_budget
        )
        validation = SqlPolicyValidator(default_limit=default_limit, max_limit=max_limit).validate(
            sql,
            dialect=data_source.dialect,
            allowed_tables={allowed_table},
            allowed_columns=allowed_column_names,
            scan_budget=scan_budget,
        )
        return CompiledQuery(
            sql=validation.normalized_sql,
            parameters=parameters,
            parameter_types=parameter_types,
            metric={
                "id": str(metric.id),
                "name": metric.name,
                "display_name": metric.display_name,
                "version": metric.version,
                "owner": metric.owner,
            },
            dimensions=[
                {"id": str(item.id), "name": item.name, "version": item.version}
                for item in dimensions
            ],
            lineage={
                "data_source_id": str(data_source.id),
                "table_id": str(table.id),
                "table": allowed_table,
                "connector_id": str(data_source.connector_id),
            },
            validation=validation,
            column_masks=column_masks,
        )

    @staticmethod
    def _append_row_policy(
        row_policy: Any,
        *,
        allowed_column_names: set[str],
        predicates: list[str],
        parameters: list[Any],
        parameter_types: list[str],
    ) -> None:
        if not isinstance(row_policy, dict) or not row_policy:
            return
        if "column" in row_policy and "allowed_values" in row_policy:
            filters_value: Any = {row_policy.get("column"): row_policy.get("allowed_values")}
        else:
            filters_value = row_policy.get("filters", row_policy)
        if not isinstance(filters_value, dict):
            return
        filters: dict[Any, Any] = filters_value
        for column, raw_value in sorted(filters.items()):
            if column in {"column", "allowed_values", "filters", "description"}:
                continue
            if column not in allowed_column_names:
                raise ValidationError(
                    "sql_row_policy_invalid", "Row policy references an unknown column"
                )
            values = raw_value if isinstance(raw_value, list) else [raw_value]
            if not values:
                raise ValidationError("sql_row_policy_invalid", "Row policy values cannot be empty")
            placeholders: list[str] = []
            for value in values:
                parameters.append(value)
                parameter_types.append("scalar")
                placeholders.append(f"${len(parameters)}")
            if len(placeholders) == 1:
                predicates.append(f'"{column}" = {placeholders[0]}')
            else:
                predicates.append(f'"{column}" IN ({", ".join(placeholders)})')

    def _time_range(self, question: str) -> dict[str, str]:
        zone = ZoneInfo("Asia/Shanghai")
        now = datetime.now(zone)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        normalized = question.casefold()
        if "昨天" in normalized or "yesterday" in normalized:
            start, end = today - timedelta(days=1), today
        elif "今天" in normalized or "today" in normalized:
            start, end = today, now
        else:
            match = re.search(r"(?:最近|过去|last\s+)(\d+)\s*(?:天|days?)", normalized)
            days = min(int(match.group(1)), 366) if match else 30
            start, end = today - timedelta(days=days), now
        return {"start": start.isoformat(), "end": end.isoformat(), "timezone": str(zone)}
