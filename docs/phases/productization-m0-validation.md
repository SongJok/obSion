# M0 产品化可信基线验证

日期：2026-09-05

状态：进行中；不是 M0 完成声明，不改变 Phase 99 的生产晋级阻塞。

## 范围

根据 `goal.txt` 与 `second_goal.txt` 建立 [产品化验收映射](../product/productization-plan.md)，保留当前单 Python 控制面、统一 Harness、Gateway/Policy 及历史契约。当前工作区存在此前未提交改动，本记录验证工作树，不冒充同一已提交候选版本的干净构建证据。

新增 `scripts/test_local.py` 与 `make test-local`：在临时空目录执行 Python 本地回归，移除继承的应用和 pytest 配置，显式加载 asyncio 插件并排除现有 live 测试。原 CI、`make test`、迁移与真实租户入口保持兼容。该脚本不是网络隔离沙箱。

新增测试覆盖环境配置剥离、空工作目录、固定测试集合、失败退出码传播、临时目录清理及拒绝参数注入。仅测试工具与文档改变，无数据库结构变更，无需新增迁移。

## 本次已验证

| 检查 | 结果 |
| --- | --- |
| Python 既有源码及测试 Ruff | 通过 |
| Python 严格类型检查 | 217 个源文件通过 |
| 新增测试工具 Ruff 及格式检查 | 通过；首轮格式错误已修复 |
| JavaScript 全工作区测试 | Web 203、Desktop 17、IDE 12、TypeScript SDK 26 项通过 |
| JavaScript 全工作区 Lint / typecheck | 通过 |
| JavaScript 全工作区构建 | 通过；Desktop、IDE、TypeScript SDK 编译及 Next.js 生产构建成功；未发布或部署 |
| `git diff --check` | 通过 |
| 临时 PostgreSQL 完整 Alembic upgrade | 通过，head 为 `f3d4e5a6b7c8` |
| Alembic 模型漂移检查 | 通过，`No new upgrade operations detected.` |
| Helm 静态验证 | 本地缓存 Helm 3.18.4 镜像、禁网络、只读 chart 挂载下 lint 和 template 通过；只有 icon 建议，不代表集群部署验证 |
| Java SDK 测试 | 6 项通过，0 失败、0 错误、0 跳过，BUILD SUCCESS；一次性 Maven/JDK 21 容器只读挂载源码并在临时目录构建，下载公开 Maven Central 依赖。此前离线尝试因缺少 `maven-resources-plugin:3.4.0` 缓存失败，未执行测试；本次复验通过，不抹去此前失败记录 |
| OpenAPI 契约核对 | 空临时目录、显式测试配置下生成，JSON 与 `docs/api/openapi.json` 完全一致，未覆盖登记文件 |
| 事件/错误契约、Registry、评测集及评测门定义 | 通过，96 个事件、325 个错误码、8 个 Agent、16 个 Connector、14 个 Skill |
| 发布说明及候选契约检查 | 通过；`promotion_eligible=false`，6 个操作方门禁仍为 PENDING，未验证发布产物清单 |
| 离线评测执行 | 11 项执行通过、27 项 RUN_OUTPUT 跳过；不是 38 项真实模型评测通过 |

迁移使用本地已有的 `pgvector/pgvector:0.8.6-pg17-bookworm` 镜像、独立容器、随机 loopback 端口和纯测试凭据，不挂载或修改已有数据库。Alembic 在空工作目录与清理后的环境运行，不读取仓库 `.env`。

首轮误选普通 PostgreSQL 镜像，因缺少 `vector` 扩展失败；随后停止该临时容器并使用 pgvector 镜像完成复验。没有删除扩展要求或弱化迁移。

## 后续增量与复验

