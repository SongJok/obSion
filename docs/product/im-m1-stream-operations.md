# M1 钉钉 Stream 与持久 Inbox 恢复操作说明

2026-09-11 更新：正式 Stream 入口现在也在 SDK 建连前初始化 TLS 信任库；系统默认 CA 为空时使用
certifi，保留显式证书配置，绝不禁用验证。SDK 原始日志仍关闭，应用仅记录 `inbox_accepted` 或带
`normalize/persist` 固定阶段的 `inbox_rejected`，不记录消息、票据、身份或异常原文。
点仔真实私聊已经取得 Inbox → Run → Outbox → 厂商 SUCCESS/READ 的关联证据，见
[M1c 验证记录](../phases/productization-m1c-validation.md)。下文“未执行真实连接”是早期切片的历史边界，
不覆盖这次新增的测试租户证据；真实群、四意图创建和生产验收仍须分别完成。

本增量仅实现官方 SDK 薄入站适配和控制面 Inbox 恢复客户端，不代表 M1 完成或真实租户验收通过。没有执行真实钉钉连接、消息发送或模型调用。正式入口仍是 Stream → Inbox；根目录 `dingtalk_obsion_agent.py` 仅保留 DWS 兼容用途，并且现在只把消息转发到控制面 Harness，不直调模型、不维护本地历史、不生成关键词回答。该兼容入口的决策见 [ADR 0101](../adr/0101-dingtalk-dws-control-plane-bridge.md)。

## 安装与启动

安装工作区包及可选依赖，例如在仓库根目录执行：

```sh
uv sync --package obsion-im --extra stream
```

`stream` extra 固定 `dingtalk-stream==0.24.3`。普通 IM 安装、Inbox worker 不要求 SDK；缺依赖时 `stream` 给出明确安装提示。

由密钥管理器注入以下环境变量，不将实际值写入 TOML、命令行参数、源码或日志：

- `OBSION_URL`：固定控制面 origin。生产仅 HTTPS；HTTP 仅允许 localhost/127.0.0.1/::1。本入口拒绝 URL 用户信息、路径、query 和 fragment，不跟随重定向、不继承环境代理。
- `OBSION_TOKEN`：控制面 Bearer。服务端必须验证其组织和安装授权。
- `OBSION_IM_INSTALLATION_ID`：管理员预配置的安装 UUID，也可用 `--installation-id UUID` 覆盖。
- `OBSION_DINGTALK_APP_KEY`、`OBSION_DINGTALK_APP_SECRET`：官方 Stream 客户端凭据，仅在适配器中用于官方 SDK 建连。
- `OBSION_DINGTALK_CORP_ID`：必填固定企业；每条消息的 `senderCorpId` 必须一致。
- `OBSION_DINGTALK_STREAM_APP_ID`：可选固定 Stream 消息头 `appId`。配置后严格匹配，不将其等同于 AppKey。每条消息的 `robotCode` 必须与固定 AppKey 一致。

先在控制面建立 ACTIVE 安装和安装域 sender 绑定，再显式启动：

```sh
obsion-im --channel dingtalk stream --installation-id UUID
obsion-im inbox-worker --installation-id UUID --once
obsion-im inbox-worker --installation-id UUID --limit 20 --max-events 200 --poll-interval 1
```

以上是操作示例，本次开发未运行真实连接。Stream 和 worker 均拒绝 `--deliver`、`--outbox`、`OBSION_IM_DELIVER`、`OBSION_IM_OUTBOX`，包括显式 local-outbox；须从原出站进程环境中移除它们。其他旧命令行为不变。

## 事务、ACK 与恢复

