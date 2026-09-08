# 正式钉钉产品入口：M1 契约准备

日期：2026-09-05

状态：契约与实现集成进行中；不替代 M0 或真实租户验收。

2026-09-06 更新：安装域与 Inbox、Stream 持久接收适配和恢复 worker 已进入主工作区；投递 UNKNOWN、单次发送资格与真实回执校验已集成。会话映射隔离修复、PostgreSQL 验证与最终回归仍在进行。实现决策见 ADR 0081、0082、0083，准确证据见 [M1 验证记录](../phases/productization-m1-validation.md)。下述“已确认缺口”保留初始设计背景，不代表修复后代码仍维持旧行为。

## 可复用基础

- `apps/im-adapter/src/obsion_im/channel.py`：入站/出站消息及 Channel 契约。
- `services/control-plane/src/obsion/application/im_identity.py`：sender 到真实 User/Principal 的绑定及正式任务创建。
- `services/control-plane/src/obsion/application/im_delivery.py`：Run 关联投递准备、内容指纹、策略和回执记录。
- `services/control-plane/src/obsion/persistence/app_server_requests.py`、`operator_invocations.py`：持久幂等、内容冲突和 UNKNOWN 对账模式。
- `services/control-plane/src/obsion/actions/worker.py`：PostgreSQL 租约与 SKIP LOCKED 领取模式；只复用机制，不继承动作授权。

## 已确认缺口

当前正式 IM 契约没有 installation、厂商事件 ID 或持久 Inbox；组织由适配器主体决定，尚未根据经验证的 corp/app 安装解析。sender 绑定不区分同一组织的不同安装。Webhook 同步等待任务和投递，不能证明持久 ACK ≤1 秒。

当前 Delivery 只有 PENDING/SENT/FAILED，不能表达可能已发生的外部效果。DingTalk 客户端在 POST 传输失败或部分 HTTP 错误后重试；厂商响应缺少 messageId 时回退为本地幂等键。该键不是厂商回执，也没有作为厂商幂等保证发送。此问题须连同持久 UNKNOWN、重试和对账语义修复，不能仅把异常改为 FAILED。现有 Bridge 会把发送异常写为 FAILED，而 prepare 会把非 SENT 再变为 PENDING；Webhook 的失败响应还可能触发厂商重投并创建新 Run。厂商已返回真实回执但内部 complete 调用失败也留下重复窗口。因此，单独去掉客户端重试不等于端到端去重，必须同时落实 Inbox 和不可盲重试的投递状态机。

当前适配器执行厂商 HTTP，不等于已经通过 Capability Gateway 执行。`im.reply.deliver` 的策略检查不自动构成版本化 Capability、Connector grants、CredentialBroker、统一限流与执行审计链。不能通过移除普通 Agent 只读门禁解决。

当前私有用户工作空间也不证明结果可以发到群；需独立群/项目绑定和发送前受众授权。

## 最小纵向增量

1. 建立 `ImInstallation`：provider、外部 corp/app 唯一键、organization、受管连接器引用、ACTIVE/REVOKED、管理员与验证来源。安装身份不得由普通入站 payload 随意指定。
2. 建立 `ImInboxMessage`：installation + vendor_event_id 唯一约束、服务端 canonical fingerprint、最小规范化输入、主体/会话、状态、lease 与 Turn/Run 关联。
3. 定义 Stream 与 HTTP 共用的 `TrustedImInbound` 应用契约；适配器只处理 SDK 生命周期、可信连接上下文和 envelope 翻译，不拥有模型或 Harness。
4. 先验证安装并持久化 Inbox，再 ACK；同键同内容重放，同键不同内容拒绝并审计。ACK 仅表示持久接收，不表示任务完成。
5. Worker 在当前授权下取得安装域 sender 绑定，调用现有应用服务形成唯一逻辑任务。任务关联及去重必须事务性持久化，不能依赖进程内字典。
6. 旧 vendor binding 缺安装信息时进入待补全状态，不猜测 backfill；兼容开发入口不得成为正式 vendor ingress 的绕过路径。
7. 在 Outbox 与受众授权未完成前，群结果不得直接发送敏感最终答案；仅允许经授权的不含敏感信息状态或登录鉴权链接。

## 出站后续契约

在原 Delivery 上定义 attempt、租约、next_attempt_at、installation/audience 关联，以及独立的 UNKNOWN/RECONCILING。厂商 message ID 可缺失，不能伪造；不确定请求先对账，不能重新 POST。取消、撤权、群成员变化和失败恢复都须在发送前重新检查。

需要定义 Run 关联的受治理 service-delivery invocation，固定 Capability/Connector 版本、Policy decision、内容/受众指纹和真实回执。机器人不能获取应用管理凭据。

## 必须单独决策与验证

需要 ADR 明确可信安装身份、旧绑定迁移、Inbox 幂等及 ACK、服务投递权限例外、UNKNOWN 与群受众发布。随后才实施前向迁移和兼容字段，不以本文当作 ADR 已批准。

验收包括并发 100 次重复事件和重启重放、内容冲突、跨安装/跨租户、撤权、群成员变化、Worker 失效、厂商接受但响应丢失和限流。真实测试租户的问答、白名单创建及回执证据仍为独立门禁。

本文保留初始契约背景；2026-09-06 实现已配套新增迁移，具体以 ADR 0081—0083 和 M1 验证记录为准，不能再将本轮增量视为无需迁移的纯文档变更，也不是 M1 完成证据。