- 密钥扫描已扩展到无扩展 UTF-8 文件及 Dockerfile/Containerfile/Makefile 变体，二进制与测试 fixture 仍按规则排除。发布加固 32 项测试通过；本次 `scan-secrets` 为 0 项匹配。该增量后的第二轮 Python 全量回归为 1189 passed / 22 skipped / 6 deselected，耗时 252.40 秒；后续新增代码仍需复验。
- 全仓库 Ruff 首次发现三个根目录历史钉钉脚本的 15 个规范问题；导入及格式修复后 `ruff check .` 通过。随后 `ruff format --check .` 仍失败：10 个历史 Markdown 文件的 Python 代码块需要格式修正，807 个文件已符合格式。尚不能声明全仓库格式门通过。文档代理因隔离工作树权限策略被拒绝写入主工作区，已停止，未通过其他方式代行被拒绝操作。未启动这些历史机器人脚本或执行真实外部调用。
- Phase 102 Week 1 原完成报告已明确标为 SUPERSEDED，撤回完成声明并保留历史内容；Phase 100 正式报告与架构门明确为 PROVISIONAL，原占位已替换为真实结果及未满足项。

## 新增迁移测试后的补充检查

- `ruff check .`、217 个源码文件的严格 mypy、`git diff --check` 再次通过。
- 在空临时目录与清理后的应用环境中，事件/错误契约、Registry、发布说明和候选契约再次通过，密钥扫描为 0 项匹配。候选仍有 6 个 PENDING 操作方门禁，`promotion_eligible=false`，未校验发布产物清单。
- 语义迁移测试集成后的本地 Python 全量回归：1189 passed / 23 skipped / 6 deselected，263.95 秒。新增的 1 项 skip 是独立启用的语义迁移测试，已在一次性 PostgreSQL 中另行通过。此轮发生于阶段声明检查集成期间，不作为其最终完整快照证据。随后包含阶段声明检查的全量回归已完成：**1202 passed / 23 skipped / 6 deselected，270.44 秒**；这是当前未提交工作树证据，不是干净候选或生产验收。

## 阶段声明持续检查

已新增 `obsion validate-project-status`，接入 `make validate-project-status`、默认 `make lint` 及 CI quality。主工作区专项 **13 passed，0.20 秒**，真实 CLI 调用通过；全仓库 Ruff、218 个源码文件的严格 mypy 及 `git diff --check` 通过。

本次检查覆盖 99 份正式报告和 99 份架构门：当前 Phase 98，下一阶段 Phase 99；Phase 100 为 PROVISIONAL，显式登记的 Phase 102 Week 1 历史报告为 SUPERSEDED。包含孤立超前架构门的非终态校验。`production_promotion_evaluated=false`，不代替生产门禁；也不表示所有根目录历史总结已逐一收口。该工具不修改运行时或数据库，无需迁移。远程 CI 尚未执行。

## 语义迁移持续回归

新增 `test_postgres_semantic_migration.py`，以独立 `OBSION_RUN_SEMANTIC_MIGRATION_TEST=1` 启用，并加入 CI `migration-round-trips` 的 `m0-semantic` 独立数据库项。主工作区集成后，在一次性 pgvector PostgreSQL 数据库实测 **1 passed，2.62 秒**；测试后容器已停止并自动移除。新增测试 Ruff、格式及 `git diff --check` 通过。CI 配置已修改，但未推送或声称远程 CI 已运行。

覆盖 e8→f3 两张历史表的结构、既有 metrics 数据、历史实体与查询记录在连续两次 upgrade head/check 后保持不变。降级会删除历史表，因此 downgrade/re-upgrade 仅证明结构重建，不证明被删除表的数据保留。测试禁用 dotenv，并固定 Alembic 使用同一显式配置；验证命令还清理继承应用配置、使用空临时工作目录和随机 loopback 端口，不接触业务数据库。

## 已完成的数据库补充验证与尚未完成项

- Python 本地全量回归已完成：1181 项通过、22 项跳过、6 项 live 排除；跳过项不计通过。
- 临时真实 PostgreSQL 不变量测试已完成：16 项通过、5 项历史迁移往返测试因独立开关未启用而跳过。随后在五个独立临时数据库分别启用审计表重命名、Phase 2 身份、Phase 5 会话、Phase 79 幂等账本、Phase 98 密码凭据的迁移往返测试，5 项全部通过。
- 本次新增代码后的完整架构、契约、发布检查和干净快照证据尚待完成。
- 本次两次创建的临时数据库容器均已停止并自动移除；通过本次验证标签查询，无残留容器。
- 真实钉钉租户、gVisor 集群、OIDC、容量、灾难恢复、UAT 和生产签署不属于本地测试通过的推论。

