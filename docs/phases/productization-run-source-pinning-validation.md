# Run 来源版本固定产品化验证

日期：2026-09-08。状态：本地切片通过；M2、真实来源和生产晋级保持未完成。

## 2026-09-08 当前授权与原子性修复（ADR0103）

实现：绑定前等待 Connector 管理锁，随后重载当前用户/角色、刷新 Run 状态；L2 `run.source.pin` 需要明确 ALLOW 且无 obligation。调用者必须拥有事务，内部 SAVEPOINT 保证版本、PolicyDecision、Event、事件 Outbox 与成功 Audit 原子提交。inspection 额外检查当前仓库 ACL。环境只开放 test/development/staging。

验证过程中保留的失败与修复：

- 新增七个权限反向测试先得到 7 failed，证明原实现允许 DENY/ASK/MASK/obligation/停用策略/角色撤权/用户停用；修复后通过。
- 本轮基线完整 Python 为 2047 passed、1 failed、217 skipped、6 deselected（458.38 秒）；唯一失败为 ADR0102 已新增事件而旧注册表测试仍固定 96。核对注册表、schema 与生产者后更新为精确 97，未改成宽松范围或删减检查。
- PostgreSQL 首轮暴露测试夹具违反外键创建顺序，改为依次持久化 Organization → User → Workspace/Repository/Connector → Thread → Turn → Run；没有放宽数据库约束。
- 回滚测试初版在跨 Session 使用已过期 ORM 对象，改为显式刷新；仓库 ACL 夹具改用真实 `created_at` 字段。六个既有钉钉文件的格式问题已由标准格式器修复，无业务行为变更。

专项与基础设施证据：

| 检查 | 结果与边界 |
| --- | --- |
| Run pin 服务与 REST 专项 | 27 passed，12.73 秒；含当前策略/身份、终态旧缓存、生产环境拒绝、外层捕获写入失败后提交、有效 commit 冲突、跨组织/项目、Git 损坏、调用方事务要求、仓库 ACL 撤权 |
| PostgreSQL 17 来源迁移 | 独立空库往返与非空账本降级保护 1 passed，3.62 秒；使用现有 head，含 Alembic drift check |
| PostgreSQL 17 Run pin | 3 passed，1.06 秒；12 个并发请求只产生一个 pin/Event/成功 Audit，12 次 Policy 重查；通过 pg_blocking_pids 观测真实锁等待后验证来源与角色撤权 |
| 来源与事件扩展回归 | 366 passed、1 skipped，126.40 秒；来源管理/账本/REST 的 SQLite+PostgreSQL、Run pin 与 PostgreSQL 事件并发；唯一跳过是 SQLite 无 PostgreSQL 不可变触发器 |
| JavaScript 工作区 | Web 210、Desktop 17、IDE 12、TypeScript SDK 26 全部通过；总计 265，无失败 |
| 全工作区 lint/typecheck/build | 通过，包含 Web 生产构建；没有部署 |
| Python 静态检查 | Ruff 通过、928 个文件格式通过、mypy 246 个源码通过 |
| 合同与文档 | 97 Event 版本/336 Error 代码校验通过；OpenAPI 实时生成与快照完全相同；阶段声明通过，Secret 扫描 0 匹配，git diff --check 通过 |

最终完整 Python 回归结果在下方最终验收段记录。临时 PostgreSQL 实例已清理，业务容器和业务数据库未改变。当前证据来自未提交工作树，不是签名候选、远程 CI、真实集群或生产验收。

运行时代码摘要（SHA-256）：`run_source_pins.py` 为 `9d5fd598b34a2509628e8d027c41c62bfa57d5b1dc59e5710ca525d13bf97c34`；`api/run_inspection.py` 为 `a84c3e2f006567acf03c9d0e01c1a198d483eca8304e5745e0fe43d5000ee722`。

无需新迁移：只使用已有账本及 PolicyDecision/Audit/Event 字段。启用绑定调用方之前需要显式角色权限与范围化 ALLOW Policy；失败回退关闭入口并保留已有账本，不能回退到跳过授权的实现。

另在隔离控制面经真实云效 HTTPS 完成三次目录查询（均 200/SUCCESS），详见 [云效复验](productization-codeup-validation.md)。`obsion`/`openwork` 无匹配，已请求用户指定目标仓库；没有执行源码四类读取、源代码写入、钉钉发送或生产晋级。

## ADR0102 历史验证

实现包含 `RunSourcePin` 不可变账本、`RunSourcePinService`、`run.source_pinned.v1` 事件合同和 `GET /api/v1/runs/{run_id}/source-pins`。服务要求调用者提供已经通过 Gateway 获取的快照和原始 commit 字节，随后在同一数据库事务内复查 Run/Workspace、仓库 ACL、配置版本、撤销账本、配置漂移和 Git 对象完整性。

验证证据：

- `tests/test_run_source_pins.py`：3 passed，覆盖创建/幂等、撤销拒绝、REST inspection、撤销后的可用性投影和脱敏；
- `tests/test_contract_quality_gates.py`：5 passed，事件注册 97 个版本、错误目录 336 个代码；
- Run pin 与契约组合：7 passed；
- 独立 PostgreSQL 17 临时库：来源迁移往返和非空账本降级保护 1 passed；
- Ruff、mypy、`git diff --check` 和 OpenAPI 当前性通过。

限制：没有在本轮读取真实云效仓库或生产凭据；没有把 pin 服务接入 Harness 自动获取、Artifact lineage、Kubernetes/gVisor 传输、持久配额/fencing/recovery；钉钉真实租户、PostgreSQL 多 Worker、UAT 和生产发布仍需要外部证据。
