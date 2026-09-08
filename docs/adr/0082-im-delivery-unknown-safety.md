# ADR 0082：IM 投递不确定结果与一次发送资格

日期：2026-09-06

状态：接受，限定于仓库内兼容投递链的安全收紧，不代表 M1 或生产验收完成。

## 背景

数据库事务无法与厂商发送原子提交。旧 Bridge 将断连写为 FAILED，prepare 将所有非 SENT 记录重置 PENDING；钉钉 POST 自动重试及本地 ID 冒充厂商回执，使不确定结果可能产生重复发送。

## 决策

- 首次 prepare 在同一事务中锁定 Run、校验 Policy、建立唯一 Delivery，提交后只授予当前请求一次发送资格。PostgreSQL 行锁与 SQLite 写锁保证并发调用不能同时取得资格。
- 已有 PENDING、FAILED、UNKNOWN 均拒绝再次 prepare。PENDING 表示发送资格已经占有，不是可重复领取的队列状态；进程在发送前退出也不会自动恢复资格，优先保证不重复，而非保证必达。
- 保留旧 `/fail` 路径与字段，但其语义收紧为 UNKNOWN，因为旧请求没有证明厂商未接收。只能由原请求主体报告；SENT 不会被失败报告降低。
- `/complete` 仅接受原请求主体提供的非空厂商回执，拒绝以本地 Delivery ID 当作回执。同回执幂等，不同回执冲突；UNKNOWN 可凭真实回执收敛为 SENT。
- Bridge 只在本次 prepare 返回 PENDING 时发送。UNKNOWN、FAILED、缺失或未知状态均拒绝发送；厂商成功而 complete 失败时尽力报告 UNKNOWN，若数据库也不可达则保留 PENDING 的占有状态，不重新发送。
- 钉钉 `chat/send` 不携带厂商认可的幂等键，故所有 POST 只请求一次；只读认证 GET 保留有界重试。没有厂商回执时返回不确定错误，不生成伪回执。
- 新迁移将既有 PENDING/FAILED 转为 UNKNOWN，保留真实 SENT。状态字段既有长度为 32，无需扩容。存在任一非 SENT（PENDING、FAILED、UNKNOWN）记录时禁止 downgrade，避免升级后新产生的发送占有记录被旧版重新发送。

## 边界与代价

这不是完整 Outbox、自动对账或群受众授权。未知结果可能需要人工获取厂商回执；没有“确认未发生后重发”入口。主事务回滚、ACK 丢失和厂商事件重投仍需独立 Inbox 闭环。旧版 API 与厂商适配器不得据此宣称 M1 正式上线。

全局发布时必须先停止旧发送 Worker，再迁移并切换控制面与适配器，不能让旧客户端继续持有发送资格。受众授权及 CredentialBroker/Gateway 路由属于后续独立实现，不因本 ADR 获得权限例外。

## 验证

本地测试覆盖：并发 prepare 只有一个成功、PENDING/UNKNOWN 禁止重发、真实回执幂等与冲突、不同主体拒绝报告、complete 丢失后的 UNKNOWN、POST 超时/5xx/无回执只请求一次。测试使用 SQLite 与 HTTP mock；真实 PostgreSQL 迁移和真实厂商回执另行验收，不将 mock 计作外部效果证据。
