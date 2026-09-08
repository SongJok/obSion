# ADR 0100：云效目录到本地仓库的验证映射

- 日期：2026-09-08
- 状态：本地产品化切片已实现；真实云效目标仓库、生产环境和 M2 仍未验收
- 关联：[ADR 0094](0094-native-codeup-read-gateway.md)、[ADR 0095](0095-codeup-operator-discovery.md)、[ADR 0091](0091-project-source-version-ledger.md)、[ADR 0093](0093-governed-project-source-rest.md)

## 问题

目录发现可以返回云效仓库数字 ID 和命名空间路径，但此前管理员仍需手工把这些数据复制到来源配置。
手工复制容易把旧目录项、错误租户或错误本地仓库写入配置，也没有把目录读取和来源账本连接成一个可审计的产品流程。

## 决策

新增受保护管理入口：

`POST /api/v1/admin/codeup/connectors/{connector_id}/mappings`

请求只接受 `catalog_connector_id`、本地 `repository_id`、`provider_repository_id` 和 `provider_path`。
入口先通过 `codeup.repositories.discover` 的 Capability Gateway 重新读取目录，并要求同一条目同时匹配供应商数字 ID、完整路径和未归档状态。
调用者不能仅凭浏览器或旧响应伪造供应商标识。目录结果不写入响应之外的秘密、成员、URL 或源码。

目录校验成功后，入口在独立的 Policy/Audit 来源管理事务中执行 `project_source.codeup.map`：

- 重新加载当前主体、组织、`connectors.write` 权限、Connector、配置、SecretReference、本地仓库 ACL 和租户状态；
- 只允许 `codeup` 只读 Connector，固定 Central HTTPS origin、`codeup.read.v1`、`code.read` 和受限仓库映射；
- 同一供应商仓库 ID 不得绑定到另一个本地仓库；同一映射重放返回 `UNCHANGED`；
- 新映射原子更新 Connector 配置并创建不可变 `ConnectorConfigurationVersion`；
- 每次允许、拒绝、无效或冲突都保留 PolicyDecision/Audit 证据；响应只返回本地 UUID、供应商 ID、版本 UUID、结果和 Policy decision UUID；
- 不创建 Workspace 来源绑定，不扩展仓库 ACL，不授予 Agent 能力，不执行源码读取或沙箱操作。

已有映射版本继续通过来源账本和显式 `register_source` 绑定到 Workspace/本地仓库；Codeup 读取仍必须经过 Gateway、Policy、凭据、仓库 ACL 和运行时配置重查。

## 安全边界与兼容性

映射管理只在 `test`、`development`、`staging` 环境开放，生产入口继续关闭。HTTPS 默认端口 443 与显式 `:443` 视为同一固定出口，其他端口、主机、协议、路径、查询串、凭据和内联令牌均拒绝。
目录 Connector 与源码 Connector 仍是两个独立配置，目录读取不自动启用源码读取。

实现复用现有项目来源账本表，不增加迁移，不改变旧四种 Codeup 读取能力或旧 API 请求模型。配置快照经过 JSON 冻结和秘密扫描；供应商 ID、组织 ID、endpoint、凭据引用和完整配置不进入公开响应或审计 metadata。

## 验证

专项测试覆盖精确目录匹配、成功创建、不可变版本、幂等重放、重复供应商 ID 冲突、目录不匹配前置拒绝、Policy/`connectors.write` 拒绝、配置端口规范化和秘密/配置不回显。
OpenAPI、错误生产者、Ruff、mypy 与来源管理回归必须保持通过。真实云效身份、实际 scopes、目标仓库源码读取、PostgreSQL 多 Worker、沙箱交付和生产晋级仍需要外部运维证据。
