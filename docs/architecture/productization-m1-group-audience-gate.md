# M1 群受众架构门

状态：实现切片通过，生产门保持关闭。

群消息的发送资格必须同时满足安装 ACTIVE、发件人绑定 ACTIVE、受众 ACTIVE 且未过期、成员指纹一致、Workspace ACL
仍有效、Run 完成、答案分级在受众上限内，以及群 Capability binding、Policy、Connector grant、凭据和限流均允许。
任一条件无法确认时，Outbox 进入 BLOCKED 或保留原有 UNKNOWN；不会发送私有答案，也不会通过重试扩大受众。

本门只证明控制面和固定协议的本地实现。真实群成员同步、真实钉钉 scope、生产网络、PostgreSQL 多 Worker、回执证据、
四类意图白名单、UAT 和安全审批仍是独立门禁；Phase 99 继续阻塞。
