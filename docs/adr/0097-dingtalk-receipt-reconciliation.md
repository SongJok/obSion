# ADR 0097：钉钉机器人只读回执对账

日期：2026-09-07。状态：Accepted for implementation；真实租户回执仍未验收。

## 决策

已被钉钉受理的 Outbox 记录保存厂商 `processQueryKey`，后续状态确认使用官方只读接口
`GET /v1.0/robot/oToMessages/readStatus`。对账是已有
`im.dingtalk.robot.reply` 能力的受治理只读子路径，继续经过 Capability Gateway、Connector
指纹、Policy Engine、grant、限流、CredentialBroker 和审计；不创建第二个发送能力，也不把
厂商密钥交给 Agent、worker 或传输层之外的调用者。

## 状态机与恢复

1. worker 或管理员先在独立事务中 claim 一个 `ACCEPTED` Outbox，并提交递增的
   `reconciliation_attempt_count` 与 `last_reconciled_at`。
2. claim 提交后才执行外部 GET。对账事务再次校验 Outbox 状态、claim 次数、安装身份、人员绑定、
   Run 完成状态、Connector 指纹和当前主体访问权；任何变化都 fail closed，不访问厂商。
3. `SUCCESS` 只保存 `vendor_send_status`、可选已读状态和时间；`FAILURE` 转为 `REJECTED`。
   `UNKNOWN` 保留 `ACCEPTED`，记录回执冲突错误，不触发任何 POST 或自动重发。
4. 超过有界对账次数后不再由 worker 自动查询；管理员可通过
   `POST /api/v1/admin/im-dingtalk/outbox/{outbox_id}/reconcile` 发起一次同样受治理的只读查询。

查询响应只保存有限状态字段，不保存正文、凭据或原始厂商响应。无效主体、异常时间戳、未知
厂商状态、限流、凭据不可用和传输失败都按 UNKNOWN 处理。手动入口只能访问本组织的 Outbox，
并要求 `admin.read`。

## 取舍

查询暂复用发送能力的版本、绑定和 grant，避免在已有管理员 activation、Capability Registry
和 Connector 迁移之外扩散一套权限。该能力的 `WRITE` side effect 描述的是发送能力本身；
对账路径在传输边界保证只执行 GET，不能据此推导发送权限或已读事实。若未来厂商将回执查询
拆成独立 scope，再以新的 capability 版本和 ADR 收敛权限。

## 验收边界

SQLite 专项覆盖 claim 持久化、成功/失败/UNKNOWN、旧 claim/fencing、权限或 Connector 变化
闭锁和时间转换；迁移测试覆盖新增字段、约束和索引。MockTransport 只验证协议与状态机，不能
替代真实钉钉租户的回执、群受众或 UAT 证据，因此 M1 和 Phase 99 仍保持未通过。
