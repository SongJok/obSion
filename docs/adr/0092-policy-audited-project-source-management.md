# ADR 0092：Policy 与审计约束的内部来源管理登记

- 日期：2026-09-06
- 状态：内部实现、最终完整本地回归与一次性 PostgreSQL 验证通过；没有公开入口或来源获取
- 关联：[ADR 0091](0091-project-source-version-ledger.md)、[M2 架构门](../architecture/productization-m2-gate.md)

## 决策

在单一 Python 控制面新增 `ProjectSourceService`，复用 ADR 0091 的四张不可变事实表、现有身份重载、`PolicyEngine.evaluate_resource` 和 `AuditWriter`。不新增数据库字段或迁移，不修改已验证的 a84 迁移，不新增 REST/CLI/SDK/Agent 能力。

四个独立权限 action：

- `project_source.version.create`：从锁定的同组织 Connector 生成独立配置版本，不接受调用者配置快照。
- `project_source.register`：按明确 Workspace、CodeRepository、配置版本和稳定仓库标识登记来源。
- `project_source.version.revoke`：追加版本撤销事实。
- `project_source.revoke`：追加来源撤销事实。

这些是 **L2 内部敏感元数据管理**，不是源码写入、权限授予或外部操作。要求当前数据库 Principal 拥有对应权限，且 Policy 明确返回无额外义务的 `ALLOW`；默认 L2 `MASK`、`ASK`、`DENY` 以及无法执行的义务均拒绝。不添加默认 ALLOW Policy，不给系统角色追加新权限，既有通配管理员仍必须有匹配 ALLOW。不修改普通 Capability 的副作用/高风险只读门禁。

## 来源与保密边界

1. 仅 `test`、`development`、`staging` 的内部服务可实例化，生产拒绝。无网络调用、Broker 解析、环境变量读取、Git 命令或源码内容。
2. 新来源专用配置为封闭的最小 `git-http` 声明：HTTPS origin、一个精确 authority、仅 `code.read` 声明、最多 100 个明确仓库标识；configuration 只允许 `allowed_repositories`。未知字段（包括 headers、token、命令等）拒绝，不能忽略或裁剪后冻结。
3. 该最小声明不等于已有可用 git-http 网络适配器。厂商差异、仓库真实身份、实际 scopes、DNS/连接时 egress 检查和 URL 构造仍待可信 Gateway 获取路径实现。现有通用 Connector 管理的兼容性不变；旧配置即使含无害额外键，也不能未经显式治理自动变成新来源。
4. 凭据只允许 `None` 或同组织存在的 `secret://<name>` 引用；不读取 SecretReference.external_ref/envelope，不解析凭据值、不验证提供商可用性，不冻结 SecretReference 背后的可变目标。不允许直接 `env://`、URL userinfo、query/fragment 或任意配置字符串承载秘密。复用纯文本扫描补充已知匹配，不能把扫描无匹配称为完整 Secret 证明。
5. 返回值仅 outcome、记录 ID、PolicyDecision ID 和固定原因。审计仅内部资源 UUID、主体、动作、风险、决策和固定原因，不包含 endpoint、配置、凭据引用或厂商仓库标识。
6. 登记要求 Workspace 写访问及 CodeRepository 读取授权交集，复用既有 Workspace 和 Code Graph SQL 授权谓词；仓库 DENY grant 优先。来源登记不是给整个 Workspace 成员授予仓库读取权。后续每次获取、上下文/产物访问和最终发送仍须完整授权交集。

## 事务、重读与撤销

调用者必须显式持有事务。每次服务调用用 SAVEPOINT 原子保存事实、PolicyDecision、Audit；Audit/DB 等不可预期异常使本次 SAVEPOINT 回滚，即使调用者捕获异常后提交，也没有半条事实。SAVEPOINT 开始前 SQLAlchemy 会 flush 调用者已有待写内容，这些内容不属于本次服务的回滚范围。

可预期的权限/状态/配置拒绝返回 `DENIED` / `INVALID` / `CONFLICT`，便于调用者提交决策与拒绝审计；不先抛异常让通常的事务上下文直接回滚审计。成功 `CREATED` / `UNCHANGED` 也只是当前事务结果，外层提交前不得宣称已持久化。外层回滚会同时回滚本次事实和审计；不存在脱离事务的旁路审计数据库。未知组织无法满足审计组织 FK，返回无决策 ID 的主体拒绝，未来认证边界另行记录。

PostgreSQL 先按 Connector → Workspace → CodeRepository 的固定顺序获得行锁，再重新加载主体与 Policy。避免等待锁前的允许决策用于锁释放后的登记。配置读取采用数据库列而非 ORM identity map；身份加载和资源 Policy 查询增加 `populate_existing`，避免同一 Session 中已缓存 Role/Policy 掩盖撤权。配置 JSON 往返深拷贝，不引用可变 Connector dict/list。

相同固定来源 tuple 与相同厂商标识重放返回 `UNCHANGED`，仍重新授权并写新的审计；不同标识返回冲突，已撤销来源不重建。每次创建版本都生成新 ID，不隐式查询 latest 或内容去重。撤销重复调用保留首个主体、原因和时间，只新增调用审计；Connector 禁用/配置漂移不阻止有独立权限的撤销。来源撤销还要求项目 ACL，仓库删除/ACL 已收紧时可由有版本撤销权限的操作方终止整个旧配置版本，不能为了撤销而扩大代码权限。两类撤销都检查固定版本环境，不因只撤销不获取便允许跨环境管理；但当前 Connector 环境/配置漂移仍不阻止在原版本环境终止旧版本。

行锁只维护本地登记事务一致性，不是外部请求期间的 fencing/租约。SQLite 只用于查询/事务分支测试，没有 PostgreSQL 行锁语义。角色、成员、Policy、资源撤权仍是各查询在事务中可见的状态，不声称已实现全系统线性化撤权或外部调用许可。

## 验证与未完成项

需实际 PostgreSQL 覆盖：相同绑定并发、双撤销幂等、真实行锁等待期间配置/Policy/角色/撤销变化后的拒绝、成功审计原子性与调用者回滚。普通入口中的 PostgreSQL opt-in 跳过不得计为通过。真实数据库使用独立合成容器，无业务库或真实凭据。

厂商身份/实际 scopes、Run commit/tree pin、来源获取、Artifact ACL/受众传播、Gateway/Harness、Kubernetes 项目传输、恢复配额和真实集群/容量验收仍未完成。内部管理服务不是 M2 完成或生产晋级证据。
