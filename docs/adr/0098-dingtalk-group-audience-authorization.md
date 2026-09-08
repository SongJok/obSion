# ADR 0098：钉钉群受众授权与安全回复

日期：2026-09-07。状态：Accepted for implementation；真实租户成员同步和 UAT 仍未完成。

## 决策

钉钉群消息与单聊保持同一 Python 控制面、Inbox、Harness、Capability Gateway、Policy 和 Outbox，
但群是独立的受众边界。来自群的发件人绑定只证明“谁发起了任务”，不能证明答案可以发布给全群。

1. 管理员为安装和 `openConversationId` 创建或更新持久 `ImGroupAudience`，绑定一个 Workspace、活动成员快照、
   成员指纹、答案最高分级、是否允许最终答案和是否允许安全状态。创建时重新检查组织、安装、活动用户和 Workspace 写权限。
2. 授权记录默认只有五分钟验证窗口；过期、撤销、安装撤销、用户停用、Workspace ACL 变化或成员指纹变化均 fail closed。
   本切片没有凭空调用厂商成员 API；`verification_source` 记录外部核验来源，后续同步器必须通过同一管理服务更新快照。
3. 群消息处理时重新检查发件人是否在快照中、快照成员是否仍有 Workspace 访问权，并为每个发件人建立独立线程。
   未配置或无法确认受众时，消息仍可形成发件人的私有任务，答案不会自动进入群 Outbox。
4. 群 Outbox 固定保存 `audience_id`、成员指纹、会话 ID、原始答案分级和 `FINAL`/`STATUS` 模式。入队、发送、
   对账前都重查安装、成员、Workspace ACL、Run、答案分级、受众指纹和 Connector。群最终答案只有在 `allow_final_answer`
   且分级不高于受众上限时发送；否则只允许无敏感状态文本，不能把私有答案降级伪装成群答案。
5. 群发送使用官方 `POST /v1.0/robot/groupMessages/send`，固定 `robotCode`、`openConversationId`、`sampleText`，
   复用单次发送、fencing、UNKNOWN 和只读 `readStatus` 对账语义。任何不确定外部结果都不重新 POST。
6. 管理 API 的写入和撤销继续经过现有 Policy 与 Audit；Gateway 的发送和对账继续经过 Capability 版本、Connector grant、
   限流、CredentialBroker 和审计。成员 ID、指纹和答案内容不进入普通 Agent 凭据或日志。

## 取舍与边界

成员快照并不等同于实时厂商群成员列表，因此真实群上线前必须提供可靠的钉钉成员查询/同步证据。状态文本只说明结果需在
Obsion 工作台登录查看，不包含答案、文档内容或凭据。当前阶段不开放群撤回、群卡片、跨群转发或群成员自动发现。

## 验证

专项测试覆盖管理员创建/列表/撤销、Workspace 与活动用户边界、无受众私有处理、固定群发送和群回执查询；一次性
PostgreSQL 17.11 迁移升级、漂移检查和回滚保护通过。MockTransport 不能替代真实钉钉成员同步、权限 scope、网络和 UAT。
