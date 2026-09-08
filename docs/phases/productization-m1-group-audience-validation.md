# M1 群受众授权与安全回复验证

日期：2026-09-07。状态：实现切片通过；M1 和 Phase 99 仍未完成。

本切片新增持久 `im_group_audiences` 表、管理员管理接口、群目标 Outbox 字段和官方群消息传输。群授权绑定安装、
`openConversationId`、Workspace、活动成员快照和五分钟验证窗口；处理、入队、发送、回执查询都会重新检查这些输入。
答案按 Artifact 分级判断，超出群上限时只允许固定无敏感状态。未配置或撤权的群不会自动发布私有答案。

验证结果：

- IM/Outbox/传输回归：**101 passed**。
- 群受众 API 与安全边界专项：**2 passed**。
- PostgreSQL 17.11 独立迁移往返、`alembic check` 和新群字段/约束检查：**1 passed**。
- Ruff、mypy、Python 编译和 `git diff --check`：通过。群受众验证中的 SQLite 时间比较已统一通过
  `ensure_utc(...)` 处理，避免生产 PostgreSQL/本地 SQLite 返回值在重查受众时发生 naive/aware 混用。

限制：测试使用 SQLite、合成 Workspace、MockTransport 和本地凭据占位；没有向真实人员或群发送消息，也没有验证钉钉
实际群成员查询、应用 scope、跨 Worker PostgreSQL 并发、真实回执或 UAT。群授权记录因此不能解释为生产群已授权。
