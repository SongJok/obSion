# Stream 建连稳定性验证

日期：2026-09-11。状态：VERIFIED_TEST_TENANT_CONNECTION_SLICE；长期连接与故障恢复验收仍在进行。

依据 [ADR 0108](../adr/0108-stream-connection-timeout.md)，保留官方 SDK 0.24.3 的 WebSocket、
消息路由和 ACK；只替换每个实例的建连 HTTP 方法。两个既有 Stream 入口均安装该方法，不修改全局
requests/socket 行为。

- 建连 HTTP 使用现有 httpx 依赖，连接 5 秒、读取/写入 10 秒、连接池 5 秒；这是各网络阶段超时，
  不是整个连接生命周期的绝对时限。不改变 Inbox 0.8 秒持久 ACK 预算。
- 只向固定官方建连 origin 发送已有安装凭据；SDK 的其他环境 origin 不被默许。返回必须为非空 ticket
  与不含账户、查询、片段的 WSS endpoint；重定向、厂商错误、坏响应均不当作成功。
- 日志只含本地定义的 connection_start、connection_ticket_ready、失败类别；不记录 ticket、URL、
  身份凭据、异常原文或消息正文。ticket_ready 只表示取得连接票据，不能代替 WebSocket 收件验收。
- 超时返回 SDK 既有重连循环；没有新服务、数据库、订阅或权限。安全收紧点是拒绝原 SDK 的任意建连
  origin 环境覆盖；本项目受管 zziv 安装使用固定官方 origin，不受影响。其他部署若有自定义代理，
  必须通过受管网络配置评估，不能把应用凭据发往任意 endpoint。

专项 87 passed，覆盖超时后的下一次尝试、日志无秘密、响应校验、两个入口及原 ACK/身份隔离用例；
初版新增独立 HTTP 文件触发既有架构门禁，已把请求实现移入原 `dingtalk.py` 厂商网络边界，
没有放宽架构测试。最终适配器全量 **245 passed、3 skipped，2.73 秒**；Mypy 16 个源文件通过。
增加了 WebSocket keepalive 生命周期观察，保留原 SDK 日志禁用策略与原始参数传递。
后续整合日常问答与金额来源检查后，完整 Python 回归 **2337 passed、223 skipped、7 deselected，
614.78 秒**；没有以跳过项代表真实租户或基础设施通过。

本地只重启本任务拥有的 Stream 进程，保留已有安装与工作进程。2026-09-11 18:23:18（Asia/Shanghai）
实际记录 connection_start → connection_ticket_ready。最终代码于 18:27:43 再次启动，
18:27:44 记录 `websocket_connected`，确认进入官方 SDK 已建立的 WebSocket 上下文。
生命周期观察不输出 socket、ticket 或厂商原文。此前 20/21 的 DWS 消息任务虽 SUCCESS，
仍未找到对应 Inbox；不声称重启或取得票据已解决全部收件延迟。已请用户在已确认 zziv 的点仔中发送
独立的普通输入用例 22，等待真实入站、答案与回执。其他组织、未知进程及生产资源未改动。

补充核对：DWS 当前 profile 仍为 zziv，最近五条点仔消息中能定位 20/21，读取无失败但有更早分页，
不将有限页结果表述为全量历史。服务端从 19 的 Inbox 游标继续查询 RECEIVED/PROCESSED 均为空。
[官方接收说明](https://open-dingtalk.github.io/developerpedia/docs/learn/bot/appbot/receive/)
说明单聊可直接接收、群聊要求 @；本次查阅的说明和 Stream FAQ 未给出 DWS 接口发送必然不触发的
结论。CLI 发送帮助也未提供明确的机器人回调触发选项，因此保留该原因未知，不通过取消 AI 标记或
重复发送来猜测。普通输入用例仍须取得实际 Inbox 记录。

M1 与生产验收保持未完成。无数据库迁移；回退适配器代码即可恢复旧入口，但旧版无界建连缺陷也会恢复。

## 2026-09-11 新入站与实际投递

本任务运行中的有界建连 Stream（PID49811）在北京时间18:27:44记录 websocket_connected，
20:01:34.986记录 inbox_accepted。用例23的厂商消息ID与该安装的Inbox完全相等，随后
GENERAL回答和Outbox SUCCESS/READ均有持久证据。详见
[日常闭环账本](../release/evidence/productization/20260911-dingtalk-everyday-23.json)。
没有重启其他未知进程或修改组织/安装校验。连接后收到新消息已得到证明；一个样例不证明 ACK p95、
多 worker 稳定性、断网恢复或长期服务级别。20/21未入站原因仍未知，不概括为厂商永久限制。
