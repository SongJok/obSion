# ADR 0093：受治理的项目来源管理 REST

- 日期：2026-09-06
- 状态：已实现并完成本地回归；不开放源码获取或生产管理
- 关联：[ADR 0092](0092-policy-audited-project-source-management.md)、[M2 架构门](../architecture/productization-m2-gate.md)

## 决策

在现有 `/api/v1` 受保护 REST 下增加四个仅管理元数据的动作，直接复用 `ProjectSourceService`，不新增后端、事实源、Agent 能力、CLI 或 SDK 方法。

| 路径（均为 POST） | 权限 | 成功响应 |
| --- | --- | --- |
| `/admin/project-sources/versions` | `project_source.version.create` | 201，新独立配置版本 |
| `/admin/project-sources` | `project_source.register` | 200，CREATED 或 UNCHANGED |
| `/admin/project-sources/versions/{connector_version_id}/revoke` | `project_source.version.revoke` | 200，CREATED 或 UNCHANGED |
| `/admin/project-sources/{source_id}/revoke` | `project_source.revoke` | 200，CREATED 或 UNCHANGED |

生产环境在依赖层拒绝，内部服务仍保留独立生产拒绝。不注册 fetch、clone、download、执行命令、读取配置或凭据的接口。路由出现在 OpenAPI 不代表生产可调用或已部署。

## 认证、授权和输入

复用 `get_principal` 的 Bearer / 可撤销 HttpOnly Cookie 身份和既有浏览器 Origin 检查。来源服务获得的是认证主体，不接收消息发送者、actor、组织、角色、permissions、actual_scopes、environment 或配置快照等可提权字段。只有 `im.delegate` 权限不足以管理来源；不通过 IM sender 映射转为另一主体，也不把应用声明 scopes 当实际授权。给主体显式分配来源权限和匹配 L2 ALLOW Policy 仍属于既有管理面，不自动追加权限、默认 ALLOW 或绕过 obligations。

请求模型 `extra=forbid`，UUID 强类型；仓库标识有长度和封闭字符约束；撤销原因只能是 ADR0092 的三个固定代码。版本创建只接收 Connector UUID，配置从锁定数据库记录获取，不接受调用者 JSON 替代。来源登记保留 Workspace 写权限与仓库 SQL ACL 交集，环境、配置漂移、撤销和重放均由现有服务重查。

响应只包含 outcome、目标记录 UUID、PolicyDecision UUID、固定原因。不返回配置、凭据引用、厂商仓库 ID、源码或所谓 VERIFIED 状态。成功登记仍不是实际 scopes、厂商身份或 Run commit/tree pin 证明。

## 事务和错误

路由显式开启外层事务，调用现有服务后退出 `session.begin()` 完成提交，随后才把结构化结果转为 HTTP 成功或错误。预期的 DENIED / INVALID / CONFLICT 审计不会因为抛 HTTP 错误而回滚。审计或数据库异常传播，由现有统一异常处理返回不含异常内容的 500，不确认成功；提交前故障回滚外层事务。连接在数据库提交后中断仍可能结果不确定，500 不能当作“数据库一定没有写入”，必须先对账，不能盲重试。

真实 `Database` 路径红测发现 SQLite legacy 事务模式会使先于真实 BEGIN 的 SAVEPOINT 独立提交：外层 `before_commit` 故障后仍遗留版本、PolicyDecision、Audit 各一条。首次修复为所有事务显式 BEGIN，虽通过来源专项，却使既有并发 Run 读取后写入发生 SQLite 锁升级失败，故不采用。

最终修复只在运行时 SQLite 的 SAVEPOINT 事件检查真实驱动 `in_transaction`；若尚无数据库事务，先 `BEGIN IMMEDIATE` 再创建 SAVEPOINT，确保释放 SAVEPOINT 不会绕过外层提交，且先获取写预留锁以免之后读写升级。已有 DML/外层 SAVEPOINT 则不重复 BEGIN。普通 SELECT 保留既有 legacy 行为，不声称 repeatable read；PostgreSQL 连接与事务配置不变。新增直接 Database 回归覆盖有/无先前写入的外层提交与回滚、嵌套回滚/连续 SAVEPOINT，以及普通读取不阻塞独立写入。不靠测试专用事务配置掩盖运行时缺陷，无 schema 变化。

