# ADR 0102：Run 的项目来源版本固定

- 日期：2026-09-08
- 状态：本地切片已实现；真实来源获取、沙箱传输和生产晋级仍未验收
- 关联：[ADR 0090](0090-git-project-content-integrity.md)、[ADR 0091](0091-project-source-version-ledger.md)、[M2 架构门](../architecture/productization-m2-gate.md)

## 问题

项目来源账本只能说明 Workspace 允许使用哪个仓库和 Connector 配置版本。一次 Run 实际使用的 commit、tree 和文件快照仍可能只存在于调用者内存中，无法在重放、审计或来源撤权后判断当时使用的精确内容。

## 决策

新增不可变 `run_source_pins` 事实表，并提供内部 `RunSourcePinService.pin_verified_snapshot` 边界。调用者必须先通过 Capability Gateway 获取来源，再提交已验证的 `ProjectSnapshot`、原始 Git commit 对象和显式运行环境。服务在同一事务中重新检查：

- Run/Workspace 写访问、组织边界和 Run 是否仍可写；
- ProjectSource、Connector 配置版本、仓库 ACL、撤销账本和配置漂移；
- SHA-1/SHA-256 commit/tree 与完整文件快照的精确匹配；
- 相同 `(Run, source)` 的幂等重放与不同版本冲突。

成功创建会写 `run.source_pinned.v1`、Audit 和不可变 pin。pin 只记录来源版本摘要、commit/tree、快照指纹和大小；不记录源码、endpoint、配置、凭据或供应商权限声明。Run 终态禁止新增 pin。

Run inspection 暴露只读 `GET /api/v1/runs/{run_id}/source-pins`。读取历史 pin 时再次检查当前来源状态；来源撤销或数据库摘要异常只把 `source_available` 置为 `false`，不删除历史事实，也不将历史 pin 当作当前授权。

## 兼容性与边界

该切片不改变已有来源管理 API、Codeup 读取协议或 Harness 调度；尚未自动从云效获取源码，也未把 pin 服务接入 Harness 执行、Artifact lineage 或沙箱传输。Agent 不接收 Connector 凭据。实际来源 ACL、厂商 scopes、网络 fencing、持久沙箱恢复和最终发送仍必须在各自 Gateway/Policy 边界内重查。

## 验证

SQLite 合成服务测试覆盖 Git 完整性、幂等、撤销拒绝和 REST inspection。独立 PostgreSQL 17 空库验证覆盖基线到 head 的迁移往返、重复空账本降级/重升、版本事实与绑定撤销的非空降级保护。契约门禁固定 97 个 Event 版本和 336 个错误码；真实云效、钉钉租户、生产多 Worker 和 UAT 仍未提供证据。
