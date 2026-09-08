# M1 安装域、持久 Inbox 与投递安全验证

日期：2026-09-07

状态：实现与集成进行中；不是 M1 完成声明，也不是生产晋级证据。

## 2026-09-08 DWS 兼容入口收敛

根目录 `dingtalk_obsion_agent.py` 已收敛为无状态控制面桥接：通过现有 `ExperienceRuntime`/`ImBridge` 进入统一 IM/Harness 路径，移除直接 OpenAI 调用、进程内会话历史和关键词降级。入口专项 3 passed，Ruff/编译通过；没有真实 DWS 或钉钉租户证据。官方 Stream、持久 Inbox、Outbox、群受众、厂商回执和 UAT 仍按本记录后续门禁执行。详见 [ADR 0101](../adr/0101-dingtalk-dws-control-plane-bridge.md)。

## 2026-09-07 只读回执对账切片

依据 [ADR 0097](../adr/0097-dingtalk-receipt-reconciliation.md)，已补齐已受理单聊的持久回执对账：

- `DingTalkRobotTransport` 使用官方 `readStatus` GET 查询 `processQueryKey`，校验发送状态、目标用户、已读状态和时间戳；查询失败、异常响应和未知状态统一保守返回 `UNKNOWN`，绝不发送第二条消息。
- Outbox 新增厂商发送/已读状态、已读时间、对账时间、尝试次数及前向迁移 `a86e47f58a69`；claim 在独立事务提交后才允许执行厂商 GET，旧 claim、安装/人员/Connector 指纹、Policy 或 Run 访问变化会闭锁。
- worker 扩展为“过期回收 → 入队 → 发送 → 只读对账”；管理员可以通过 `POST /api/v1/admin/im-dingtalk/outbox/{outbox_id}/reconcile` 指定一条 `ACCEPTED` 记录重试对账。管理响应仍不包含正文、凭据或原始厂商响应。
- SQLite 对账状态机与静态契约专项通过；一次性 PostgreSQL 17.11 空库往返 **1 passed**，覆盖 `upgrade/check`、新增字段、非负约束、`last_reconciled_at` 索引、回滚保护和历史账本保留。当前仍未运行真实租户回执、群受众和 UAT，不能将 UNKNOWN 或本地状态解释成厂商送达/已读。

## 2026-09-07 持久机器人 Outbox 切片

基于 [ADR 0096](../adr/0096-dingtalk-durable-robot-outbox.md)，本轮把新版 DingTalk 单聊协议接入 PostgreSQL 持久 Outbox 与 Capability Gateway：

- 新增 `a85e36f47a58` 前向迁移和同 Run/Inbox 唯一账本；队列、lease、fencing token、UNKNOWN、拒绝、重试上限与答案/Connector 指纹均持久化。一次性 PostgreSQL 17.11 容器已完成 `upgrade head` 与 Alembic `check`，约束名称与 ORM 模型一致。
- worker 默认关闭，启用后按“过期回收 → 入队 → claim → Gateway dispatch”运行；外部 POST 不自动重试，过期 claim 进入 UNKNOWN。
- 新增 `/api/v1/admin/connectors/{connector_id}/activate`，管理员可在受控环境中验证钉钉固定 egress、协议、grant、`OBSION_*` 身份变量与 CredentialBroker 凭据，再激活 Connector；不会自动绑定能力。
- `GET /api/v1/admin/im-dingtalk/outbox` 只返回内容无关的状态、attempt、fencing、receipt key 和错误码，答案正文与凭据不进入管理响应。

本轮专项验证 **98 passed**（协议、旧 IM 安全、Outbox ORM claim/fencing/UNKNOWN、激活 API和契约门禁）；Ruff、Python 编译和 OpenAPI 当前性检查通过。专项业务测试使用 MockTransport/SQLite，未向真实钉钉人员发送消息；PostgreSQL 只完成迁移升级与漂移检查，尚未完成多 worker 并发/重启演练。仍需真实 PostgreSQL 并发/重启演练、厂商回执对账、群受众授权和受控 DingTalk UAT。