增加三个兼容性扩展错误码：

- `project_source_denied`：403，权限、不可用状态、缺失/跨租户资源统一响应，不返回内部 reason。
- `project_source_invalid`：422，封闭配置或业务参数无效，不回显配置。
- `project_source_conflict`：409，固定 tuple 已绑定不同声明，不回显旧标识。

业务错误 details 仅关联 PolicyDecision UUID，认证/生产环境拒绝使用统一请求 correlation 日志但没有来源 Policy/Audit 记录。请求结构校验失败复用 `request_validation_failed`；请求体和校验器细节不进入错误响应。未知组织仍不能用伪造组织 FK 写来源审计，认证失败由现有 `request.rejected` 记录，不引入跨租户审计旁路。这不声称提供全局持久认证失败账本。

版本创建不是幂等 API：每次成功生成新 UUID。客户端不能因丢响应盲重试并将新 UUID 当旧回执；运维可通过既有授权审计查询对账。来源登记和双撤销幂等只针对固定目标事实，重放仍须重新授权、追加新的 Policy/Audit。

## 兼容、验证与限制

无数据库字段/约束变更，复用 a84 账本和 Policy/Audit，因此没有新迁移。OpenAPI 仅追加四路径、四请求/响应模型；既有路径和模型保持不变。精确错误目录与 producer 清单同步，未禁用静态错误分析。静态分析器原来仅识别 FastAPI 构造函数内的响应模型引用，本轮将同样的严格识别补充到 APIRouter；仍拒绝模块级映射、别名逃逸、非 model 键和构造器遮蔽，补充两种构造器正反向测试，不排除新路由文件。

71 组测试按 SQLite/PostgreSQL 双参数执行，使用真实 ASGI 路由、共享认证、运行时 Database、独立 Session 和 Policy/Audit；不启动 lifespan/worker，不联网，不解析 Secret。SQLite 仅额外每连接启用测试 FK，BEGIN 行为来自运行时，不替换事务实现；不能用它冒充 PostgreSQL 锁、触发器或部署。PostgreSQL 使用一次性合成容器，分别验证成功/拒绝提交、Audit 失败及外层提交前故障原子性。普通入口的 71 项 PostgreSQL opt-in 跳过须单列，不证明网络丢失后的提交结果确定性。

最终 SAVEPOINT 方案后的直接数据库/来源 REST/既有并发 API 组合为 141 passed / 71 skipped（91.64 秒）。一次性 PostgreSQL 17.11 通用 integration 为 372 passed / 9 skipped（107.69 秒，169 SQLite + 202 PostgreSQL + 1 纯契约），四独立迁移各 1 passed；本切片三次临时容器均清理。中间所有事务显式 BEGIN 引入的并发回归及停止的全量不算通过；完整最终验证及各轮失败详见 [M2 验证记录](../phases/productization-m2-validation.md#2026-09-06-受治理来源-rest-与真实事务修复)。

所有源码/测试增量后的完整 Python **1868 passed / 213 skipped / 6 deselected（382.92 秒）**，Ruff、**887 文件格式**、四工作区严格 **mypy 239 源码**和 `git diff --check` 通过。213 skip 包含本轮 71 项 PostgreSQL opt-in，实际数据库结果单列，不把跳过算通过。没有前端改动，本轮未重跑 JavaScript；随后只同步文档，不是 clean candidate、远程 CI 或真实租户验收。

厂商身份和实际 scopes、Run commit/tree pin、受限来源获取、来源 ACL/受众传播、Gateway/Harness、Kubernetes 项目传输、持久实例/配额与真实集群验收仍待实现。这个 REST 管理切片不使任务8、M2 或生产晋级完成。
