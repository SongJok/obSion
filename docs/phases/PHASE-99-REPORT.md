# PHASE-99-REPORT：Run 来源版本固定增量

状态：IN_PROGRESS

2026-09-08 授权修复增量：[ADR0103](../adr/0103-run-source-pin-reauthorization.md) 为绑定接入当前主体重载及 L2 Policy，补齐 Connector 管理锁、锁后 Run 状态刷新和 SAVEPOINT 原子提交；inspection 额外受当前仓库 ACL 约束。27 项服务/API 专项通过，临时 PostgreSQL 来源迁移往返 1 项及并发/锁等待撤销 3 项通过。基线全量发现的事件注册数遗漏已按现有第 97 个事件修复，保持精确断言。最终工作树回归另见 [验证记录](productization-run-source-pinning-validation.md)。无新增 schema 或迁移，无业务数据库变更。

本报告记录 Phase 99 进行期间为产品化来源链补上的 Run 版本固定能力，不代表 Alpha1 生产晋级完成。

已完成：

- 新增不可变 `run_source_pins` 表及前向迁移 `a88f69d7c8a0`；
- 在 Gateway 已获取的快照边界内校验原始 Git commit、tree、文件字节和模式，并写入 Run 的精确版本摘要；
- 新增 `run.source_pinned.v1` 事件、审计记录、幂等重放和冲突拒绝；
- 新增 Run 来源 inspection REST，撤销或异常摘要只返回 `source_available=false`；
- 加入 SQLite 服务/API 回归、契约生产者清单和独立 PostgreSQL 迁移往返验证。

验证结果：Run pin 专项 3 passed；契约质量门禁 5 passed；Run pin 与契约组合 7 passed；独立 PostgreSQL 17 迁移往返 1 passed；Ruff 与 mypy 通过；OpenAPI 快照包含新 inspection 路由。

仍未完成：真实云效来源 commit/tree 获取，来源 ACL 与实际 scopes 的证明，Harness 自动接线，Artifact lineage 传播，Kubernetes/gVisor 项目传输、配额、fencing、恢复，真实钉钉租户、UAT 和 Phase 99 生产门禁。当前阶段继续保持阻塞，不能据此发布或声称完整企业底座已验收。
