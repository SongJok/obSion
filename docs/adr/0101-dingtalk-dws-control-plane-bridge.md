# ADR 0101：DWS 兼容入口只转发到控制面

- 日期：2026-09-08
- 状态：本地兼容入口已实现；官方 Stream、真实租户和 M1 生产验收仍按既有门禁执行
- 关联：[M1 Stream 运维](../product/im-m1-stream-operations.md)、[ADR 0083](0083-dingtalk-stream-durable-ack.md)

## 问题

仓库历史的 `dingtalk_obsion_agent.py` 曾在进程内保存对话，并直接调用 OpenAI 兼容接口；不可用时还会生成关键词回答。
这条路径绕过统一 Principal、安装域绑定、Workspace/Thread/Run、Capability Gateway、Policy、Audit 和持久事件，不能作为企业机器人产品入口。

## 决策

保留脚本名以兼容已有 DWS `--agent-cmd` 配置，但把它收敛为无状态薄适配器：

- 只读取 DWS 提供的 sender/conversation 标识和 `OBSION_TOKEN`/控制面 URL；
- 通过 `obsion-im` 的 `ExperienceRuntime` 和 `ImBridge` 调用 `/api/v1/experience/im/messages`，让控制面创建和执行真实 Harness Run；
- stdout 只输出控制面返回的答案，stderr 只输出稳定的失败提示，不输出供应商响应、连接器配置或异常正文；
- 不导入 OpenAI 客户端，不接收模型密钥，不维护进程内历史，不提供本地关键词降级；
- sender 未绑定、权限拒绝、控制面不可用或结果不确定时失败关闭，用户应通过正式 Stream/Inbox 和管理面排查。

该入口适用于已有 DWS 开发连接的兼容场景，仍不替代官方 Stream 的持久 Inbox/ACK 入口，也不绕过正式 DingTalk Outbox、群受众或回执对账。真实租户验证必须使用 `obsion-im stream`、`inbox-worker` 和对应生产门禁。

## 验证

入口测试覆盖提及解析、答案转发和异常脱敏；静态检查确认脚本没有 OpenAI/本地历史/关键词生成实现。该测试不证明真实 DWS、钉钉租户、控制面部署或端到端延迟。