修复新增 Outbox 错误码持久化的静态契约登记后，隔离全量 Python 回归为 **2012 passed、213 skipped、6 deselected（390.36 秒）**；跳过项仍不计作真实 PostgreSQL 或厂商验收。

## 2026-09-07 新版机器人出站协议

[ADR 0096](../adr/0096-dingtalk-durable-robot-outbox.md)明确完整 Outbox 的实施顺序与长期去重要求。
新增控制面内部 `DingTalkRobotTransport`，固定新版 OAuth 与单用户 `sampleText` 发送协议，
不改旧 `/chat/send` 入口，不注册 Agent 能力或公开 REST。仅供后续已提交发送资格的 Gateway 调用。

- 使用 NOT_ATTEMPTED / ACCEPTED / REJECTED / UNKNOWN 区分发送前失败、厂商受理、目标拒绝与不确定结果。
  `processQueryKey` 只表示处理键，不转换成对方已读或最终送达。无消息 POST 自动重试。
- 固定 HTTPS、无代理环境继承/重定向、4096 UTF-8 字节文本预算、64 KiB 流式响应预算、压缩正文读取前拒绝；
  拒绝凭据反射、异常收件人、无效回执；取消向上传播，由持久账本恢复为 UNKNOWN。
- 最终组合 **73 passed，27.16 秒**：51 项新协议用例、17 项既有钉钉/Bridge 安全用例和 5 项精确契约门禁。
  全部厂商请求使用明确 MockTransport，未向真实人员或群发送消息。
- 全仓 Ruff/899 文件格式与四工作区 mypy/243 源码通过。初次长行与 Any 类型收窄问题已修复，未弱化检查。
  本轮未修改已有运行路径、表结构或前端；没有新迁移，未重复完整 Python/JavaScript/真实 PostgreSQL 回归。

持久 Outbox、Gateway 授权接线、worker/重启恢复、管理视图、群受众和真实租户仍须完成。
通用幂等记录到期清理不能作为长期消息防重机制。该内部模块的通过不改变 M1 或生产晋级状态。
下文为前期切面的历史记录。

## 本轮基线

两份外部需求文件已完整读取，按较新的 `second_goal.txt` 收敛首版范围。保留既有未提交工作区改动，未执行 git commit、push、发布或真实厂商发送。

- 本地隔离 Python 回归：**1202 passed、23 skipped、6 deselected，299.56 秒**。这是新增 M1 集成之前的基线，不代表新增功能已全量回归。
- JavaScript 工作区测试通过；前端 lint/typecheck 与 `git diff --check` 通过。
- Python Ruff 通过，严格 mypy 检查 **218** 个源码文件通过。
- 全仓 `ruff format --check .` 失败：**10 份历史 Markdown 代码块需要格式化，811 个文件已符合格式**。没有把后续 mypy 的成功退出码当作格式检查通过，也没有排除这些文件以弱化门禁。
- 首次在空目录执行契约 CLI 时未指定 `--root`，因找不到 `docs/project-status.yaml` 失败；改用显式绝对路径后，项目状态、事件/错误契约、评测定义/评测门、发布说明和候选契约均通过。候选契约检查不等于生产验收。

## 已集成的投递安全切面

见 [ADR 0082](../adr/0082-im-delivery-unknown-safety.md)。首次 prepare 占有一次发送资格；后续非 SENT 禁止再次 prepare。旧 fail 接口将不确定结果记录 UNKNOWN，真实回执才能完成；原请求主体之外的报告被拒绝。Bridge 不发送 UNKNOWN/FAILED/缺失或未知状态，内部 complete 响应丢失后尽力记录不确定结果。

DingTalk `chat/send` POST 不自动重试，缺失或非法厂商回执不再回退成本地 ID。只读认证 GET 保留有界重试。

主工作区专项：**35 passed，14.75 秒**；集成源码 Ruff 与完整 218 个源码文件的 mypy 通过。测试覆盖 SQLite 并发 prepare、UNKNOWN 阻止重发、真实回执幂等、请求主体边界、Bridge 回执落库失败、DingTalk 单次 POST 和迁移安全降级。

