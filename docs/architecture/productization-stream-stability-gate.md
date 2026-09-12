# Stream 建连稳定性架构门禁

日期：2026-09-11。状态：VERIFIED_TEST_TENANT_CONNECTION_SLICE。

- 单一 Python 入站适配器，复用原安装、官方 SDK WebSocket/ACK、Inbox、Policy 与 Harness。
- 建连请求有显式网络超时；失败返回既有重连循环。
- 不修改全局网络函数，不打印 ticket、凭据、厂商原始响应和消息正文。
- 固定官方建连 origin；不新增订阅、不扩大权限。
- 245 项适配器测试、真实连接票据及 WebSocket 已连接状态已验证，用例23新消息入站、Run和SUCCESS/READ已核对；不能标记完整 M1、生产发布或长期稳定性通过。

见 [ADR 0108](../adr/0108-stream-connection-timeout.md) 和
[验证记录](../phases/productization-stream-stability-validation.md)。
