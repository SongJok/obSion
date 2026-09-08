# Phase 102: 数据智能增强历史提案 (Data Intelligence Enhancement)

> **SUPERSEDED（2026-09-06）：历史提案，不是实现或验收证据。** 以下 API、表结构、代码草图及完成勾选不代表当前契约。语义兼容层按 [ADR 0080](docs/adr/0080-single-semantic-runtime-compatibility.md) 复用唯一 DataIntelligenceService / Capability Gateway，不再按本文建立第二套执行器、Policy 或事实源。正式状态见 [project-status](docs/project-status.yaml)，后续开发见 [产品化计划](docs/product/productization-plan.md)。

**版本**: 0.102.0-dev  
**基于**: Phase 100 (Context-first clarification) + Alpha.1  
**目标**: 提升企业问数能力，完善NL2SQL和数据分析功能

---

## 🎯 目标

根据goal.txt的核心场景"企业问数"，Phase 102专注于：

1. **复杂SQL生成优化** - 提升多表关联、聚合查询准确率
2. **语义层完善** - 建立企业指标、维度、实体语义层
3. **数据洞察自动生成** - 从查询结果自动生成分析报告
4. **时序数据分析** - 支持趋势、同比环比分析
5. **Evidence增强** - 所有数据结论必须有SQL证据

---

## 📋 核心需求（严格按照rule.txt）

### 必须遵守的架构原则

- ✅ Agent永不直连生产数据库
- ✅ 所有SQL必须经过Parser → AST → Policy → Query Gateway
- ✅ 强制LIMIT、Timeout、Scan Budget
- ✅ 每个数据结论必须是Claim + Evidence + Confidence
- ✅ SQL执行结果转换为Evidence
- ✅ 默认只读，禁止INSERT/UPDATE/DELETE

### DataAgent核心流程

```
User Question
    ↓
Understanding Engine (语义理解)
    ↓
Semantic Resolver (指标/维度/实体解析)
    ↓
Query Planner (生成逻辑查询计划)
    ↓
SQL Compiler (编译为SQL AST)
    ↓
SQL Validator (语法和权限验证)
    ↓
Policy Engine (安全策略检查)
    ↓
Query Gateway (路由到Read Replica)
    ↓
Result → Evidence (结果转为证据)
    ↓
Insight Generator (生成洞察)
    ↓
Claim + Evidence + Confidence
```

---

## 🏗️ 技术架构

### 1. 语义层 (Semantic Layer)

#### 数据模型

```python
# services/control-plane/src/obsion/data/semantic/models.py


class MetricDefinition:
    """指标定义"""

    id: str
    name: str  # 中文名称：付费用户数
    expression: str  # COUNT(DISTINCT user_id)
    filters: dict  # {pay_status: 'SUCCESS'}
    time_column: str  # paid_at
    aggregation: str  # SUM/COUNT/AVG/MAX/MIN
    data_source: str
    owner: str
    synonyms: list[str]  # 同义词


class DimensionDefinition:
    """维度定义"""

    id: str
    name: str  # 渠道、地域、用户类型
    column: str  # channel_code
    table: str
    value_type: str  # categorical/numeric
    hierarchies: list[str]  # 层级关系


class EntityDefinition:
    """实体定义"""

    id: str
    name: str  # 用户、订单、商品
    primary_table: str
    primary_key: str
    related_tables: list[str]
```

#### API端点

```
POST /api/v1/data/semantic/metrics
GET  /api/v1/data/semantic/metrics/{id}
POST /api/v1/data/semantic/dimensions
GET  /api/v1/data/semantic/dimensions
POST /api/v1/data/semantic/entities
```

### 2. SQL智能生成

#### 增强理解能力

```python
# services/control-plane/src/obsion/data/understanding.py


class DataUnderstandingEngine:
    """数据查询意图理解引擎"""

    async def understand(self, question: str, context: Context) -> UnderstandingResult:
        """
        输入: "最近30天新用户付费率为什么下降？"
        输出:
        {
          "domain": "business_analytics",
          "intent": "anomaly_investigation",
          "metrics": ["new_user_payment_rate"],
          "time_range": {"relative": "last_30_days"},
          "comparison": "time_series",
          "need_breakdown": true,
          "need_root_cause": true
        }
        """
```

#### SQL编译器增强

```python
# services/control-plane/src/obsion/data/compiler.py


class SQLCompiler:
    """SQL编译器 - 从逻辑查询生成安全SQL"""

    async def compile(self, logical_plan: LogicalPlan) -> CompiledSQL:
        """
        1. 解析指标定义
        2. 处理时间范围
        3. 添加JOIN条件
        4. 应用过滤器
        5. 添加聚合
        6. 强制LIMIT
        7. 生成AST
        """

    async def validate(self, sql_ast: SQLAST) -> ValidationResult:
        """
        检查:
        - 是否只有SELECT
        - 是否有LIMIT
        - 扫描预算评估
        - 列权限检查
        - 敏感数据脱敏
        """
```

### 3. 查询执行与Evidence生成

