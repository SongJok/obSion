# Run 来源版本固定产品化架构门

状态：本地切片通过；生产门保持关闭。

Run 的源码来源必须形成以下链路：来源账本版本 → Gateway 已授权获取 → Git 完整性校验 → 不可变 Run pin → Event/Audit → 读取时来源状态重查。任何一步缺失都只能返回不可用或等待补齐，不能把模型输出或调用者传入的 hash 当作来源证明。

本门要求：

- PostgreSQL 是 pin 和事件的事务事实源；SQLite 只用于本地行为回归；
- 不把 Connector ID、branch、tag、latest、供应商目录响应或 Code Graph 元数据当作 commit/tree 证明；
- Event/Error/OpenAPI 合同与生产者清单保持精确一致；
- 历史 pin 可审计但不延长来源授权，撤销后 inspection 必须标记不可用；
- ADR0103 加入当前身份/Policy 重载、L2 显式允许、Connector 管理锁、事务 SAVEPOINT 和锁后 Run 状态刷新；仓库 ACL 拒绝时 inspection 也必须标记不可用；
- 任何公开摘要都不返回源码、凭据、endpoint、配置、实际 scopes 或供应商响应内容。

已通过本地契约、API、Git 完整性和一次性 PostgreSQL 迁移验证。ADR0103 另有真实本地 PostgreSQL 的 12 请求并发绑定及两类锁等待撤销证据。真实云效身份、来源传输、沙箱隔离、Artifact ACL、Harness 接线、完整多 Worker 恢复、钉钉 UAT 和 Phase 99 晋级仍是开放门禁。
