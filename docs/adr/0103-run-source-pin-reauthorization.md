# ADR 0103：来源版本绑定的当前授权与原子提交

- 日期：2026-09-08
- 状态：已实现，验证记录见下方；不开放生产执行
- 范围：Phase 99 期间的 M0/M2 来源链修复
- 前置：[ADR 0102](0102-run-source-version-pinning.md)、[ADR 0092](0092-policy-audited-project-source-management.md)

## 问题与复现

ADR 0102 的内部绑定服务信任传入 Principal 的权限，未调用 Policy Engine。七个反向用例证明：DENY、ASK、MASK、未满足的 obligation、停用 Policy、角色撤权及停用用户仍可创建 pin。仅在获取源码时授权不能覆盖获取完成后的授权变化。

该服务没有自己的 SAVEPOINT。如果调用方捕获事件或审计写入异常后继续提交事务，可能保留不完整事实。长期 Session 中的 Run 对象也可能缓存旧状态。inspection 只检查来源账本，仓库拒绝授权后仍显示可用。

## 决策

1. 保持 Python 控制面、现有方法签名、REST 字段和不可变数据库模型；绑定仍为内部服务，没有新增外部获取或 Agent 入口。
2. 要求调用方已经开启事务。绑定在内部 SAVEPOINT 内执行；版本记录、PolicyDecision、Event、事件 Outbox 和 Audit 原子写入。异常回滚本次绑定，外层事务仍由调用者拥有。拒绝抛出既有错误，不把失败调用伪装成成功执行；调用者不得把异常当作持久拒绝审计凭据。
3. 先锁来源所属 Connector，再检查 Run/Workspace 和仓库，与来源管理的 Connector → Workspace → Repository 顺序相容。来源撤销和 Connector 更新与绑定互斥；等待锁之后重新加载数据库中的用户、Role 和 Policy，不接受调用者附带的权限。
4. 绑定必须通过 `PolicyEngine.evaluate_resource`，动作为 `run.source.pin`，风险 L2，资源包含 Run、来源、工作区、仓库和配置版本 ID。必须同时拥有该权限、匹配明确 ALLOW 策略且没有待执行 obligation；ASK、MASK、DENY 和无显式 L2 ALLOW 均拒绝。成功审计关联真实 PolicyDecision。
5. 环境仅限 test/development/staging，与当前来源管理的启用范围一致；production 仍关闭。现有内部调用方需要显式配置上述权限与策略，不自动扩大角色或默认能力清单。这是对未公开内部服务的授权收紧，不改变已有公开调用契约。
6. 持有 Run 锁后刷新当前状态；终态继续拒绝绑定。相同版本重放重新授权，不重复创建版本/事件/成功审计；不同有效 commit 仍冲突。
7. inspection 保留 Run ACL 下的历史摘要语义，额外检查当前仓库 ACL。来源撤销、配置漂移或仓库拒绝都将 `source_available` 置为 false。该字段仍不是外部 scopes、网络或后续执行的授权票据。

## 迁移、回退与限制

无需数据库迁移：复用 `a88f69d7c8a0` 的表及已有 PolicyDecision/Audit/Event 字段，没有修改历史迁移或存量记录。回退应关闭绑定调用方，保留不可变事实；不得回退到绕过 Policy 的实现。部署前为需要绑定的角色显式授予 `run.source.pin`，并配置按工作区/仓库/环境收窄的 ALLOW 策略。

此锁只协调本地事务，不是跨网络的权限租约。后续 Gateway 获取、沙箱传输、产物访问和投递仍各自重查授权。真实云效 commit/tree 获取、Harness 自动接线、Artifact lineage、gVisor/CNI、钉钉真实租户和生产晋级不在此验证结论内。

## 验证

见 [验证记录](../phases/productization-run-source-pinning-validation.md) 和 [Phase 99 架构门](../architecture/phase-99-run-source-pinning-gate.md)。新增 PostgreSQL 并发及锁等待测试由现有 CI integration 目录任务自动收集，普通本地回归保持显式跳过。