```python
# services/control-plane/src/obsion/data/executor.py


class QueryExecutor:
    """查询执行器"""

    async def execute(self, sql: CompiledSQL, context: ExecutionContext) -> QueryResult:
        """
        1. 通过Query Gateway执行
        2. 应用行级权限
        3. 脱敏敏感列
        4. 记录审计日志
        5. 转换为Evidence
        """

    def to_evidence(self, result: QueryResult, metadata: dict) -> Evidence:
        """
        将SQL查询结果转为Evidence对象

        Evidence {
          type: "DATA_QUERY",
          source: "postgresql://...",
          sql: "SELECT ...",
          rows: [...],
          confidence: 1.0,
          timestamp: "...",
          permissions: [...]
        }
        """
```

### 4. 洞察生成 (Insight Generator)

```python
# services/control-plane/src/obsion/data/insights.py


class InsightGenerator:
    """数据洞察生成器"""

    async def generate(self, question: str, evidence: list[Evidence], context: Context) -> Insight:
        """
        从查询结果生成洞察

        输入:
        - 用户问题
        - SQL查询结果Evidence
        - 上下文

        输出:
        - 趋势分析
        - 异常检测
        - 维度钻取
        - 关联分析
        - 可视化建议
        """

    def detect_anomaly(self, time_series: list) -> AnomalyResult:
        """时序异常检测"""

    def compare_periods(self, data: dict) -> ComparisonResult:
        """同比环比分析"""

    def suggest_visualization(self, data_shape: dict) -> ChartSpec:
        """推荐可视化类型"""
```

---

## 📝 具体任务

### Task 1: 建立语义层基础设施

**文件**:
- `services/control-plane/src/obsion/data/semantic/__init__.py`
- `services/control-plane/src/obsion/data/semantic/models.py`
- `services/control-plane/src/obsion/data/semantic/registry.py`
- `services/control-plane/src/obsion/data/semantic/resolver.py`

**数据库迁移**:
```sql
CREATE TABLE metric_definitions (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL,
    name VARCHAR(200) NOT NULL,
    expression TEXT NOT NULL,
    filters JSONB,
    time_column VARCHAR(100),
    aggregation VARCHAR(20),
    data_source_id UUID,
    owner_id UUID,
    synonyms JSONB,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE dimension_definitions (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL,
    name VARCHAR(200) NOT NULL,
    column_name VARCHAR(100) NOT NULL,
    table_name VARCHAR(100) NOT NULL,
    value_type VARCHAR(50),
    hierarchies JSONB,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE entity_definitions (
    id UUID PRIMARY KEY,
    organization_id UUID NOT NULL,
    name VARCHAR(200) NOT NULL,
    primary_table VARCHAR(100) NOT NULL,
    primary_key VARCHAR(100) NOT NULL,
    related_tables JSONB,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Task 2: SQL编译器增强

**文件**:
- `services/control-plane/src/obsion/data/compiler.py`
- `services/control-plane/src/obsion/data/ast_builder.py`
- `services/control-plane/src/obsion/data/sql_validator.py`

**功能**:
- [ ] 多表JOIN自动推断
- [ ] 时间范围表达式处理
- [ ] 聚合函数优化
- [ ] 子查询生成
- [ ] WITH CTE支持
- [ ] 强制添加LIMIT和超时

### Task 3: Query Gateway实现

**文件**:
- `services/control-plane/src/obsion/data/gateway.py`
- `services/control-plane/src/obsion/data/query_router.py`

**功能**:
- [ ] 路由到Read Replica
- [ ] 行级权限过滤
- [ ] 列级脱敏
- [ ] 查询计划缓存
- [ ] 慢查询告警
- [ ] 扫描预算控制

### Task 4: Evidence转换器

**文件**:
- `services/control-plane/src/obsion/data/evidence_builder.py`

**功能**:
- [ ] QueryResult → Evidence转换
- [ ] 附加SQL源码
- [ ] 记录执行时间
- [ ] 标注数据源
- [ ] 权限继承

### Task 5: 洞察生成器

**文件**:
- `services/control-plane/src/obsion/data/insights.py`
- `services/control-plane/src/obsion/data/anomaly.py`
- `services/control-plane/src/obsion/data/visualization.py`

**功能**:
- [ ] 趋势分析
- [ ] 异常检测（Z-score, IQR）
- [ ] 同比环比计算
- [ ] 维度归因分析
- [ ] 图表类型推荐

### Task 6: API端点

**文件**:
- `services/control-plane/src/obsion/api/routes/data.py`

**端点**:
```python
POST /api/v1/data/query
    """自然语言查询数据"""
    
POST /api/v1/data/semantic/metrics
    """创建指标定义"""
    
GET /api/v1/data/semantic/metrics
    """列出指标"""
    
POST /api/v1/data/sql/validate
    """验证SQL安全性"""
    
POST /api/v1/data/sql/explain
    """SQL执行计划"""
    
GET /api/v1/data/sources
    """列出数据源"""
