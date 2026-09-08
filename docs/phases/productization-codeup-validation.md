# 云效 Codeup 产品化连接验证

日期：2026-09-06。状态：本地查询切片通过；真实租户、M2 整体与生产晋级仍未完成。

## 2026-09-08 当前组织目录复验

用户补充组织信息后，验证进程核对其与本地已有组织配置一致，使用已有权限为 0600 的外置密钥文件，经环境引用注入 CredentialBroker。没有把聊天中的令牌复制到源码、文档或日志，没有更改业务实例。请求通过独立临时 SQLite 控制面的正式 REST → Policy → Capability Gateway → 原生 HTTPS；没有 MockTransport。

在 2026-09-08 06:37:57—06:37:58 UTC 完成三次只读查询：`obsion` 搜索 200/0 条，`openwork` 搜索 200/0 条，未筛选第一页 200/20 条、next_page=2、complete=false。三个查询均有 PolicyDecision 和 SUCCESS 审计。目录中的具体仓库与租户标识保留在临时验证环境，不进入仓库文档。

本次证明当前组织的目录读取可用，不证明具体项目已经绑定或源码四类查询已通过。已请求用户指定目标仓库路径/数字 ID；在确认前不使用无关仓库作为项目验收样本。云效写入、全量仓库枚举、实际 scope 完整性、钉钉租户、沙箱及生产晋级未执行。

## 2026-09-07 目录发现与真实联通增量

[ADR 0095](../adr/0095-codeup-operator-discovery.md)新增独立管理目录连接器、封闭输入/输出契约、
operator-only Gateway 与管理 REST。请求前后重查当前主体、组织和连接配置，普通 Agent Run 拒绝目录枚举。
元数据不包含成员、作者或 URL；发现不自动登记或授权仓库。无 schema 变化，无新迁移，不改变旧四类读取契约。

- 增量专项：85 passed，18.79 秒，SQLite + MockTransport。新增目录参数、配置类型、字段裁剪、分页、无权限/未绑定拒绝、Agent Run 拒绝，以及读取中凭据变更后的结果丢弃。
- 精确契约门禁：5 passed，27.30 秒；OpenAPI 已更新，错误目录仍为 336，未添加或豁免错误码。
- 最终完整 Python 隔离回归：1953 passed、213 skipped、6 deselected，379.01 秒。跳过项不计作真实 PostgreSQL 或厂商验收；本轮未修改前端，未重复 JavaScript 回归。
- Ruff 全仓检查、896 文件格式及 diff 空白检查通过；四工作区 mypy 242 个源码通过。CLI 契约、阶段声明与密钥扫描通过，扫描零匹配；发布候选契约通过但 `promotion_eligible=false`，六项操作者门禁仍待完成。
- 真实 HTTPS：使用外置受限凭据文件，仅向隔离验证进程注入环境引用，正式 CredentialBroker/Policy/Gateway/原生 HTTP 适配器执行，无 MockTransport。按 `obsion` 搜索返回 HTTP 200、0 条；随后一页未过滤目录返回 HTTP 200、20 条、`next_page=2`、`complete=false`。两次均有本地 Policy 与 SUCCESS 审计。
- 此证据证明该令牌在当前组织的目录读取成功；不证明完整 scope、源码访问、生产身份或生产 PostgreSQL 事务。验证实例使用独立 SQLite，未改动运行中的业务数据库或容器部署，也未向云效写入。
- 当前项目 origin 指向 GitHub，目录搜索未匹配 Obsion。目录到本地仓库的验证映射已在本地实现，见 [ADR 0100](../adr/0100-codeup-verified-mapping.md)；仍待用户明确真实云效目标仓库名称/路径后进行四类源码查询验收。不使用无关仓库冒充目标，未将企业目录条目、凭据或组织 ID 写入仓库。

以下为 ADR0094 的历史验证快照，不能替代本次增量最终回归。

本记录对应 [ADR 0094](../adr/0094-native-codeup-read-gateway.md) 和
[接入手册](../operators/codeup.md)，属于当前产品化工作，不新增或提前关闭正式阶段。

## 已实现的用户路径

授权仓库 → 工作台选择查询 → 同一 Python REST → Policy / Capability Gateway → 当前仓库 ACL →
服务端 CredentialBroker → 固定云效 Central GET → 身份/内容/预算校验 → 当前授权重查 → Audit → 安全结果。
Run 调用使用同一条能力路径并形成 RESTRICTED CODE Evidence。旧的通用工程代理契约继续兼容。

