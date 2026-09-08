"""
Obsion Data Intelligence - Semantic Layer Models

Phase 102: 数据智能增强
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class MetricDefinition(BaseModel):
    """指标定义 - 企业业务指标的语义描述"""

    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    name: str = Field(..., max_length=200, description="指标唯一标识，如 'paid_user_count'")
    display_name: str = Field(..., max_length=200, description="指标显示名称，如 '付费用户数'")
    description: str | None = Field(None, description="指标详细说明")
    expression: str = Field(..., description="SQL表达式，如 'COUNT(DISTINCT user_id)'")
    filters: dict[str, Any] | None = Field(None, description="预定义过滤条件")
    time_column: str | None = Field(None, max_length=100, description="时间列名")
    aggregation: str | None = Field(
        None, max_length=20, description="聚合类型: SUM/COUNT/AVG/MAX/MIN"
    )
    data_source_id: UUID | None = Field(None, description="关联的数据源")
    owner_id: UUID | None = Field(None, description="指标负责人")
    synonyms: list[str] | None = Field(None, description="同义词列表，用于语义匹配")
    metadata: dict[str, Any] | None = Field(None, description="扩展元数据")
    active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(from_attributes=True)


class DimensionDefinition(BaseModel):
    """维度定义 - 数据分析的维度"""

    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    name: str = Field(..., max_length=200, description="维度标识，如 'channel'")
    display_name: str = Field(..., max_length=200, description="维度显示名称，如 '渠道'")
    description: str | None = Field(None, description="维度说明")
    column_name: str = Field(..., max_length=100, description="数据库列名")
    table_name: str = Field(..., max_length=100, description="数据库表名")
    value_type: str | None = Field(
        None, max_length=50, description="值类型: categorical/numeric/temporal"
    )
    hierarchies: list[str] | None = Field(None, description="层级关系，如 ['国家', '省份', '城市']")
    metadata: dict[str, Any] | None = Field(None)
    active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(from_attributes=True)


class EntityDefinition(BaseModel):
    """实体定义 - 业务实体的数据模型"""

    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    name: str = Field(..., max_length=200, description="实体标识，如 'user'")
    display_name: str = Field(..., max_length=200, description="实体显示名称，如 '用户'")
    description: str | None = Field(None, description="实体说明")
    primary_table: str = Field(..., max_length=100, description="主表名")
    primary_key: str = Field(..., max_length=100, description="主键列名")
    related_tables: dict[str, Any] | None = Field(None, description="关联表及关系")
    metadata: dict[str, Any] | None = Field(None)
    active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(from_attributes=True)


class DataSource(BaseModel):
    """数据源定义"""

    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    name: str = Field(..., max_length=200, description="数据源名称")
    type: str = Field(..., max_length=50, description="类型: postgresql/mysql/clickhouse")
    connector_id: UUID = Field(..., description="连接器引用；凭证只由 Capability Gateway 解析")
    read_only: bool = Field(default=True, description="是否只读")
    config: dict[str, Any] | None = Field(None, description="额外配置")
    active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(from_attributes=True)


class QueryHistory(BaseModel):
    """查询历史 - 用于学习和优化"""

    id: UUID = Field(default_factory=uuid4)
    organization_id: UUID
    user_id: UUID
    run_id: UUID | None = Field(None, description="关联的Run ID")
    question: str = Field(..., description="用户原始问题")
    understanding: dict[str, Any] | None = Field(None, description="理解结果")
    logical_plan: dict[str, Any] | None = Field(None, description="逻辑查询计划")
    compiled_sql: str | None = Field(None, description="编译后的SQL")
    execution_time_ms: int | None = Field(None, description="执行时间（毫秒）")
    rows_returned: int | None = Field(None, description="返回行数")
    success: bool = Field(..., description="是否成功")
    error_message: str | None = Field(None, description="错误信息")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    model_config = ConfigDict(from_attributes=True)


class UnderstandingResult(BaseModel):
    """查询理解结果"""

    domain: str = Field(..., description="领域: business/engineering/operations")
    intent: str = Field(..., description="意图: query/analysis/investigation")
    metrics: list[str] = Field(default_factory=list, description="涉及的指标")
    dimensions: list[str] = Field(default_factory=list, description="涉及的维度")
    entities: list[str] = Field(default_factory=list, description="涉及的实体")
    time_range: dict[str, Any] | None = Field(None, description="时间范围")
    filters: dict[str, Any] | None = Field(None, description="过滤条件")
    aggregations: list[str] = Field(default_factory=list, description="聚合类型")
    need_breakdown: bool = Field(default=False, description="是否需要维度拆解")
    need_comparison: bool = Field(default=False, description="是否需要对比分析")
    need_root_cause: bool = Field(default=False, description="是否需要根因分析")
    confidence: float = Field(..., ge=0.0, le=1.0, description="理解置信度")


class LogicalQueryPlan(BaseModel):
    """逻辑查询计划"""

    select_columns: list[str] = Field(..., description="选择的列")
    from_tables: list[str] = Field(..., description="涉及的表")
    joins: list[dict[str, Any]] = Field(default_factory=list, description="JOIN条件")
    where_conditions: list[dict[str, Any]] = Field(default_factory=list, description="WHERE条件")
    group_by: list[str] = Field(default_factory=list, description="分组列")
    having_conditions: list[dict[str, Any]] = Field(default_factory=list, description="HAVING条件")
    order_by: list[dict[str, Any]] = Field(default_factory=list, description="排序")
    limit: int = Field(default=500, ge=1, le=5000, description="限制行数")
    time_range_filter: dict[str, Any] | None = Field(None, description="时间范围过滤")


class CompiledSQL(BaseModel):
    """编译后的SQL"""

    sql: str = Field(..., description="SQL语句")
    parameters: dict[str, Any] = Field(default_factory=dict, description="参数")
    estimated_rows: int | None = Field(None, description="预估扫描行数")
    timeout_seconds: int = Field(default=30, description="超时时间")
    data_source_id: UUID = Field(..., description="目标数据源")
    read_only: bool = Field(default=True, description="是否只读")


class QueryResult(BaseModel):
    """查询结果"""

    columns: list[str] = Field(..., description="列名")
    rows: list[list[Any]] = Field(..., description="数据行")
    row_count: int = Field(..., description="行数")
    execution_time_ms: int = Field(..., description="执行时间")
    data_source: str = Field(..., description="数据源")
    sql: str = Field(..., description="执行的SQL")
    truncated: bool = Field(default=False, description="是否被截断")


class DataInsight(BaseModel):
    """数据洞察"""

    type: str = Field(..., description="洞察类型: trend/anomaly/comparison/correlation")
    title: str = Field(..., description="洞察标题")
    description: str = Field(..., description="洞察描述")
    confidence: float = Field(..., ge=0.0, le=1.0, description="置信度")
    evidence: list[str] = Field(..., description="支持证据")
    visualization_suggestion: dict[str, Any] | None = Field(None, description="可视化建议")
    metadata: dict[str, Any] | None = Field(None)