1. 官方机器人回调只规范化 `msgId`（兼容 payload `messageId`）、`senderStaffId`、`conversationId`、`conversationType` 字符串 `1/2`、`text.content`。拒绝无稳定事件键、缺字段、超长输入和不匹配 corp/app。业务事件键不使用连接消息头 messageId，以免重连导致不同键。
2. 只向固定 `/api/v1/experience/im/installations/{配置 UUID}/inbox` POST 五字段 JSON。payload 中的安装、URL、sessionWebhook、token 和其他字段不转发。安装 ID 不从消息推导；sender 到 Principal 的授权由控制面负责。
3. 仅精确 HTTP 202 且无正文回执字段、安装、事件键、状态、UUID、时间合法才返回官方 `AckMessage.STATUS_OK`。重复消息复用同一业务键；控制面承担持久去重和内容冲突检查。HTTP 200、重定向、无效 JSON、错误状态或超时均不 ACK success。
4. callback 不调用 process、不等待 Run、不执行 Harness/模型/外部发送。ACK 只代表持久接收。持久接收及客户端关闭共享 0.8 秒总体等待预算，关闭另限 0.05 秒，另有 httpx 阶段超时；这不是实测端到端 ACK ≤1 秒证据，SDK 建连、调度、网络仍需真实环境验证。
5. worker 使用 `GET ?status=RECEIVED&limit=20&after=UUID` 的无正文列表，按 UUID 升序推进稳定 keyset，再 POST `/inbox/{id}/process`。process 的唯一逻辑任务及幂等事务由控制面实现；worker 不执行外部效果，也不清除失败记录。
6. 一条处理失败不会终止其他事件；成功、失败均推进游标，避免前 20 条失败堵住后续。持续模式达到 max-events 后保留游标，走到末尾后从头重试。`--once` 最多处理 max-events 个事件，失败时退出码 1；超过限额的积压应使用持续模式处理。进程重启从持久 RECEIVED 恢复，不依赖内存去重。
7. process 响应丢失或异常属于不确定结果：不执行补偿、不生成第二个任务、不删除 Inbox。下次列表与服务端幂等状态决定恢复结果。列表故障只增加无正文失败计数并等待下一轮。

## SDK 接口核验及日志/停止限制

开发时从公开 PyPI 将官方 0.24.3 安装到临时检查目录，核对了 `dingtalk_stream/handlers.py`、`frames.py`、`stream.py`、`chatbot.py`、`credential.py` 和 `__init__.py`；同时读取了官方 GitHub `handlers.py`。使用已核验的接口：

- `Credential(client_id, client_secret)`、`DingTalkStreamClient(credential, logger=...)`。
- `CallbackHandler.process(message)` 返回 `(code, response)`；官方 `raw_process` 负责用原消息头 ID 构造 ACK。
- `register_callback_handler(ChatbotMessage.TOPIC, handler)`，TOPIC 为 `/v1.0/im/bot/messages/get`。
- `start_forever()` 管理官方连接与重连。

公开源：<https://github.com/open-dingtalk/dingtalk-stream-sdk-python>，发行包：<https://pypi.org/project/dingtalk-stream/0.24.3/>。SDK 升级必须重新核验和回归，不能自动浮动版本。

0.24.3 的 SDK 默认日志可能包含连接 ticket、原始消息及异常。本包装向 SDK 客户端及三个 handler 提供独立禁用 logger（不传播、不改变应用或全局 logger）；不记录厂商原始响应。worker 只输出 processed/failed/polls_failed 累计计数；持续模式每轮输出并 flush 一条 JSONL，--once 输出最终计数。不要在生产启用第三方 HTTP/WebSocket wire DEBUG 日志。

官方 0.24.3 没有公开 stop API，且 `start()` 捕获并继续处理 asyncio CancelledError；因此不声称 Stream 可可靠优雅取消。生产将 Stream 单独作为受监督进程，使用 SIGTERM 和监督器终止宽限策略。Inbox 先提交再 ACK，终止中的不确定接收由厂商重投和持久去重恢复。worker 支持可中断轮询等待、每事件之间检查停止及 asyncio 取消；SIGTERM 重启同样依靠持久 Inbox，无本地队列需保存。

## 运输边界及未完成项

本增量在 `inbox.py` 封装固定控制面 typed HTTP client；底层使用已有 httpx 依赖，不包含厂商 HTTP endpoint。现有 `AsyncObsionClient._request` 丢弃 HTTP 状态并接受任意 2xx，不能证明恰好 202，因此此处使用最小独立控制面 transport，严格状态校验、禁 redirect 和环境代理；不是第二后端或新 Harness。

本入口没有出站能力，也不把 process 等同于运行/发送完成。Capability Gateway、群受众发布、Outbox、UNKNOWN 对账、真实租户机器人配置、并发去重与 PostgreSQL 事务门禁仍需对应服务端增量和独立验证。本地 MockTransport/假 SDK 结果不能替代真实 ACK 延迟、断网恢复或租户权限验收。