不得据此把 M0 或 M1—M6 标为完成，也不得将跳过计为通过。

## 2026-09-06 模型历史、重试与基线修复

以下为后续工作树增量，不覆盖上文各轮历史证据：

- 主树完整本地 Python 回归：**1374 passed / 25 skipped / 6 deselected，303.36 秒**。涵盖提供商中立工具历史、OpenAI-compatible / Anthropic / Gemini 两轮真实 ModelGateway + SQLite + MockTransport 测试，以及只对同租户已 pin `SideEffect.NONE` 版本进行瞬态重试的保护。不包含尚在隔离树开发的沙箱、自主循环和反馈学习。
- 工具结果中的嵌套 JSON 凭据曾被新回归在三提供商上复现外传到 MockTransport；现先解析 JSON 再递归脱敏，并保留工具角色与调用 ID。参数拒绝重复 JSON 键和非有限数字。未调用真实模型，也不宣称兼容所有提供商最新 thinking / thought-signature 协议。
- 全仓 Ruff 与格式门通过，**847 个文件已格式化；mypy 225 个源文件通过**。此前 10 份历史 Markdown 的代码块格式失败已修；ORM 名称列表改为 `text` 围栏，未把伪代码修改成生产实现。
- JavaScript lint / typecheck / build 均通过，包含 Next.js 生产构建；此前四工作区测试合计 **258 passed**。未发布、推送或部署。
- 普通历史文档中发现疑似真实 App Secret：部署指南 3 处，`DINGTALK_BOT_SUCCESS.md` 1 处，企业集成报告 2 处，均已从当前文件移除；另移除历史会话票据 1 处。扩展 IM Secret 字段扫描后，首次回归 **68 passed / 1 failed** 正确发现后 3 处残留，清理后 **69 passed，17.49 秒**。输出只含位置和类型，不包含原值。当前扫描无匹配不等于凭据已轮换、Git 历史已清理或完整泄露调查已完成；需管理员在管理面撤销/轮换。
- 根目录相关 Phase 102、任务与钉钉原型报告新增明确 SUPERSEDED / 历史说明，撤回将旧勾选视为正式完成的解释。DWS 仅限管理面；不执行历史文档中的管理、部署或机器人启动命令。

## 2026-09-06 Memory 撤销迁移验证

新增前向迁移 `a83d14e25f36_memory_revoke_guard.py`，父版本 `a82c03d14e25`。旧状态 CHECK 已允许 `REVOKED`，但 `obsion_guard_memory_mutation` 漏掉合法撤销；本次只补 `CANDIDATE / APPROVED → REVOKED`，保留内容、来源、租户等不可变规则及终态不可复活。已有撤销记录时拒绝降级，不改写其状态来迁就旧版；未修改历史迁移。

- PostgreSQL **17.11** 一次性空库：迁移专项 **1 passed，2.64 秒**，同库 Memory / IM Inbox / 只读重试不变量 **34 passed，5.57 秒**。
- 加强测试后重新创建容器，三个独立空库分别验证：Memory **1 passed，2.37 秒**、IM **1 passed，3.46 秒**、语义迁移 **1 passed，2.03 秒**。包括旧缺陷复现、upgrade / downgrade / re-upgrade、Alembic 模型漂移检查、25 种状态转换、各状态下不可变字段及删除拒绝、撤销与篡改同语句拒绝、已有撤销记录的降级拒绝。非法修改必须报触发器 SQLSTATE `23000`，不能用外键错误冒充不变量通过。
- IM 迁移失败回滚断言改为真实当前 head，不再假定 `a82c03d14e25` 永远是最新版本；仍精确验证失败后的版本与回执保护。
- 两次测试容器及测试数据均已清理，无业务数据库挂载，随机 loopback 端口和纯测试凭据，不读取仓库 `.env`。
- 新迁移专项加入 CI 独立数据库矩阵。Trivy 三处扫描改为阻断 **HIGH / CRITICAL**，不再忽略 unfixed；当前没有设置漏洞豁免。这是工作流配置变更，不是远程 CI、漏洞库扫描或无漏洞的证据。

