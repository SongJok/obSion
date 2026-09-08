# 语义基线修复验证记录

日期：2026-09-05。状态：修复进行中；不是 Phase 102 完成报告，也不是生产晋级批准。

## 已实施

- 依据 ADR 0080 将历史语义编译和理解入口收敛到 DataIntelligenceService。
- 精确映射已部署的 entity_definitions/query_history，保留数据，不新增运行轨迹写入路径。
- 指标 Registry 使用组织过滤、validated 过滤和数据库窗口函数选择最新版本；列表应用数据库 LIMIT。
- 搜索采用按名称游标分页，避免前 1000 个目录项截断造成漏召回或隐藏歧义。每页最多 1000 项，找到请求数量的候选后停止；未命中仍可能遍历全目录，后续需建立索引化搜索和查询预算。
- SQL 执行器要求 read_only 严格为布尔 True；先检查 parameters 类型再计算参数元数据，避免非法输入产生 TypeError。
- API serve 默认使用 Settings 的地址和端口，命令行显式参数优先；Makefile 复用此入口。

## 实测结果

| 验证 | 结果与范围 |
| --- | --- |
| 最近完整 Python 回归 | 1178 passed / 28 skipped / 0 failed，267.21 秒；发生在本次跨页搜索补丁之前 |
| 跨页搜索补丁后专项 | 27 passed，28.46 秒；覆盖语义兼容、目录数据库、SQL 只读输入及错误契约门 |
| 正式 mypy 范围 | 217 源码文件通过；跨页修改后的语义包 5 文件类型检查通过 |
| services/packages/apps Ruff | 通过；不能外推为根目录旧脚本全部通过 |
| 本地 Alembic check | 已验证无新增升级操作；不代表干净数据库迁移或数据保留演练 |
| API CLI 优先级 | 3 项通过，未为验证而停止现有服务 |

目录数据库专项使用隔离 SQLite，验证指标、维度、实体的租户/版本选择，以及超过 1000 条目录的末页命中和跨页歧义。它不证明 PostgreSQL 外键、事务隔离或生产目录授权；Registry 仍不是 Policy 授权入口。

28 项 skip 包含 21 项显式启用的 PostgreSQL 测试和 7 项真实外部服务测试，均未计为通过。执行器安全测试使用 mock 验证网络调用前拒绝，不等于真实 PostgreSQL 成功查询。

## 2026-09-05 M0 补充复验

包含跨页补丁的工作树已完成本地 Python 全量回归（1181 passed / 22 skipped / 6 live deselected）及 JavaScript 全工作区 258 项测试。独立 pgvector PostgreSQL 的完整 upgrade head、Alembic check、16 项不变量和 5 项历史迁移往返均通过。结果与后续增量见 [M0 验证记录](productization-m0-validation.md)；这些证据不代表真实模型/业务数据库闭环或生产晋级。

## 未完成

- 干净候选快照完整复验与历史阶段证据收口。
- 真实 PostgreSQL 从 Harness 到 Evidence/Audit/Replay 的成功闭环。
- 剩余根目录规范问题、凭证残留与正式治理状态更新。
- DWS 正式入口、群聊受众授权与云效专用只读适配器。
- 生产晋级、外部凭证轮换、数据所有者及安全批准。没有代填批准、重写 Git 历史或执行生产写入。
