# ADR 0080：单一语义运行时与遗留表兼容

- 日期：2026-09-05
- 状态：已实施，完整阶段验收进行中
- 范围：未发布的 Phase 102 语义包修复，不代表 Phase 102 或生产晋级完成

## 背景

`obsion.data.semantic` 引入了与现有治理目录不兼容的 DTO、错误的 ORM 导入和未参数化的 SQL 编译原型。正式 Harness 已使用 `obsion.data_intelligence.service.DataIntelligenceService`，不能再维护第二套目录解释与 SQL 安全实现。

迁移 `f3d4e5a6b7c8` 已应用，`entity_definitions` 和 `query_history` 必须保留。缺少 ORM 映射导致 Alembic 建议删除两表，不能通过实际删表解决漂移。

## 决策

1. `DataIntelligenceService` 仍是唯一正式查询理解与 SQL 编译实现。历史 `SQLCompiler` 和 `DataUnderstandingEngine` 类仅继承该实现，不覆盖编译或理解逻辑。
2. 编译入口要求 `Settings`、数据库会话、`Principal` 及引用已注册指标 ID 的逻辑计划。旧 `compile_metric_query` 和原始表名计划不再支持。这是未发布 Python 原型接口的破坏性调整，不声称保持原有调用签名兼容。
3. Registry 只查询当前组织既有 `Metric`、`Dimension`、`SemanticEntity`。指标只返回经过验证的版本；模糊匹配多个候选时不任取一个。Registry 不是授权入口：调用方仍必须经过现有 API、Policy 和 Gateway，不能把组织过滤等同于资源访问授权。
4. 旧 DTO 暂时保留供历史数据读取；`LogicalQueryPlan`、`CompiledSQL`、`UnderstandingResult` 不代表正式服务的输入输出合同，也不能作为可执行请求。正式合同由 `DataIntelligenceService` 定义。数据源 DTO 使用 `connector_id`，不携带连接串。
5. 用 `LegacyEntityDefinition`、`LegacyQueryHistory` 精确映射已有表，保持类型、索引、默认值和可空性，不新增运行时写入。`semantic_entities` 仍是实体目录；Event Store 仍是唯一运行轨迹事实源。
6. 本次不执行 DDL，不删除或回填任何既有行；历史迁移仅有规范化格式变更，无数据库语义变化。将来如需治理约束、数据迁移或退役，必须采用独立前向迁移并验证数据保留。

## 验证与边界

专项回归覆盖包导入、实现复用、原始表名计划拒绝、连接器引用、时区时间、遗留映射存在和模糊候选拒绝。既有语义、SQL Gateway、DataAgent、单一 Event 协议测试继续保留，不降低断言。

本地 `alembic check` 已报告无新增操作；此结果仅证明当前 schema 与 metadata 一致，不是新建数据库迁移、真实只读查询或生产批准的替代。

后续验收仍需覆盖 Registry 的真实数据库租户/版本隔离、完整回归及真实 PostgreSQL 查询闭环。当前服务仍主要支持单表查询，不能声称已经支持安全多表 JOIN、完整自然语言时间解释或云效数据血缘。