```

### Task 7: 测试

**文件**:
- `services/control-plane/tests/data/test_semantic_layer.py`
- `services/control-plane/tests/data/test_sql_compiler.py`
- `services/control-plane/tests/data/test_query_gateway.py`
- `services/control-plane/tests/data/test_insights.py`

**测试覆盖**:
- [ ] 指标定义CRUD
- [ ] SQL编译正确性
- [ ] 安全策略拦截
- [ ] Evidence生成
- [ ] 洞察准确性
- [ ] 异常SQL拒绝
- [ ] 权限过滤

### Task 8: 前端集成

**文件**:
- `apps/web/src/components/data-query-panel.tsx`
- `apps/web/src/components/sql-viewer.tsx`
- `apps/web/src/components/data-insight-card.tsx`
- `apps/web/src/components/metric-selector.tsx`

**UI组件**:
- [ ] 自然语言查询输入框
- [ ] 指标/维度选择器
- [ ] SQL查看器（带高亮）
- [ ] 结果表格（带分页）
- [ ] 洞察卡片展示
- [ ] 图表推荐和渲染

---

## 🔒 安全要求

### SQL Policy强制执行

```python
# services/control-plane/src/obsion/data/policy.py


class SQLPolicy:
    """SQL安全策略"""

    ALLOWED_STATEMENTS = {"SELECT", "WITH", "EXPLAIN"}
    DENIED_STATEMENTS = {"INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "TRUNCATE"}

    def enforce(self, sql_ast: SQLAST) -> PolicyDecision:
        """
        检查:
        1. 只允许SELECT/WITH/EXPLAIN
        2. 必须有LIMIT（默认500，最大5000）
        3. 超时时间≤30秒
        4. 扫描预算≤1000000行
        5. 禁止访问敏感表
        6. 列级权限检查
        """
```

### 数据脱敏

```python
class DataMasker:
    """数据脱敏器"""

    SENSITIVE_PATTERNS = [
        r"\d{11}",  # 手机号
        r"\d{15,18}",  # 身份证
        r"[\w\.-]+@[\w\.-]+\.\w+",  # 邮箱
    ]

    def mask(self, result: QueryResult, schema: ColumnSchema) -> QueryResult:
        """
        脱敏敏感列:
        - phone: 138****5678
        - email: s***@tuwan.com
        - id_card: 110***********1234
        """
```

---

## 📊 验收标准

### 功能完整性

- [ ] 能处理"最近30天付费用户数"类问题
- [ ] 能处理多表关联查询
- [ ] 能生成正确的GROUP BY和聚合
- [ ] 能处理时间范围表达式
- [ ] 能检测SQL中的危险操作并拒绝
- [ ] 所有查询结果转为Evidence
- [ ] 能生成趋势分析洞察

### 性能要求

- [ ] 简单查询响应时间 < 2秒
- [ ] 复杂查询响应时间 < 10秒
- [ ] SQL编译时间 < 500ms
- [ ] 语义解析准确率 > 90%

### 安全要求

- [ ] 100% SQL经过Policy检查
- [ ] 0 直接数据库连接
- [ ] 100% 敏感数据脱敏
- [ ] 所有操作有审计日志

### 测试覆盖

- [ ] 单元测试覆盖率 > 85%
- [ ] 集成测试覆盖核心流程
- [ ] 安全测试覆盖SQL注入等场景
- [ ] 黄金数据集测试通过

---

## 🚀 开发顺序

### Week 1: 语义层基础
1. 创建数据库迁移
2. 实现Metric/Dimension/Entity模型
3. 实现Registry和Resolver
4. 编写单元测试

### Week 2: SQL编译增强
1. 实现增强的SQL Compiler
2. 实现AST Builder
3. 实现SQL Validator
4. 实现Policy Engine
5. 编写测试

### Week 3: Query Gateway
1. 实现Query Gateway
2. 实现Query Router
3. 实现数据脱敏
4. 实现Evidence Builder
5. 集成测试

### Week 4: 洞察生成
1. 实现Insight Generator
2. 实现异常检测
3. 实现同比环比
4. 实现可视化推荐
5. 端到端测试

### Week 5: 前端集成
1. 实现查询面板
2. 实现结果展示
3. 实现洞察卡片
4. 整体联调

---

## 📚 参考资料

### 内部文档
- `goal.txt` - 核心场景定义
- `rule.txt` - 架构原则
- `docs/architecture/data-intelligence.md`

### 相关Phase
- Phase 100 - Context-first clarification
- Phase 50+ - Query capability基础

### 外部参考
- Apache Calcite - SQL解析和优化
- dbt Semantic Layer
- Looker LookML
- Metabase Metrics

---

## ✅ Definition of Done

Phase 102完成标志：

1. ✅ 语义层基础设施就绪（Metric/Dimension/Entity）
2. ✅ SQL编译器能处理80%常见查询
3. ✅ Query Gateway完整实现并通过安全测试
4. ✅ Evidence转换器工作正常
5. ✅ 洞察生成器能产出趋势和异常分析
6. ✅ 前端集成完成，用户可以自然语言问数
7. ✅ 所有测试通过（单元+集成+安全）
8. ✅ 文档完整（API文档+架构文档）
9. ✅ 代码review通过
10. ✅ 演示视频录制

---

**注**: 严格按照rule.txt执行，禁止MVP代码，必须是产品级实现。
