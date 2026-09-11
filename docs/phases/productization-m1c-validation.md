# M1c 与云效实际连接验证

日期：2026-09-11。基线：GitHub main `c2b6199`，本轮开始、中途及最终刷新后 ahead/behind 均为 0。
本轮接续已有 ADR 0104 工作树修改，保留原有实现；新增 ADR 0105 修复。当前证据来自本地未提交工作树及实际测试租户，
不是远程 CI、已发布版本或生产晋级证据。

## 当前验收结果

### 点仔真实收发

DWS 查询确认用户指定的点仔应用已发布、机器人 ONLINE、使用 STREAM；正式 Stream、持久 Inbox worker 与本地控制面运行。
修复正式 Stream 入口遗漏 CA 初始化后，两条真实用户消息均进入 Inbox 和 Harness，用户在会话中收到回答。

| 测试 | 入站 Inbox | Harness Run | 结果 |
| --- | --- | --- | --- |
| `OW-20260911-09` | `01a08f1f-bfb4-7b40-a59a-d9a2637705d2` | `01a08f1f-c38e-77b1-8535-08bf5805a4ab` | PROCESSED；无证据时明确表示不知道，Outbox ACCEPTED |
| `OW-20260911-10` | `01a08f25-cbd8-7b7d-a96e-58ac93ee4433` | `01a08f25-d3d9-7cbf-abeb-3f67370fb25b` | PROCESSED；回复引用 README 证据，厂商 SUCCESS / READ |

第二条 Outbox：`01a08f26-2d0a-74f6-9999-9fbbac5a9131`，实际回复消息 ID：`msgeCLecE/FX4OjqiVz2kuPsQ==`。
DWS 定位了真实回复，会话界面也显示了答案；不是手工注入回调或仅验证发送 API。
Outbox 保持 ACCEPTED，厂商投递/已读字段独立保存；不把厂商接受冒充产品状态已完成。

第一条回复的早期回执策略拒绝，达到三次对账上限，保留原记录，未重发或重置计数。
通过管理 API 配置限定测试安装、私聊接收人、环境、对账入口及 `reconcile` 操作的只读 Policy，
第二条成功取得 SUCCESS/READ，没有向其他接收人开放发送权限。

### 云效全部可见仓库及源码

使用外置 PAT 引用，通过已部署 REST → Policy → Capability Gateway → CredentialBroker → 原生 HTTPS GET：

- 省略组织过滤并实际发现 2 个可见组织；按 100 条分页取得 100 + 29 条，两个组织分别有 128 / 1 个仓库。
  复合组织/仓库 ID 全部唯一，完整路径有效，末页 `next_page=null`，共 129 个，均未归档。
- 129 个仓库均接入本地注册表并建立四项只读能力的精确绑定；129 / 129 次真实 `repository.get` 返回 200。
  本地仓库为 RESTRICTED，仅显式授权指定管理员与当前测试适配器主体，不授予全组织访问。
- 每个组织抽取一个仓库，真实完成 `repository.get`、`commits.list`、`commit.get`、`file.read`，共两组成功。
  文件固定到实际 commit；路径、字节数、Git blob hash 及输出 SHA-256 校验通过。没有在文档记录源码或私有仓库名称。
  这是每组织源码抽样，不能外推为所有仓库的所有文件、分支或写权限已验证。
- 指定管理员密码会话返回 201；独立 cookie 会话未携带开发 Bearer，能够看到全部 129 个仓库并完成真实仓库读取。
  本地默认分支与已验证的厂商元信息同步，保留当前 ACL 和分级。
- 本地连接只保存 `env://OBSION_CODEUP_APP_ID`。该历史变量由用户确认为 PAT 来源，不将普通 App ID 猜测为令牌。
  不向模型暴露令牌，不调用云效写接口；本阶段不启用官方 MCP 默认的写工具。

目录完整性对应本次查询时的 PAT 可见范围，不是跨页事务快照或未来仓库自动授权承诺。
新增仓库需要重新发现并显式配置本地授权。实际成功操作证明本次读取权限，不能代替完整厂商 scope 清单验收。

## 根因修复与兼容性

[ADR 0104](../adr/0104-im-receipt-and-catalog-validation.md) 保留并验证阶段元数据、目录分页、ACK、可靠投递和事件契约修复。
[ADR 0105](../adr/0105-pat-catalog-and-stream-trust.md) 记录本轮增量：

1. 正式 Stream 在 SDK 建连前复用可信 CA 初始化；本机默认 CA 数为零时加载 certifi，不关闭 TLS。
   新诊断仅含固定阶段码，SDK 私有消息日志保持禁用。
2. 新增管理员 PAT 跨组织目录 REST，调用前后重查当前主体、组织和 Connector；保留原 Run 调用模式。
3. 重复能力绑定触发现有唯一约束时，相同配置返回原 ID，不同/禁用绑定返回 409，不再发生 500 或隐式扩权。
4. 真实 Codeup 文件 `size` 为十进制字符串，原实现错误拒绝。仅兼容规范、有界数字字符串，保留严格内容和 blob 校验。
   两个组织的新实例文件复测已通过。

## 迁移及运行验证

本轮无新 schema。前序本地服务在备份后已从 `f3d4e5a6b7c8` 前向升级到 `d0e2f5a7b3c4`。
新建独立 PostgreSQL 17 验证容器执行历史 IM 升级、降级、重新升级到 head 及 drift 检查：1 passed（2.16 秒）；
容器已移除，没有对业务库降级。更新后的 API 健康检查成功，业务库 Alembic check 返回 `No new upgrade operations detected`。

## 自动化验证

- 修复后的相关专项：90 passed，覆盖 Codeup 文件严格格式、目录 Gateway、绑定重放/冲突及静态契约。
- 最终 Python 完整隔离回归：2225 passed、221 skipped、7 deselected（542.32 秒）；跳过项主要为显式开启的 PostgreSQL 或外部集成验证，不计为通过。
- JavaScript：Web 210、Desktop 17、IDE 12、TypeScript SDK 26，共 265 passed。
- Ruff 检查/格式、严格 mypy 250 源文件、前端 lint/typecheck 通过。
- 契约：343 个错误码、98 个事件版本；状态校验通过，Secret 扫描 0 命中。

## 失败历史与保留边界

早期 `OW-20260910-01`、`OW-20260911-08` 仅见发送，没有真实入站和回复；这些旧失败已由上述可关联实消息证据替代。
初始 Python 回归曾为 2145 passed / 7 failed，旧 API 密码入口曾为 404；均在后续实现和验证中修复。
手工回调的 ACK=200 或取得 processQueryKey 仍不单独记为用户消息闭环。

Phase 98 当前企业连接增量已取得测试租户证据；M1 整体验收、Phase 99 和生产晋级保持关闭。
群成员与受众实测、四意图完整 UAT、来源到 Harness 自动接线/沙箱传输，以及既有 staging、恢复、签名、
OIDC/秘密管理、数据负责人和安全批准门禁仍需相应证据。未创建发布、Git 提交或推送，未改写远端 main。
