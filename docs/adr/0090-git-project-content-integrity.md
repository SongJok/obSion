# ADR 0090：Git 项目内容完整性，不以 hash 代替来源授权

- 日期：2026-09-06
- 状态：内部实现与本地完整回归通过；没有开放项目来源获取能力
- 关联：[ADR 0088](0088-bounded-project-copy-delivery.md)、[M2 架构门](../architecture/productization-m2-gate.md)

## 背景与已核实接点

`CodeIntelligenceService.index_snapshot` 接受调用者 `commit_id` 并建立代码索引；`CodeSourceFile` 保存 hash/大小而不是项目文件字节。这是代码图契约，不是可信 Git 版本获取。`CodeRepository` / `CodeRepositoryGrant` 提供组织内分级、ALLOW/DENY 用户/角色/部门授权及删除过滤，但没有绑定可信远端或 Workspace 项目来源。

工程 `git.commit` / `git.diff` / `git.history` 接口经 `HttpJsonExecutor` 返回规范化摘要；`allowed_repositories` 与 `declared_grants` 不是厂商应用实际 scopes 证明。Gateway 已有 Policy、AgentSpec、Capability version、Connector 和审计检查，但普通结果会进入 Evidence。不能通过塞入源码 JSON 或上传普通 Artifact 来跳过更窄的仓库权限传播。

进一步核实：当前 `Connector` 为可变配置行，模型中没有独立 `ConnectorVersion`；`ProjectRevision.connector_version_id` 只是基础契约引用，不能填入 Connector ID 冒充不可变版本。`ArtifactService` 的列表和下载只检查 Workspace 访问，上传的 `lineage` 由调用者提供，不是强制的来源授权关系。

因此先补可独立验证的纯内容完整性，不先开放任意 URL、宿主路径、Git clone 或源码输出能力。这只是授权固定来源获取的前置切片，不关闭该交付项。

## 决策

新增内部 `sandbox/git_integrity.py::verify_git_snapshot(snapshot, raw_commit=...)`：

1. 输入复用 `ProjectSnapshot` 和 `ProjectRevision`，不增加第二种项目归档或数据库事实源。只处理内存字节，无网络、文件读取、进程、凭据或引用解析。
2. 原始 commit 对象内容必须为精确 `bytes`，非空且不超过 256 KiB，在 hash、解析之前检查。根据已 pin ID 长度选择 Git SHA-1 / SHA-256 对象格式，计算 `kind + SP + decimal_length + NUL + content`；不使用 JSON 摘要重新拼凑所谓原始 commit。
3. commit hash 必须等于固定 `commit_id`，首个 tree header 必须等于 `tree_id`。拒绝 NUL、header CR、重复 tree、缺 author/committer、异常 continuation、非法 parent ID 及重复 parent。支持多 parent 和扩展 header 的原始多行字节，不验证签名、父图存在或全部 Git fsck 语义。
4. 根据每个文件的原始 UTF-8 字节计算 blob，按 `100644` / `100755` 构造各层 tree，目录 mode 为 `40000`。Git 使用原始 UTF-8 名字节排序，比较目录时追加 `/`，不是普通完整路径排序。自叶至根计算 tree，最终必须与 pin 一致。
5. 保留已有 500 文件、2000 文件/目录条目、32 层路径、单文件 256 KiB、总内容 8 MiB、manifest 上限及路径/Secret 拒绝。缺文件、额外文件、改名、mode/换行变化都不能通过完整 tree 校验；不静默过滤 `.env.example` 等被拒文件后声称完整项目。
6. 本切片只有普通文本文件，因此 symlink / gitlink / 非普通 mode、未表达的空子树等无法重建原 tree，必须拒绝。LFS pointer 前缀显式拒绝，不以 pointer 文本冒充下载后的项目文件，也不运行 clean/smudge、attributes、hooks 或依赖命令。
7. 成功返回 `None`，不返回授权票据、来源认证标识或 `VERIFIED` 交付状态；拒绝只含固定原因，不回显源文件、commit 作者、消息或异常输入。

## 授权与完整性的边界

Git 对象不含本地 organization / Workspace / repository UUID / Connector version 身份，同一内容可以存在于多个仓库。修改这些本地引用不会改变 Git hash；专项明确验证这一事实，防止后续把该函数当授权服务。

SHA-1 仅为兼容 Git 对象格式，不提供防碰撞安全签名或来源认证。本切片不实现 SHA-1 collision detection。即便 SHA-256 全部匹配，攻击者仍可以提供自洽的其他仓库对象。来源必须另行由可信 Gateway 确认仓库身份、应用实际 scopes、固定版本和任务授权。供应商归档缺文件、应用 scopes 缺失或来源不确定时应拒绝，不以一致的 caller-supplied hash 补足授权。

后续需要：管理面可信仓库绑定与不可变来源 pin、运行时授权交集和撤权、凭据仅在 Broker、受限网络/响应预算、来源 ACL/分级传播、Kubernetes 输入输出、持久实例/配额/恢复。每次获取、使用、发布、下载与最终发送均须重查，不能把完整性结果缓存为永久权限。

## 验证方案与状态

- 正反向覆盖 SHA-1 / SHA-256、空文件/空树、可执行 mode、中文/CRLF/无末尾换行、多 parent、扩展 header、pin/内容/mode 不一致、commit 解析与分配前预算、LFS、symlink/gitlink 和过滤文件拒绝。
- 在仅由测试创建的空临时 bare Git 对象库使用固定 `hash-object --no-filters` / `mktree -z` 作为独立实现，核对目录排序、中文名字、mode、blob/tree/commit。禁用系统/全局 Git 配置，空 HOME、空 template 和 hooks 路径；无远端、无 ref 提交、无项目代码执行。
- 首轮 Ruff 发现测试字符串超长，后续 mypy/pytest 因命令短路没有运行；结果不得登记为测试通过。修复后 Git/项目合并 156 passed（0.76 秒）；再补四项预算回归后新增 Git 测试共 55 项，最终完整 Python 1685 passed / 36 skipped / 6 deselected（309.50 秒），Ruff / 872 文件格式 / mypy 233 源码及 git diff --check 通过。CLI 契约和 Secret 零匹配扫描通过，生产晋级仍 false。详情见 [M2 验证](../phases/productization-m2-validation.md)。

## 兼容性与迁移

没有新增表、事件/错误注册、REST/SDK 接口或 Agent 能力；无需数据库迁移。内部 `ProjectRejected` 不作为新增公开错误契约。不修改现有 Code Graph / 工程摘要、Policy 只读边界、Gateway 或 Harness。普通 `ProjectSnapshot` 仍可表示沙箱修改后的文件，不能在其构造器中强制要求所有副本匹配基础 tree；完整性检查只用于基础项目对象的明确调用边界。

该切片不证明真实 Git 租户、来源撤权、受众 ACL、Kubernetes/gVisor、项目行为或生产可用。M2 与正式 Phase 99 晋级保持未完成/阻塞。
