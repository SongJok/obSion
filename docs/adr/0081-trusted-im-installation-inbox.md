# ADR 0081：可信 IM 安装与事务性 Inbox 接收

日期：2026-09-06

状态：接受，限定于 M1 控制面最小增量；不是完整 M1 验收。

## 背景

旧 IM 身份绑定以组织与 provider 标识 sender，每次调用新建 Turn，不能证明厂商应用安装身份或持久事件接收。M1 要求绑定安装的适配器、持久 ACK、重放和授权重查，不能增加第二套 Harness。

## 决策

- 管理员必须具备当前 `identity.write` 权限并获得 Policy ALLOW，才能创建 `ImInstallation`。全局唯一 `(provider, external_corp_id, external_app_id)` 标识安装；组织只来自管理员身份。adapter User 与 ACTIVE Connector 必须属于同一组织，保留创建者及有界验证来源引用。
- Connector 只接受控制面已支持的对应类型 `dingtalk-docs`、`feishu-docs`、`wecom-docs`，接收及处理时再次检查。`dingtalk-http` 等适配器传输标签不是控制面 Connector 类型。此检查只证明供应商类型和 ACTIVE 状态，不证明真实 scopes、环境适用性或出站 grants；安装依据是操作方配置证明，不伪造 OAuth 或签名验证证据。
- 入站身份必须是安装固定的、当前有效的 adapter User，同时具备 `im.delegate` 并获得 Policy ALLOW。安装路径只是选择器，不授予权限；body 不能选择组织、主体、角色、指纹或 provider。适配器必须将已验证连接上下文绑定到固定安装；控制面 API 本身不验证厂商 SDK envelope。
- 新安装域 sender 绑定不回退到旧绑定。旧 API 与数据保留兼容，但不构成可信安装入口；管理员须显式配置新绑定，不猜测回填。
- `TrustedImInbound` 只包含事件 ID、稳定 sender、完整 conversation ID/type 和文本。服务端在脱敏前对规范化输入计算 SHA-256 指纹，Inbox 仅保存脱敏文本及最小路由标识。
- PostgreSQL 与 SQLite 均对 `(installation_id, vendor_event_id)` 建立唯一约束。同内容重放返回同一持久回执，异内容以 `idempotency_key_reused` 返回 HTTP 409；无正文冲突审计先提交再拒绝。接收记录固定 binding 与 subject，绑定替换不能重新解释已接收任务。
- POST receive 提交后返回 HTTP 202；不创建 Turn、不等待 Harness、不发送消息，也不声称实测 ACK ≤1 秒。GET 查询及有界 RECEIVED/PROCESSED 列表使用同一 adapter 授权；按 UUID 升序 `after` keyset 分页，避免成功处理导致 offset 跳过或失败队首阻塞。
- POST process 重查安装、当前 adapter/Connector、binding、subject，并由 Policy 检查 subject 的 `workspace.write`。通过既有 `WorkspaceService` 创建 Workspace → Thread → Turn → Run。
- 显式 `ImConversationBinding` 以安装、subject、稳定 sender/type/完整 conversation ID 的摘要唯一标识会话，FK 固定 Workspace 与 Thread；name/title 只是显示标签，不参与身份查找。每个会话使用独立私有 Workspace 与可复用 Thread；复用前检查当前组织、owner、PRIVATE 和无其他成员，已共享则返回 403，不再写入 IM 数据。归档后创建新历史并事务更新映射，不授予 adapter Workspace 成员权限。
- 安装数据库写锁协调 receive/process、安装撤销与绑定管理。SQLite 使用 no-op UPDATE 写锁，不假定 SELECT FOR UPDATE 生效。Turn/Run 创建、唯一关联、审计和 PROCESSED 转换在同一事务；PROCESSING 仅为事务内状态，不是已提交的 worker 租约。提交前失败整体回滚为 RECEIVED，提交后重放返回既有任务；process 不产生外部效果。
- 新 Turn 只携带 `im_inbox`，不携带旧 `im_delivery` context。群成员或已绑定 sender 身份不授予私有答案发布权限；Outbox、群受众、UNKNOWN 对账与受治理厂商投递属于独立契约。

## 迁移与限制

前向迁移 `a81b92c03d14` 基于 `f3d4e5a6b7c8`，创建四张独立新表及索引；导入既有 DB models 时注册元数据，不修改旧绑定。任一新账本表非空时拒绝 downgrade，PostgreSQL 在检查前锁定相关表，不能静默删除重放状态。

安装级串行化优先保证最小可证明事务边界，而非吞吐。此 ADR 不声称已实现分布式租约、Stream 生命周期、Outbox、生产延迟或真实租户投递。人员、角色、Policy 与 Connector 更新在处理授权点重新评估，不等于所有既有管理 API 均采用全局可串行化事务。

## 验证

`services/control-plane/tests/test_im_inbox.py` 覆盖持久回执可见性、100 并发接收、并发处理、重启重放、冲突审计、严格输入、安装/adapter/租户隔离、撤销及绑定替换、事务回滚、列表恢复和多轮隔离，以及同名预创建、重命名、共享拒绝和 provider 不匹配。

`tests/integration/test_postgres_im_inbox.py` 在显式启用的一次性 PostgreSQL 中验证 100 并发 receive/process、连接池重建后重放及 SQL 唯一约束。实际结果与未验收边界记录在 M1 验证报告中，不能由本 ADR 推导 M1 完成。
