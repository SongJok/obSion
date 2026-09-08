# 云效 Codeup 验证映射产品化验证记录

日期：2026-09-08。状态：本地实现和契约验证通过；真实目标仓库、生产环境和 M2 整体验收仍未完成。

## 本次交付

目录发现结果现在可以通过受保护的映射 REST 显式固定到本地 Code Repository。映射前重新走目录 Gateway，要求数字 ID、完整路径和未归档状态完全匹配；映射事务经 `project_source.codeup.map` Policy、`connectors.write` 权限、本地仓库 ACL、SecretReference 和来源账本审计。

同一映射重放不会生成第二个版本；同一云效数字 ID 不能绑定不同本地仓库。响应不包含 endpoint、configuration、credential_ref、令牌、scope 或源码。

## 自动化证据

- `tests/test_codeup_gateway.py`：24 passed，其中 5 项覆盖目录到本地仓库映射，另覆盖目录读取、版本冻结、幂等、冲突、前置目录拒绝、权限拒绝和配置输入边界。
- Codeup Connector、来源管理和来源 REST 回归：228 passed，142 个 PostgreSQL opt-in 用例按当前环境跳过。
- 精确错误生产者与契约门禁：映射错误分支已登记，OpenAPI 快照已重新生成。
- `configuration_snapshot()` 对 Codeup 固定出口接受 HTTPS 默认 443 与显式 `:443`，拒绝非字符串列表项和未哈希输入，不产生内部 `TypeError`。

这些是本地 SQLite/合成 HTTP 证据。它们不证明真实云效令牌的 scopes、组织成员关系、目标仓库内容、PostgreSQL 跨 Worker 锁行为或生产部署。

## 运行流程

1. 管理员使用目录 Connector 查询云效仓库。
2. 管理员提交目录 Connector、云效数字 ID/路径和本地仓库 UUID。
3. 服务端通过 Gateway 重查目录，匹配成功后再提交来源映射事务。
4. 管理员在项目来源清单中确认冻结版本，再显式绑定 Workspace 来源。
5. Codeup 读取继续经现有 Gateway/Policy/ACL/凭据路径；撤权或配置变化时丢弃结果。

目录映射不会自动绑定项目、授予 Agent、获取源码、创建沙箱副本或执行云效写操作。

## 未完成门禁

真实云效租户、目标仓库四类读取和撤权场景、实际 scope 脱敏记录、PostgreSQL 多连接并发/重启、来源到 Run 的 commit/tree pin、沙箱传输、钉钉正式租户、UAT 和 Phase 99 生产晋级仍待完成。
