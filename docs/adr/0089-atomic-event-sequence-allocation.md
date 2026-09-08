# ADR 0089：事务内原子事件序号分配

- 日期：2026-09-06
- 状态：已实现并完成本地专项验证；完整工作树复验另行记录，不是生产晋级声明
- 关联：[ADR 0002](0002-transactional-event-store.md)

## 背景

项目副本增量后的完整 Python 回归为 **1594 passed / 2 failed / 26 skipped / 6 deselected**。其中既有 Workflow Gateway 测试偶发 `IntegrityError`；普通独立重跑十次没有复现，不能用偶然通过关闭问题。

合成交错在 API 读取 Run 后让另一事务推进同一 Run，稳定复现 `events.run_id, events.run_sequence` 唯一冲突。`expire_on_commit=False` 的 SQLAlchemy identity map 保留旧对象，后续 `SELECT FOR UPDATE` 不自动刷新其属性；SQLite 又不实现行级 `FOR UPDATE`。旧代码用缓存中的计数加一，锁的存在不能证明读到了最新版本。AggregateHead 首次创建的 read-then-insert 同样没有可锁的既有行。

## 决策

1. Run 序号由数据库执行 `UPDATE runs SET aggregate_version = aggregate_version + 1 ... RETURNING aggregate_version` 分配，条件包含 Run ID 和 organization。不存在或组织不匹配仍使用既有 `event_run_missing`，不扩展错误码集合。
2. AggregateHead 使用 PostgreSQL / SQLite 原生 `INSERT ... ON CONFLICT DO UPDATE ... RETURNING sequence`，同组织才能增加计数；不把外租户的聚合头认领给当前请求。不支持其他数据库方言，不引入第二事实源。
3. 保留 Event 的 aggregate 唯一序号、Run 唯一序号及 Outbox 的 event_id 唯一约束。事件与 Outbox 使用同一冻结 envelope，在调用者事务内写入，不重试整个 Gateway/能力调用，也不重复外部副作用。
4. 先校验和脱敏 draft，再 flush 调用者已有变更；计数、最终 envelope 校验、Event 和 Outbox 写入使用内部 SAVEPOINT。即使调用者捕获事件失败并提交外层事务，失败事件也不留下部分计数、Event 或 Outbox。调用者原有业务变更的提交/回滚仍归外层事务负责。
5. SQLite legacy transaction 模式下 SELECT 不启动物理事务。在内部 SAVEPOINT 前执行不匹配任何行的空 UPDATE，防止释放 SAVEPOINT 意外提交、外层 rollback 无法撤回事件。这是 SQLite 本地兼容处理，不是 PostgreSQL 行锁的替代证明。
6. DML 使用 `synchronize_session=False`，不基于旧 ORM 值计算计数。仅在内部 SAVEPOINT 成功后，用数据库返回值同步 identity map 中已经存在的 Run/Head 计数字段，不全对象刷新或覆盖调用者其他字段。
7. `set_committed_value` 本身不登记 SQLAlchemy 的事务变更。同步后以公开 `flag_dirty` + flush 登记状态，无属性历史变化时不额外 UPDATE，让调用者外层 SAVEPOINT 回滚按 SQLAlchemy 规则使缓存失效。AsyncSession 调用者读取已失效属性时须显式 refresh；不能保留看似已提交的错误序号。

## 验证

共用合成场景在 SQLite 和一次性 PostgreSQL 上执行，使用完整 Organization → User → Workspace → Thread → Turn → Run 外键链，不拿 SQLite 忽略外键的最小假数据冒充 PostgreSQL 验证：

- 旧 identity map 不重用序号，保留未 flush 的 status/plan。
- 有 Run / 无 Run、已有 / 首次聚合头四种组合，每组 12 个独立并发事务；序号连续且 Event/Outbox 一一对应。
- 外层事务 rollback 同时撤回计数、Event、Outbox，下次从相同序号开始。
- 捕获最终 envelope 失败后无计数缺口，缓存保持原值。
- 捕获真实数据库主键冲突后 session 可继续使用，失败事件与计数不残留。
- 调用者外层 SAVEPOINT rollback 后，缓存失效或回到正确值，事件与 Outbox 不残留。
- 外组织 Run 和聚合头拒绝，失败的 Run 增量回滚。

原 Workflow 测试保留正常场景，并新增确定性交错参数：在真实访问检查读入 Run 后，独立事务提交一条合成事件，确认请求中的 ORM 仍旧，再执行原 Gateway dispatch 全部断言。不用 sleep 碰运气、不重试 HTTP 请求，也不删除真实后台 worker。

已执行结果：

- SQLite 十项序号专项 **10 passed，3.72 秒**；目标 Ruff/mypy 通过。
- 序号 / EventStore / 冻结契约 / Workflow / 错误来源门合并 **38 passed，36.08 秒**。
- 一次性 PostgreSQL **17.11** 首轮相关十项 **10 passed，4.62 秒**；新增外层 SAVEPOINT 与持久化失败回归后的通用 integration 全集 **27 passed / 7 skipped，6.72 秒**。七项为另行 opt-in 的迁移往返，不能计通过；审计专用迁移按既有 CI 入口排除。
- 两次独立容器均从空库 upgrade 到 `a83d14e25f36`，`alembic check` 无模型漂移；测试后只清理本次自建容器。随机 loopback 端口、纯测试凭据、无业务挂载、空 cwd 与剥离继承应用配置，不读取仓库 `.env`。
- 新 PostgreSQL 测试进入既有 CI 的 integration 收集范围；未运行远程 CI。
- 全部修复后的完整 Python **1630 passed / 36 skipped / 6 deselected，308.42 秒**；全仓 Ruff、869 文件格式、mypy 232 个源码及 `git diff --check` 通过。此后仅同步验证文档，不把本地工作树当成干净发布候选。

失败历史也保留：首轮合成用户漏填 email、无 Run 场景误用需 Run 的事件契约、错误来源清单未跟随方法拆分、SQLAlchemy 类型注解不完整均已修复。加强断言分别发现内部 SAVEPOINT 失败后缓存计数残留，以及调用者 SAVEPOINT 回滚不失效的问题；没有删掉断言迁就实现。

## 兼容性与边界

没有表结构、索引、公开 API、事件 schema 或错误码变化，无需数据库迁移。静态错误来源清单只将同一 `event_run_missing` 从 `EventStore.append` 移到 `_append_prepared`，保留精确覆盖门禁。

该修复解决事件序号分配与事务一致性，不保证任意多 Run/多聚合锁顺序没有死锁，不替代外层业务状态并发控制，也不授权自动重试业务副作用。它不实现 Outbox 外发、项目来源授权、沙箱集群或生产容量验收。完整 Python 回归与产品化边界见 [M0 验证](../phases/productization-m0-validation.md)；正式阶段和生产晋级阻塞保持不变。