支持仓库信息、指定提交、提交列表、固定提交的文本文件；提供未配置、无权限、凭据缺失、限流等处理指引。
没有自动 Connector、Agent grant、外部写入或模型自由 HTTP 出口。连接示例文件只提供结构，不含可用租户或凭据。

## 自动化结果

| 检查 | 结果 | 实际覆盖 |
| --- | --- | --- |
| Codeup 与契约/Registry 组合 | 83 passed，45.30 秒 | 71 个 Codeup 用例（55 个协议/边界、16 个测试控制面）及 12 个既有契约/Registry 用例；SQLite + 明确 MockTransport |
| 完整 Python 隔离回归 | 1939 passed，213 skipped，6 deselected，410.85 秒 | 控制面、Python SDK、CLI、IM；空临时 cwd，去除 OBSION/PYTEST 注入，真实租户测试未运行 |
| 完整 JavaScript 工作区回归 | Web 210、Desktop 17、IDE 12、TypeScript SDK 26 全部 passed | 新增 6 个 Codeup 交互与 1 个 HTTP 封装测试，含旧结果清理、迟到响应、HTML 纯文本展示和分页 |
| Web lint / typecheck / production build | passed | Next.js 生产构建通过，未部署 |
| 全仓 Ruff / format | passed；895 个 Python 文件已格式化 | 未豁免新文件 |
| 四工作区 mypy | passed；242 个源码文件 | 未放宽类型配置 |
| OpenAPI / 错误 producer 精确覆盖 | 完整 Python 中 passed | 96 个事件版本不变；错误目录 336，活动来源 334，保留兼容错误 2 |

213 个跳过以 PostgreSQL opt-in 为主，不能算 PostgreSQL 通过；本次没有启动真实数据库或厂商服务。
无数据库 schema 改动，无新迁移。既有来源/Memory/IM 等迁移结果只见其原记录，不复用为本切片的新证据。

最终复核新增压缩响应防护：请求 identity，并在消费正文前拒绝压缩编码，避免解压扩张绕过读取预算。83 项组合与最终全量在该补充后通过。

开发中出现过三组失败：初始错误类别使用了未注册类别、手工测试夹具遗漏模型必填字段、后置授权检查引用不存在的 Connector.transport。
均按根因修复；最终专项与完整回归是在修复后运行。格式检查初次的长行问题通过格式化修复。没有删除或减弱失败断言。

CLI 契约、阶段状态、发布说明和 Secret 扫描通过（零匹配）；离线评测执行 11 项、失败 0、跳过 27，不能将跳过算作真实模型评测通过。发布候选契约检查通过，但 `promotion_eligible=false`，六项操作者门禁仍待完成。`git diff --check` 通过。

## 实际浏览器检查

用浏览器打开仅监听 loopback 的临时 Vite 预览，直接导入当前仓库 `CodeView/CodeupReader` 与真实 CSS。
API 在预览内显式替换为合成仓库/文本和配置缺失响应；不连接企业后端，也不使用真实令牌。

- 默认桌面视图完成选择仓库、发起查询、显示配置缺失指引和查询编号；逐张查看截图，无控件遮挡。
- 390×844 下完成固定提交与文件路径输入、查询和长文本展示。页面 `scrollWidth=390`；五个新控件左右边界为 37/353，均高 42；长代码区自身宽 316、内容宽 10869，在内部滚动。
- 320×740 下页面 `scrollWidth=320`；五个控件左右边界为 37/283，无横向页面溢出；提交编号在内容区自然换行。
- 合成 `<script>` 文本按代码展示，不执行。UI 用例不代替真实 API、身份或端到端厂商联通测试。
- 检查结束恢复浏览器视口、关闭临时页，并终止本次自建 4188 预览服务。

## 尚需完成的产品验收

1. 真实云效组织/仓库、只读 PAT 的安全注入，实际 scopes 与成员关系核验，四类读取及拒绝场景的脱敏记录。
2. Agent 版本与自主执行循环对这些能力的显式接线、真实项目问答验证；目前自动 seed 不扩大任何 Agent 能力。
3. 原始 commit/tree 获取、来源账本 pin、源码 ACL 向上下文/产物传播、沙箱输入输出传输；当前单文件 hash 不能代替这些证据。
4. PostgreSQL 跨连接权限变更/撤销与审计原子性专项。当前后置重查不声明整个 Policy/Binding 图的线性化交付。
5. 钉钉正式租户与 Outbox/群受众、沙箱集群、持续学习闭环、SLO/恢复演练等完整产品化验收，继续依照 M0—M6 方案推进。

正式 `current_phase=phase-98`、Phase 99 晋级阻塞保持不变，未提交、发布或部署本切片。