后续隔离实现合并、CI 配置与扫描增量之后仍需最终同快照完整回归。正式 Phase 99 / M6 晋级门禁保持阻塞。

## 2026-09-06 沙箱主树专项与审查边界

- 内部 Kubernetes HTTP 后端、可信入口及独立测试已集成，契约见 ADR 0084。首轮主树 Ruff 通过、858 个文件格式通过，但 mypy 在 macOS 判定 Linux 平台检查后的语句不可达，命令失败且 pytest 未执行。
- 将真实 Linux/non-root 检查提取为独立函数，未删除防护或禁用类型错误。复验 **mypy 229 个源码通过；sandbox、CI 配置和项目状态专项 115 passed，2.67 秒**。随后沙箱 Ruff 与 format 再次通过，6 个 Python 文件符合格式。
- 此结果为主树本地专项；Kubernetes HTTP 使用 MockTransport，入口系统调用部分使用替身和本地受控进程组，不代表真实 Linux/gVisor/CNI 或项目任务验收。尚未接入 Harness/Gateway，也未构建、发布镜像。
- M3 自主知识循环和 M4 显式同意学习候选仍在各自隔离树，尚未合入主树。独立审查遇到跨工作树隔离拒绝；存在拒绝后切换只读工具访问目标的执行偏差，已要求全部停止。本轮没有有效独立审查结论，不计入通过。实现代理报告的 148 项与 34 项隔离回归不能相加计为主树回归，生产门禁不变。

## 2026-09-06 项目副本完整回归失败及事件事务修复

- 加入 ZIP 中央目录分配前预检后的完整 Python：**1594 passed / 2 failed / 26 skipped / 6 deselected，298.01 秒**。该轮 Ruff、863 个文件格式和 mypy 232 个源码通过，不抵消测试失败。
- 第一处失败是静态错误分析器把未绑定 `object.__setattr__(self, "patch", ...)` 当成实例方法，错读 receiver/字段参数。先加 23 项正反向测试，**4 failed / 19 passed / 107 deselected**；修复 builtin/alias 解析并保持遮蔽与 Error 字段写入拒绝后，静态分析、精确契约门和完整项目副本专项 **240 passed，28.29 秒**。未把 frozen 类改为可变、未忽略源码路径、未削弱错误门禁。
- 第二处 Workflow 偶发 `IntegrityError` 经交错调度复现为 Run 事件唯一序号冲突。普通独立重跑十次虽通过，不能作为修复证据。改为数据库原子计数分配、SAVEPOINT 原子写入及成功后的事务可追踪缓存同步，详见 [ADR 0089](../adr/0089-atomic-event-sequence-allocation.md)。
- 事件新测试首轮 **9 failed / 26 passed，34.21 秒**，八项因合成用户缺 email，另项是方法拆分后的错误来源清单；类型检查同时报两处 SQLAlchemy 类型错误。修正后 **2 failed / 33 passed，33.57 秒**，剩余为无 Run 场景误用需 Run 的契约。后续加强断言依次复现内部失败后的缓存残留与调用者 SAVEPOINT 回滚未失效，均完成修复，不删除约束或重试能力调用。
- 最终 SQLite 序号专项 **10 passed，3.72 秒**；事件、Workflow 确定性交错、冻结契约和精确来源门 **38 passed，36.08 秒**。PostgreSQL **17.11** 两次一次性库先相关十项 **10 passed，4.62 秒**，再通用 integration 全集 **27 passed / 7 skipped，6.72 秒**；七项独立迁移开关未启用，审计专用迁移按 CI 入口排除。两次 upgrade head / schema check 均通过，两个自建容器已清理，未访问业务数据库。
- 本段四 JavaScript 工作区完整复验：**258 passed**（Web 203、Desktop 17、IDE 12、TypeScript SDK 26），lint / typecheck / build 全通过，无跳过。此后未改前端源码；不是部署或发布证据。
- 两次 Workflow 修复派工和一次事件审查派工错误带入 worktree 隔离参数，与主树任务冲突，均撤回。前两次只有契约和目标文件只读定位，第三次零工具调用；没有工具拒绝，也没有有效实现或审查结论。不能把撤回报告当独立审查通过，亦未恢复被拒的 M3/M4 审查。

