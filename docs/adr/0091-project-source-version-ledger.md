# ADR 0091：项目来源配置版本与不可撤回的撤销账本

- 日期：2026-09-06
- 状态：内部实现、真实一次性 PostgreSQL 与最终完整本地回归通过；不开放来源获取
- 关联：[ADR 0090](0090-git-project-content-integrity.md)、[M2 架构门](../architecture/productization-m2-gate.md)

## 背景

Git hash 只能验证内容。现有 Connector 是可变配置，代码仓库没有 Workspace 来源绑定。不能把 Connector ID 当版本，不能把 Artifact 调用者 lineage 当权限事实源。Workspace 已有 `(organization_id, id)` 唯一约束；Connector 和 CodeRepository 需要补充同样的复合引用目标。

## 决策

1. 新增 `connector_configuration_versions`，保存独立版本 ID、Connector 引用、类型、环境、endpoint、configuration、credential_ref、declared_grants、allowed_egress、创建主体和时间。不复制可变 health/status。所有字段不可 UPDATE/DELETE；配置 JSON 是冻结的配置内容，不是可变版本事实。旧 Connector 和 Gateway 保持兼容，不自动回填版本。
2. 新增不可变 `project_sources`，绑定同组织 Workspace、CodeRepository、Connector 配置版本与厂商稳定仓库 ID。对 `(organization_id, workspace_id, repository_id, connector_version_id)` 唯一；更换来源必须显式建立新的配置版本和来源，不在原行替换，也不查询 latest。
3. `connector_version_revocations` / `project_source_revocations` 分别记录版本或来源撤销。每目标最多一条，目标、主体和时间均不可 UPDATE/DELETE。撤销不抹掉配置/来源历史，不使用可恢复的 active 标志。撤销原因限于固定代码，不储存任意 Secret 或来源内容。
4. 所有目标和创建/撤销主体使用同租户复合 FK，引用采用 RESTRICT。增加父表复合唯一键，不改历史迁移；前向迁移从 `a83d14e25f36` 延续。PostgreSQL 触发器保护四张事实表，包括禁止 TRUNCATE；有任何事实时拒绝降级，降级先锁住账本再判断，不能静默丢失撤销记录。
5. 新增内部来源状态检查，按显式组织/Workspace/仓库/配置版本查数据库，检查组织启用、Workspace 未归档、仓库未删除、Connector ACTIVE、来源与版本未撤销，以及配置没有偏离固定快照。查询前显式 flush 纳入当前事务待写的撤销/禁用，读取列值而非依赖 ORM identity map，失败只返回固定原因，不泄漏 endpoint/configuration/credential_ref。该检查不返回源码、凭据或权限票据。
6. 配置漂移拒绝，而不是跟随最新值；即使恢复旧配置，已撤销版本仍不可复活。查询只证明当前事务快照中的来源状态，不保证未来外部调用时仍有效，不是跨网络租约或 fencing。

## 授权与保密边界

本切片只提供内部持久化结构和来源状态前置检查。没有管理面创建/撤销 API、Agent 能力、外部获取、实际 scopes 证明、Run commit/tree pin 或产物来源传播。数据库写入仍属于可信控制面；合成测试直接构造记录不是生产授权流程。实际创建和撤销入口必须先接 Policy 与审计，不能因可写 ORM 类存在便给 Agent 写入能力。

`declared_grants` 不是应用实际 scopes；厂商仓库 ID 不是远端身份已核实的证明；固定版本中只允许受治理凭据引用，不存凭据值，也不把该引用传给 Agent/沙箱。现有管理面配置安全校验与 Broker 不被替换。未来版本创建应从已校验、锁定的 Connector 获取快照，不能接受调用者伪造的配置/厂商证明。

来源状态检查不能代替用户、Workspace/仓库 ACL、应用实际 scopes、Connector grants、Agent 清单、任务授权、环境和预算的权限交集。授权必须通过 Policy；每次获取、使用、产物发布/下载与最终发送均重查。现有 Policy 只读边界不变，当前未接通 Gateway/Harness。

## 验证要求

- 真实一次性 PostgreSQL：完整 upgrade/check、空账本 downgrade/re-upgrade、非空账本降级拒绝。
- 跨租户版本、Workspace、仓库、创建/撤销主体及目标 FK 拒绝；不可变 UPDATE/DELETE/TRUNCATE 必须断言触发器 SQLSTATE，不以 FK 失败冒充通过。
- 固定版本不跟随 Connector 配置、跨租户/错版本不命中、撤销后拒绝且不能删记录复活、已有 ORM 缓存不隐藏最新状态。
- 本地 SQLite 仅验证查询行为和元数据兼容，不能代替 PostgreSQL 触发器证据。
- 更新 M0/M2 验证与项目状态。M2/Phase 99 仍未完成，真实来源、gVisor/CNI、容量和生产门禁须独立验收。
