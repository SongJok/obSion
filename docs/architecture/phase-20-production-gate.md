# Phase 20 — Critic、治理控制面与生产硬化

## 目标

把“可运行的 Harness”收敛为可审计、可回放、可安全运营的企业底座。执行 Agent 不得为
自己的答案背书，所有事实回答必须经过独立 Critic 和发布边界。

## 交付合同

### Critic

`obsion.harness.critic.Critic` 是无副作用的确定性验证器。它不访问模型、数据库或外部
连接器，只消费当前 Run 的 Evidence/Claim 投影。规则包括：

- 必需 Evidence 类型与空检索结果；
- Claim 的非空陈述、Evidence 链接和 Incident 跨类型门槛；
- Evidence 有效时间区间；
- Metric/Data 的 measure、unit、environment、definition version 一致性；
- Data 的 SQL validation/read-only 标记；
- Incident 因果 Claim 的信号与独立变更证据；
- 显式 provider 冲突、同一服务相反状态、重复 Evidence 和问题覆盖。

输出沿用 `critic.completed.v1` 契约：`verified`、`confidence`、`coverage`、
`missing_evidence`、`conflicts` 和四项兼容 checks。新规则通过 conflict reason code 进入
既有事件和回答 Artifact，不扩大事件 schema。

### Immutable verification graph

Harness 在生成 Claim 后追加一次 Assessment（`phase20.critic.v2`）及其 Claim 结果、
Evidence 链接和可成对的冲突记录。缺少同一 Run 的 PolicyDecision、没有 Claim 或任何
规则失败时，Assessment 使用 `PARTIAL/WITHHOLD`；只有 Critic VERIFIED、所有 Claim
结果 VERIFIED 且策略决策存在时，数据库才允许 `VERIFIED/PUBLISH`。PostgreSQL 的延迟
约束和不可变触发器防止事后改写验证结果或已封存 Claim generation。

### Governance and security

管理 API 仅返回组织范围内的治理元数据。Model endpoint 列表只返回 `has_credential`，
Secret reference、Connector credential 和解析后的 secret 永不进入浏览器、Evidence、
Audit metadata 或模型消息。外部调用必须经过 Capability Gateway/Model Gateway 的
Policy、Grant、环境、限流、超时、审计和 Evidence 链路；生产写入、部署和重启继续
fail-closed。ApprovalService 只允许合法的 PENDING 审批被同一组织内有权限的操作者决定。
Agent/Skill manifest 在注册时递归拒绝数据库 DSN、endpoint、credential 字段、内联密钥和
私钥块，防止额外字段被固化到版本快照后进入模型上下文或形成直连执行配置。

## 验收证据

- `tests/test_critic.py` 与 Incident/Knowledge/Data 黄金回归：无证据、冲突、口径不一致、
  非只读 SQL 和单类型根因均不会产生高置信 VERIFIED；正常三条黄金路径保持可回放。
- `tests/test_phase20_production.py` 直接覆盖通用审批通过/拒绝、事件与审计、模型请求脱密、
  浏览器管理投影、Incident Top1/Top3、跨类型 Claim、Verification graph 与 Replay ID 重映射。
- `tests/integration/test_postgres_verification.py`：验证聚合表不可变、发布准入、跨租户
  外键、Claim generation 封存和冲突完整性。
- Contract/Error/Event、Ruff、mypy、前端 lint/typecheck/test/build、Compose 与 Helm
  模板门禁继续作为发布前阻断项。

代码平台、可观测平台、模型供应商、生产 egress 与凭据保留策略仍需要系统所有者确认。
该确认状态不阻塞连续开发，也不应被表述为已完成的人工批准。