以上全部生产代码与测试增量后的完整工作树复验已通过：**Python 1630 passed / 36 skipped / 6 deselected，308.42 秒**；全仓 Ruff、**869 个文件格式**、四 Python 工作区严格 **mypy 232 个源码**及 `git diff --check` 全通过。新增十项 PostgreSQL 测试在普通本地入口跳过，已在上述一次性 PostgreSQL 全集中实际通过；36 项跳过和 6 项 live 排除均不算通过。

文档同步后，空 cwd 与清理环境中的真实 CLI 再次通过：96 个事件 / 325 个错误码，99 份正式报告 / 99 份架构门，8 个 Agent / 16 个 Connector / 14 个 Skill，评测集与评测门定义、发布说明和候选契约。此处只校验 38 条评测定义，不是执行 38 条模型评测。Secret 扫描零项匹配；Ruff / 869 文件格式 / `git diff --check` 再次通过。候选明确 `artifact_manifest_validated=false`、`promotion_eligible=false`，6 个操作方门禁仍为 PENDING；零匹配不代表历史凭据已经撤销或轮换。

这是未提交工作树的本地源码/测试证据，随后仅同步验证文档。没有 clean candidate、远程 CI、真实租户或生产签署；M0—M6 与正式生产晋级状态不因此完成。

## 2026-09-06 Git 内容完整性增量复验

新增内部 `git_integrity.py` 及 55 项 Git 正反向测试，见 [ADR 0090](../adr/0090-git-project-content-integrity.md)。首轮 Ruff 一条超长测试字符串失败，mypy/pytest 未执行；修复后新 Git 与既有项目专项 156 passed（0.76 秒）。最后四项预算回归加入后的完整 Python **1685 passed / 36 skipped / 6 deselected，309.50 秒**，全仓 Ruff、**872 文件格式**、四工作区 **mypy 233 源码**及 `git diff --check` 全通过。独立 Git SHA-1/SHA-256 测试实际执行，无 Git skip。

空 cwd、清理环境的 CLI 契约、项目状态、Registry、评测定义/门、发布说明与候选契约通过；Secret 零匹配。计数保持 96 事件 / 325 错误 / 99 报告 / 99 架构门 / 8 Agent / 16 Connector / 14 Skill；38 条为评测定义，不是模型执行。生产候选仍 `artifact_manifest_validated=false`、`promotion_eligible=false`，六个操作方门禁 PENDING。

本次不修改数据库或前端，因此没有重新执行真实 PostgreSQL、迁移或 JavaScript，前轮结果保持独立证据。源码/测试复验后只登记文档。内容 hash 一致不证明仓库身份、应用 scopes、来源 ACL 或任务授权；未开放来源获取、Gateway/Harness 或生产沙箱，未 commit/push/发布/部署。来源定位代理的两次错误 worktree 派工均撤回且零工具调用，不计为独立审查；没有恢复此前被拒的 M3/M4 操作。

## 2026-09-06 来源版本/撤销账本增量

新增内部配置版本、项目来源与独立撤销账本，以及前向迁移 `a84e25f36a47`。完整设计、安全边界与历次失败见 [ADR 0091](../adr/0091-project-source-version-ledger.md) 和 [M2 验证末节](productization-m2-validation.md)。没有公开 API、来源获取或生产授权接线。

