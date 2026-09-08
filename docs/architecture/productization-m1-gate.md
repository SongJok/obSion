# M1 正式钉钉入口架构门

日期：2026-09-06

状态：进行中；不能据此晋级 Phase 99 或宣称 M1 验收通过。

## 需求与不变量

依据 `goal.txt`、`second_goal.txt` 和 [首版产品化实施映射](../product/productization-plan.md)，所有入口共用 Python 控制面与 PostgreSQL 事实源。Stream 只负责可信连接生命周期、规范化消息、持久接收后的 ACK，不拥有模型、会话字典或 Harness。

- 安装归属由管理面配置，入站主体必须是该安装绑定的 adapter Principal；payload 不能选择组织或提升权限。
- 安装域用户绑定与旧 `channel + sender` 绑定分离，不能猜测迁移或回退匹配。
- Inbox 使用稳定厂商事件 ID 与服务端内容指纹；同键同内容重放，同键异内容拒绝。
- 接收事务提交后才能返回 ACK；任务处理与接收解耦，处理前重查安装、用户、Connector 和 Policy。
- Inbox 与唯一 Turn/Run 关联在同一事务提交；崩溃后从持久待处理记录恢复，不依赖内存任务队列。
- 每个用户的安装域会话隔离；私有结果不因来自群消息而获得发布到群的权限。
- 旧 Delivery 的发送资格只授予一次；发送后缺少回执属于不确定结果，不得自动重复 POST。
- 回执必须来自厂商，不能用本地 Delivery ID 代替；未知结果优先停止与对账，而不是伪造成功。

具体契约见 [ADR 0081](../adr/0081-trusted-im-installation-inbox.md)（安装与 Inbox）、[ADR 0082](../adr/0082-im-delivery-unknown-safety.md)（投递安全收紧）和 [ADR 0083](../adr/0083-dingtalk-stream-durable-ack.md)（Stream 与持久 ACK）。实现已集成，实际测试结果与未满足项见 [验证记录](../phases/productization-m1-validation.md)，不以 ADR 引用代替验收证据。

2026-09-07：[ADR 0096](../adr/0096-dingtalk-durable-robot-outbox.md)的内部新版协议已接入持久 Outbox、Gateway、可选 worker、管理查询和 Connector activation。专项 98 项通过，包含前向迁移模型、单次 claim/fencing、UNKNOWN、环境引用和激活失败闭环；一次性 PostgreSQL 17.11 upgrade/check 通过。
随后隔离全量 Python 回归 **2012 passed、213 skipped、6 deselected（390.36 秒）**；这不改变真实 PostgreSQL 并发/重启、厂商回执、群受众授权和真实租户 UAT 的未满足状态。
这只证明控制面实现切片，不证明真实 PostgreSQL 并发、厂商回执、群受众授权或真实租户 UAT；不能据此关闭第 8—9 项验收。

2026-09-07：[ADR 0097](../adr/0097-dingtalk-receipt-reconciliation.md)补齐了 `processQueryKey` 的只读回执查询、持久状态字段、worker 调度和指定 Outbox 的管理员对账入口。对账 claim 先提交次数/时间，再执行 GET；SUCCESS 只保存厂商状态，FAILURE 转 REJECTED，UNKNOWN 保留 ACCEPTED 且禁止重发。SQLite 状态机、错误/事件契约与 OpenAPI 已验证；一次性 PostgreSQL 17.11 空库迁移往返 **1 passed**，新增字段、约束和索引断言通过。
该切片仍不证明厂商真实回执、群受众或 UAT；PostgreSQL 并发/重启与真实租户证据缺失，M1/Phase 99 继续保持未通过。

## 必需验证

1. 安装、Adapter、人员绑定、Connector、组织与项目边界的正反向测试。
2. 同事件 100 次并发接收及处理只生成一个 Inbox 和一个逻辑任务；新的数据库会话重放返回相同关联。
3. 同键不同内容拒绝，拒绝过程不污染已有记录。
4. 接收成功后撤销安装或用户权限，处理不得继续；不同安装不能读、处理对方 Inbox。
5. Stream 成功 ACK 不等待模型或最终投递；持久接收失败不能回成功 ACK。
6. Worker 崩溃与重启后可处理 RECEIVED；一个不可处理事件不能无限阻塞其他事件。
7. PostgreSQL 前向迁移与模型漂移检查通过；历史 SENT 回执保留，不确定账本降级不可变成可重发状态。
8. 厂商接受后断连、HTTP 5xx、回执缺失、内部 complete 响应丢失均不触发盲重发。
9. 真实测试租户验证持久 ACK、单聊问答、白名单创建、群受众控制和真实回执；mock 不能替代。

## 本切面明确不覆盖

完整租户级自动对账证据、完整受治理出站 Capability、群受众成员授权、四意图白名单创建、根目录旧原型全面退役、真实租户 UAT 与响应 SLO，均须另行实现和验收。新增持久接收入口在这些能力完善前不能被描述为完整可用机器人。

沙箱、自主项目处理、持续学习、企业 SSO/RLS、容量和 GA 分别属于 M2—M6；当前门的通过与否不能推导这些里程碑完成。
