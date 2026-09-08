# ADR 0096：正式钉钉机器人的持久交付链

日期：2026-09-07。状态：Accepted for implementation；内部出站链已接通，真实租户验收仍未完成。

## 决策与实施顺序

正式 Stream Inbox 产生的私有任务结果需要由控制面交付。旧 `/chat/send` 适配器继续兼容旧入口，
不能把新版 `processQueryKey` 当作旧会话消息 ID，也不能用它证明用户已读。

1. 在现有 Python 控制面实现内部新版机器人协议叶节点：固定 HTTPS、服务端令牌、一次发送一个安装域用户，
   返回 NOT_ATTEMPTED / ACCEPTED / REJECTED / UNKNOWN。无自动消息 POST 重试，无任意 URL、请求头或批量受众。
   `ACCEPTED` 只表示厂商返回处理键且目标不在无效/限流名单，不表示最终送达或已读。
2. 增加长期 Outbox 与发送尝试事实：以安装、Inbox、Run、收件人、答案及版本指纹绑定唯一逻辑交付。
   QUEUED 可以重新领取；在外部 I/O 前提交 DISPATCHING 与 fencing token，超时转 UNKNOWN。
   DISPATCHING/UNKNOWN/ACCEPTED 不因租约或通用幂等保留期到期重新发送。
3. worker 只能调用专用 Capability Gateway 路径。发送前重查安装、Adapter、人员绑定、主体权限、Workspace/内容 ACL、
   Connector 冻结配置及 Policy；Gateway 解析凭据，模型与 adapter 不接触出站密钥。
4. 群入站的私有答案不能发回群。群受众须有独立成员与源码受众授权；首个接线验收路径为单聊。
   用户要求的创建/总结/分析/查询意图白名单仍须独立实现，不能靠发送协议推导满足。
5. 回执或对账只更新已有尝试，不触发重新发送；未知结果保留人工处理提示。已读状态使用独立厂商查询契约。
6. 管理 API / 工作台呈现排队、受理、未知、拒绝、待补齐和审计关联。不得把本地状态或返回 200 当完整机器人验收。

通用 OperatorCapabilityInvocation 会按保留期删除终态记录，因此不能单独承担长期消息去重。
Outbox 表已通过前向迁移加入同租户关联和终态保护；真实 PostgreSQL 并发与重启验证仍是后续验收项。
当前实现已完成第 1—3 项的控制面切片，并由 [ADR 0097](0097-dingtalk-receipt-reconciliation.md) 补齐第 5 项的只读回执对账切片：新增前向迁移、长期 Outbox、fencing claim、UNKNOWN 账本、Gateway 调用、可选 worker、管理查询、管理员激活检查、`processQueryKey` 状态查询和指定 Outbox 对账入口。第 4 项群受众授权与真实租户验收、第 6 项完整产品化闭环仍未完成，不能把本切片声明为 M1 完成。生产保持关闭，旧入口暂不改线。

环境配置约束：`app_key_env`、`robot_code_env` 只能引用 `OBSION_*` 环境变量；机器人 secret 继续只通过 `CredentialBroker` 的 `credential_ref` 解析。管理员必须调用 connector activation API 完成协议、固定 egress、grant、身份环境变量和凭据可用性检查；activation 不会自动创建 Capability binding。

## 厂商依据

[官方 BatchSendOTO 文档](https://open.dingtalk.com/document/orgapp/chatbots-send-one-on-one-chat-messages-in-batches)与
[官方 SDK v2.0.83](https://github.com/alibabacloud-go/dingtalk/blob/v2.0.83/robot_1_0/client.go)核对固定发送路径、
robotCode/userIds/msgKey/msgParam、processQueryKey、invalidStaffIdList、flowControlledStaffIdList。
SDK 仅用于协议核对，不引入 Go 后端或运行依赖。实现仅发送 `sampleText`，使用 Obsion 自身 4096 UTF-8 字节预算。

验收应覆盖发送前失败、厂商受理、无效/限流用户、断连/5xx/缺失处理键、压缩与超大响应、凭据反射、
取消、不重试、重启与多 worker 竞争；真实发送必须有明确测试接收人及消息授权。MockTransport 单列，不冒充真实租户结果。