首轮 mypy 三处泛型缺参与迁移脚本 `SchemaItem` 导入失败均已修复。最终数据库增量验证使用一次性 PostgreSQL 17.11：来源迁移 1 passed（2.74 秒），通用 integration 100 passed / 9 skipped（9.49 秒，36 SQLite + 64 PostgreSQL），独立 Memory、IM、语义迁移各 1 passed（2.38/3.68/2.34 秒）。完整 upgrade/check、空账本往返与非空降级保留、跨租户 FK、不可变 UPDATE/DELETE/TRUNCATE、错配/漂移及撤销不复活均通过。Memory 测试对全模型的 check 及回滚版本改用当前真实 head，保留旧缺陷复现与全部状态不变量。三次自建临时容器已清理，未修改业务库。

此前中间完整回归 1717 passed / 70 skipped / 6 deselected（318.08 秒）；它不包含收集后增加的五组测试与 flush 修正，因此不作为最终同快照证据。全部源码/测试增量后的最终完整回归为 **1722 passed / 75 skipped / 6 deselected，314.43 秒**；全仓 Ruff、**878 文件格式**、四工作区严格 **mypy 235 源码**及 `git diff --check` 通过。新增的 PostgreSQL 来源/迁移在普通入口显式跳过，实际数据库结果另行记录；不把跳过算通过。之后只同步文档，仍是未提交工作树证据，不是 clean candidate。

实际 CLI 契约、项目状态、Registry、评测定义/门、发布说明/候选通过；Secret 零匹配。OpenAPI 在空 cwd、显式测试配置、不启动 lifespan 的条件下生成，与登记 JSON 完全一致，未覆盖登记文件。六个操作方门禁仍 PENDING，`artifact_manifest_validated=false`、`promotion_eligible=false`。本轮未改前端、未重跑 JavaScript；无提交、推送、发布、部署或远程 CI。新增迁移 CI 配置不代表远程已执行。再次错误 worktree 审查派工已撤回、零工具、无审查结论，不计独立审查。

## 2026-09-06 内部来源 Policy/Audit 管理增量

新增 [ADR 0092](../adr/0092-policy-audited-project-source-management.md) 的内部管理服务、封闭配置快照及 130 项参数化测试；现有身份/资源 Policy 查询重载旧 ORM 缓存，仓库授权谓词可复用但语义不变。复用现有账本与审计，无新 migration，不新增 REST/CLI/SDK/Agent 或源码获取。四个独立 L2 action 必须实际权限和显式 Policy ALLOW，成功、拒绝与幂等重放可审计；事实与审计 SAVEPOINT 原子写入，外层事务由调用者管理。

首轮未使用 import 和 auth 格式门失败已修；SQLite 管理专项首轮 **54 passed / 2 failed / 56 skipped**，缺 created_at 的两项合成 membership fixture 修正后通过。中途因最后环境撤销补测而主动停止的一次完整回归不计通过。最终同源码/测试快照完整 Python **1785 passed / 142 skipped / 6 deselected（334.67 秒）**；Ruff、**882 文件格式**、四工作区 **mypy 237 源码**及 `git diff --check` 通过。

真实一次性 PostgreSQL 17.11 最终通用 integration **230 passed / 9 skipped（34.04 秒）**，准确拆分为 **98 SQLite、131 PostgreSQL、1 纯契约**。其中新管理模块 62 SQLite + 67 PostgreSQL + 1 纯契约；包括 12 路登记/撤销、实际行锁等待与权限/配置/撤销重查，以及审计失败/外层回滚。来源、Memory、IM、语义四独立空库迁移各 **1 passed（3.00/2.62/3.36/2.38 秒）**。本增量两次自建容器均清理，无业务数据或真实凭据。详细中间结果与失败保留在 [M2 验证](productization-m2-validation.md)。

CLI 契约、状态、Registry、评测定义/门、发布说明/候选与 OpenAPI 一致性通过；Secret 零匹配不等于历史凭据轮换。142 skip 含新增 67 项 PostgreSQL opt-in，不能相加伪造完整通过；六个操作方门禁仍 PENDING，promotion false。本轮不改前端、未重跑 JavaScript，无提交/外发/发布/部署。定位派工再次误带 worktree，代理停止前执行只读搜索且误遍历部分隔离树路径，无有效定位/独立审查结论；主会话未继续读取隔离路径。最终回归后只同步文档，仍非 clean candidate，任务8/M2及生产晋级未完成。

