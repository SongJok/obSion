# 云效 Codeup 验证映射架构门

状态：本地映射切片通过；生产门保持关闭。

## 必须保持的不变量

1. 目录发现和映射都只能通过 Capability Gateway；管理 REST 不直连云效。
2. 每次映射都重新经过当前主体、Policy、`connectors.write`、Connector/SecretReference、仓库 ACL 和租户状态检查。
3. 目录条目必须同时匹配供应商数字 ID、命名空间路径和未归档状态；客户端提交的数据不是信任根。
4. 配置更新与不可变 Connector 版本在同一来源管理事务中提交；重放幂等，供应商 ID 冲突拒绝。
5. 映射不自动创建 Workspace 来源、不扩大 Agent grant、不读取源码、不启动沙箱、不调用云效写 API。
6. 公开响应和审计 metadata 不包含端点、完整配置、凭据引用、令牌、实际 scopes 或源码。

## 当前证据

专项 Codeup 网关测试覆盖成功、幂等、冲突、目录漂移、Policy/权限拒绝和配置边界；OpenAPI 与静态错误生产者清单保持精确一致。来源账本和既有四类 Codeup 读取回归继续作为必要门禁。

## 不得从本门推导的结论

本门不证明真实云效身份、实际 scopes、目标仓库内容、PostgreSQL 多 Worker 事务线性化、来源 commit/tree pin、沙箱传输、钉钉正式租户、UAT 或 Phase 99 晋级。生产 Connector 管理仍按当前环境边界关闭或由外部审批控制。
