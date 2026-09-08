# Phase 99 Run 来源版本固定架构门

状态：IN_PROGRESS

本门约束 Run 使用项目来源时必须有可审计、不可变且可重查的版本事实。

必须保持：

1. 来源获取先经过 Capability Gateway、Policy、Connector 凭据 Broker、Workspace/仓库 ACL；pin 服务不执行厂商网络调用，也不接收凭据。
2. pin 只接受已构造的 `ProjectSnapshot` 和原始 commit 字节，并验证 commit/tree、完整文件内容、文件模式、大小和指纹。
3. 每个 `(organization, run, source)` 只能有一个不可变版本；相同版本幂等，不同版本冲突，终态 Run 拒绝新增绑定。
4. 创建、Event 和 Audit 必须在调用者事务内原子提交；来源撤销或配置漂移不会删除历史 pin。
5. inspection 必须经过 Run ACL，并在读取时重查当前来源状态；任何异常只降低可用性，不把历史记录当作当前授权。
6. Event payload、REST response 和 Audit metadata 不得包含源码、endpoint、完整配置、credential_ref、token 或实际 scopes。
7. [ADR0103](../adr/0103-run-source-pin-reauthorization.md) 要求等待 Connector 管理锁后重载主体和策略，通过 L2 `run.source.pin` 明确 ALLOW 且无 obligation；原始 Principal 不作为权限事实。production 不启用。
8. 绑定要求调用方事务，并在 SAVEPOINT 内原子写入 pin、PolicyDecision、Event、Outbox 和 Audit；调用方捕获异常后提交其他工作不能留下部分绑定。
9. Run 状态以锁后刷新值为准；inspection 的来源可用性同时受当前仓库 ACL 约束。重放重新授权且不重复事件。

当前证据：27 项本地合成服务/API 专项通过；一次性 PostgreSQL 17 迁移往返 1 项、12 请求并发幂等及来源/角色锁等待撤销 3 项通过。全量验证结果见对应验证记录；不把此并发证据扩展为完整 Harness/沙箱/IM 多 Worker 验收。

本门不能推出真实云效身份或 scopes、外部来源内容、Harness 自动执行、Artifact ACL 传播、真实沙箱隔离、钉钉租户验收、容量恢复或生产晋级结论。