## 2026-09-06 来源管理 REST 与 SQLite 事务边界

新增 [ADR 0093](../adr/0093-governed-project-source-rest.md) 的四个受保护管理 POST，复用真实认证、权限/Policy/ACL、来源账本和审计；封闭输入、最小元数据输出、生产拒绝。三个新增错误码使目录为 328，OpenAPI 只追加四路径和四模型，既有契约不变。无新数据库结构或迁移，不联网、不解析凭据、不开放源码/Agent 能力，也不证明厂商身份/实际 scopes。

历次失败、精确计数与修复见 [M2 本轮记录](productization-m2-validation.md#2026-09-06-受治理来源-rest-与真实事务修复)：合成 Grant 字段修正、APIRouter inline ErrorBody 识别红测及最小分析器修复、main.py 转发行号同步均保留精确门禁。改用真实 Database 后，提交前失败红测发现 SQLite SAVEPOINT 提前提交；最初所有事务显式 BEGIN 虽通过来源专项，却引入既有并发 Run 的 database locked。该次完整回归已主动停止，不计通过；单独 Automation/IM 32 项通过不抵消失败。最终修复仅在 SQLite SAVEPOINT 事件按真实驱动状态补 `BEGIN IMMEDIATE`，保留普通读取语义及 PostgreSQL 路径，不重试工具副作用。

新增六项直接 Database 回归与来源 REST/既有 Automation/IM/发布加固组合 **141 passed / 71 skipped（91.64 秒）**。71 组 REST SQLite/PostgreSQL 双参数不启动 lifespan/worker，使用真实 ASGI、共享认证、运行时 Database、Policy/Audit 与独立 Session；测试仅额外启用 SQLite FK。前两轮一次性 PostgreSQL 各有通用 integration 372 passed / 9 skipped 和四独立迁移通过，容器已清理，但它们不覆盖最后的 SAVEPOINT 收窄修改，最终同快照结果另记。

本轮不改前端、未重跑 JavaScript，前轮 258 项不是本轮执行。两次错误 worktree 派工均撤回，停止前分别 9 次主树只读 Bash、8 次主树 Read，输出弃用，无有效独立审查；没有恢复被冻结 M3/M4 操作。真实 scopes、Run commit/tree pin、来源获取与 ACL 传播、Gateway/Harness、项目传输、恢复配额和真实集群仍缺，任务8/M2及生产晋级不完成。

最终 SAVEPOINT 方案后的第三轮一次性 PostgreSQL 17.11：通用 integration **372 passed / 9 skipped（107.69 秒）**，**169 SQLite + 202 PostgreSQL + 1 纯契约**；来源/Memory/IM/语义四独立迁移各 **1 passed（2.66/2.41/3.36/2.41 秒）**，含 head/check、往返和非空账本保护。本 REST 切片三次临时容器均已按 ID/标签确认清理，无业务数据挂载、真实凭据或外部 Git 请求。

最终所有源码/测试增量后的完整 Python **1868 passed / 213 skipped / 6 deselected（382.92 秒）**；全仓 Ruff、**887 文件格式**、四工作区严格 **mypy 239 源码**及 `git diff --check` 通过。相对前轮新增 71 项本地 REST、6 项静态分析、6 项直接 Database 回归；新增 71 项 PostgreSQL opt-in 在普通入口跳过，真实执行结果单列。之后只同步验证文档，仍非 clean candidate。

CLI 实际验证通过：96 事件/328 错误、99 报告/99 架构门、8 Agent/16 Connector/14 Skill、3 数据集/38 条评测定义与评测门、发布说明和候选契约；OpenAPI 一致性在完整质量门中通过。Secret 零匹配，不等于撤销/轮换或 Git 历史清理；六个操作方门禁仍 PENDING，`artifact_manifest_validated=false`、`promotion_eligible=false`。没有真实模型评测、前端复跑、提交、推送、发布、远程 CI、外发或部署。本地 REST 切片完成不关闭任务8/M2或生产晋级门禁。