迁移链已集成：`f3d4e5a6b7c8 → a81b92c03d14 → a82c03d14e25`。在本轮创建的一次性 tmpfs PostgreSQL 中 upgrade head 与模型漂移检查通过；100 次并发 receive、100 次并发 process、重建连接池后重放和 SQL 唯一约束测试 **1 passed，3.26 秒**。另一独立空库迁移往返测试 **1 passed，3.30 秒**，验证空账本降级/重升、历史 UNKNOWN 转换、SENT 回执保留、全部非 SENT 降级阻断及非空安装账本保护。未接触业务数据库。本轮临时容器已停止并自动移除，按专用验证标签查询无残留。

独立迁移测试已加入 CI `migration-round-trips` 的 `m1-im-ledger` 项，启用 `OBSION_RUN_IM_MIGRATION_TEST=1`；远程 CI 尚未运行。PostgreSQL 降级在检查前获取表锁，避免安全检查与 DDL 之间并发写入。

## 正在集成

- 安装、安装域 sender 绑定、持久 Inbox 与独立任务处理事务。
- 官方 Stream SDK 薄适配器：成功 ACK 只表示控制面已持久接收。
- 从 RECEIVED 列表恢复的 Inbox worker；不在 ACK 之前执行 Harness。
- 内容无关的接收查询及处理状态、版本兼容 API、前向迁移与安全测试。

上述代码已集成。首轮完整回归为 **2 failed、1310 passed、24 skipped、6 deselected，300.49 秒**；失败是新增错误来源登记遗漏及单事件协议审查清单尚未登记两张 IM 辅助表。随后逐项登记 18 个既有错误码来源并修正一处转发行号，补充辅助表只关联既有 Harness 的断言，未删除或放宽原检查。最终完整回归 **1313 passed、25 skipped、6 deselected，294.03 秒**；包含新增架构断言及迁移测试的默认跳过。跳过不计通过，两个 M1 PostgreSQL 测试已在独立临时数据库另行实测通过。集成审查发现并修复按可编辑 name/title 复用会话的上下文混入问题：改用 `ImConversationBinding` FK，复用前检查 owner、组织、PRIVATE 与其他成员；共享后拒绝继续写入。Connector 严格匹配实际已注册的 provider-docs 类型，但不冒充真实 scopes 或出站 grants 验证。

Stream/worker 及旧适配器、投递安全主工作区复验：**188 passed、3 deselected，17.48 秒**。包含官方 SDK 离线回调兼容、清理纳入 ACK 总预算、单次 POST、分页恢复及持续无正文计数。完整源码 Ruff 与严格 mypy **224** 个文件通过，前端 lint/typecheck 通过，JavaScript 共 **258** 项通过（Web 203、Desktop 17、IDE 12、TypeScript SDK 26）。

首次专项命令在关闭插件自动加载后误传 `--no-cov`，pytest 因未知参数退出 4，未执行测试；删除该无效选项后通过。首次完整 mypy 发现 13 项 Stream HTTP 参数、动态 SDK 继承及 worker 返回类型错误，修复后复验通过；没有全局关闭类型规则。

OpenAPI 已重新生成：新增 7 个安装/Inbox 路径，既有路径无语义变更，旧 schema 仅 `ImDeliveryStatus` 增加 UNKNOWN。官方可选 SDK 已固定到 `uv.lock` 并以 locked/all-extras 安装验证。契约、Registry、评测门、阶段声明和发布候选契约再次通过，生产晋级仍未满足。

全仓格式复验仍失败：10 份历史 Markdown 待修、832 文件已符合格式；历史记录明确该组修改曾被隔离权限策略拒绝，本轮未代行。没有将此失败排除出质量门。

## 尚未验收与产品边界

真实钉钉安装、完整受治理出站 Capability、真实租户对账证据、群受众授权、四意图白名单创建、根目录旧原型全面退役、p95 ACK/响应 SLO、真实租户 UAT 均未完成。本切面不保证最终回复必达，不向群自动发布用户私有任务答案。

真实 gVisor 沙箱、自主项目修改与验证、持续学习评测推广、OIDC/RLS、公平调度、24 小时容量、灾难恢复、14 天试运行和上线签署仍属于后续 M2—M6。不能用本地测试结果宣称具备完整企业 AI 底座或 GA 资格。
