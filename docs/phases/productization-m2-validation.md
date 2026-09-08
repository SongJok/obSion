# M2 沙箱与项目副本本地验证

日期：2026-09-06

状态：**进行中；仅本地切片验证，不是 M2 完成、真实集群或生产验收声明。**

## 实现范围

- 内部 `sandbox/contracts.py`、`kubernetes.py`、`entrypoint.py`：真实 HTTP 协议实现、默认关闭、固定镜像与资源、UNKNOWN 对账、UID 条件清理和可信进程入口。设计与环境前置条件见 [ADR 0084](../adr/0084-kubernetes-sandbox-http-slice.md)。
- 内部 `sandbox/project.py`、`workspace.py`、`delivery.py`：有界文本副本/manifest/归档、真实目录 fd 文件操作、补丁及 `NOT_EVALUATED` 交付清单。设计与局限见 [ADR 0088](../adr/0088-bounded-project-copy-delivery.md)。
- `release/hardening.py` 仅提取纯文本扫描复用，保留既有仓库扫描语义；不把规则匹配称为完整秘密检测。
- 上述前期切片无新数据库、公开 API、Agent 能力、Gateway/runtime 接线或镜像发布，因此无需迁移；后续来源账本的前向迁移单列于末节。

## 已执行的本地证据

| 验证 | 结果 | 证据边界 |
| --- | --- | --- |
| 独立 Kubernetes/入口切片 | 102 passed，2.33 秒 | 实现代理隔离树，MockTransport/受控本地进程；无真实集群 |
| 合入主树 sandbox + CI/status 专项 | 115 passed，2.67 秒 | 不含之后的项目副本切片 |
| 主树项目副本 + 文档 Secret 专项初轮 | 112 passed，0.38 秒 | 合成项目，无真实仓库 |
| 沙箱/项目/扫描/加固/CI/status 合并专项 | 261 passed，21.29 秒 | 此时尚未加入最后的目录互换等补充测试 |
| 项目副本补充专项 | 104 passed，0.43 秒 | 包含两项真实 Git apply 检查和实际应用，无跳过 |
| 完整源码 mypy | 232 个源码通过 | macOS 静态检查，不是 Linux 隔离证明 |
| 全仓 Ruff / format / git diff --check | 通过，862 个文件符合格式 | 后续文档及归档目录预检增量另行复验 |

最后增加 ZIP 中央目录实际条目数的分配前检查及一项回归后的完整 Python 为 **1594 passed / 2 failed / 26 skipped / 6 deselected，298.01 秒**，不是通过。JavaScript 四工作区 **258 passed**，lint/typecheck/build 全通过。

其中项目 `object.__setattr__` 触发分析器未绑定方法参数误读，保留 Error 写入拒绝并补正反向测试后，静态/契约/项目合并 **240 passed，28.29 秒**。另一个既有 Workflow 事件并发序号失败已定位并修复，事件/Workflow/契约专项 **38 passed，36.08 秒**，一次性 PostgreSQL 通用集成 **27 passed / 7 skipped，6.72 秒**，迁移及漂移检查通过、容器已清理。修复后的完整工作树复验为 **Python 1630 passed / 36 skipped / 6 deselected，308.42 秒**；全仓 Ruff、869 个文件格式、mypy 232 个源码及 `git diff --check` 通过。详见 [M0 增量记录](productization-m0-validation.md) 与 [ADR 0089](../adr/0089-atomic-event-sequence-allocation.md)。普通入口跳过项与真实 PostgreSQL 专项分开记录，不相加伪造全通过；本地结果不改变 M2 未完成结论。

## 验证方法

- Python 通过临时空工作目录，清除继承 `OBSION_*`/`PYTEST_*`，关闭插件自动加载并显式 asyncio；完整入口使用 `scripts/test_local.py`。没有读取仓库 `.env`/`ci.txt`。
- Kubernetes 使用有状态 `httpx.MockTransport` 服务端，不替换 adapter 的实现；验证协议、拒绝分支、丢响应与取消不伪造成功。
- 文件操作仅使用临时合成目录；真实测试 symlink/hardlink/FIFO 越界拒绝，不访问真实密钥或仓库。
- 两项补丁验证在临时目录执行真实 `git apply --no-index --check` 和 `git apply --no-index`，禁用 Git 全局/系统配置与交互，逐项核对内容和模式；未 init、commit、联网或执行合成源代码。
- Git 可执行文件缺失会显式 skip，不能算通过；上述本地运行未跳过。

## 已修复失败与限制

- 主树入口平台检查曾使 macOS mypy 判定 Linux 分支不可达；拆出非 root/Linux 前置函数后类型及测试通过，没有删除防护或屏蔽错误。
- 新增项目文件首轮存在格式、显式 UUID 构造类型问题；修复后完整静态门通过。一轮检查与局部缓冲区重构重叠读到未定义旧变量，已修复并重新执行；失败不计通过。
- 不接受凭据路径的策略比较保守，包括 `.env.example`；不支持二进制/符号链接/submodule/LFS 自动下载，也不静默删文件后声称完整仓库。
- `ProjectWorkspace` 要求独占且静止的目录，不保证抵抗在同目录运行的恶意并发项目进程，也不承担 cgroups、quota、网络隔离或任务授权。
- patch 可以很大，使用整文件替换而非最小 diff；适用性测试不代表行为正确性或测试通过。

## 未完成的验收

真实授权仓库获取及其 Git 对象核验接线、Kubernetes 输入/输出传输、产物来源 ACL/分级传播、Gateways/Harness 接线、持久实例和 quota/fencing/sweeper、真实 gVisor/CNI 攻击集与容量、真实项目闭环均未完成。内部纯内容核验的后续增量见下节，不等于可信来源链已接通。详细门禁见 [M2 架构门](../architecture/productization-m2-gate.md)。

无提交、推送、发布或部署；M3/M4 隔离实现不计为主树能力，原正式阶段与生产晋级阻塞保持不变。

## 2026-09-06 Git 对象完整性前置切片

新增 [ADR 0090](../adr/0090-git-project-content-integrity.md) 与内部 `git_integrity.py`，复用 ProjectSnapshot 对原始 commit、完整文件 blob/tree、文件名与 mode 做 Git SHA-1/SHA-256 校验；不联网、不读取宿主仓库、不执行过滤器或项目代码。拒绝 LFS pointer、pin 不一致、缺失/额外/改名/内容与模式变化，不把 hash 一致当仓库身份或权限凭证。

首轮 Ruff 因一条测试字符串超过 100 列失败，mypy/pytest 未执行。修复后的 Git 完整性与既有项目副本专项 **156 passed，0.76 秒**，无跳过；目标 Ruff 与新模块 mypy 通过。其中两项独立 Git plumbing 验证在自建空临时 bare 对象库执行 `hash-object --no-filters` / `mktree -z`，确实覆盖 SHA-1 与 SHA-256、目录排序和中文模式，不是复用生产 hash 逻辑的替身。没有远端、ref 更新或宿主 Git 配置读取。

随后增加四项 snapshot 预算在 hash 前重新检查的回归，新增 Git 专项共 55 项均进入最终完整 Python：**1685 passed / 36 skipped / 6 deselected，309.50 秒**。全仓 Ruff、**872 个文件格式**、四 Python 工作区严格 **mypy 233 个源码**及 `git diff --check` 通过。36 项跳过与 6 项 live 排除不计通过；本增量不改数据库/前端，没有重新运行真实 PostgreSQL 或 JavaScript，前轮证据不能冒充本轮执行。

本轮清理环境、空 cwd 的实际 CLI 检查通过：96 个事件 / 325 个错误码、99 份正式报告 / 99 份架构门、8 个 Agent / 16 个 Connector / 14 个 Skill、38 条评测定义及评测门、发布说明和候选契约。Secret 扫描零匹配，不代表历史凭据已轮换。候选仍 `artifact_manifest_validated=false`、`promotion_eligible=false`，6 个操作方门禁为 PENDING；不是生产晋级、模型评测执行或真实来源验收。完整源码与测试通过后只同步文档。

来源定位确认三项尚未接通的边界：Code Graph commit 是调用者元数据；Connector 没有独立不可变版本模型；Artifact lineage 为调用者 JSON，下载仅检查 Workspace。后续须实现可信来源绑定/版本 pin 持久化、应用实际 scopes、来源 ACL/撤权与 Gateway 受限获取。当前没有公开新能力或迁移，授权项目获取任务保持未完成。

本段来源定位两次错误附带 worktree 参数，与主树范围冲突，均撤回且代理零工具调用；无定位或独立审查结论。实际定位、实现与测试由主会话完成，未恢复此前被拒的 M3/M4 审查。

## 2026-09-06 来源版本与撤销账本前置切片

新增 [ADR 0091](../adr/0091-project-source-version-ledger.md)、`db/project_source_models.py`、`sandbox/source_state.py` 与前向迁移 `a84e25f36a47`（父 `a83d14e25f36`）。四张不可变配置版本/项目来源/版本撤销/来源撤销表，使用同租户复合 FK 和固定绑定唯一约束。PostgreSQL 禁止所有字段 UPDATE、DELETE 及 TRUNCATE；非空账本禁止降级，不把旧 Connector 自动回填为可信来源。

内部状态检查按明确组织/Workspace/仓库/版本重新读取数据库，检查归档、删除、禁用、撤销、环境及配置漂移。查询前显式 flush，不让待写撤销被 Core 查询遗漏；不使用 ORM 旧缓存，也不跟随 latest。成功不授予权限、不验证 commit/tree 或应用实际 scopes、不返回源码和凭据。

### 已执行数据库验证

- 首轮 mypy 三处泛型缺参已补齐。首次 PostgreSQL 迁移测试因 `SchemaItem` 误从 SQLAlchemy 顶层引用，在加载脚本时失败（1 failed，0.30 秒），迁移 SQL 未执行；改为 `sqlalchemy.schema.SchemaItem` 后通过。首个清理断言早于 Docker 自动移除完成，随后按本次容器 ID 核对无残留；后续脚本等待自动移除完成。
- 第一次成功：PostgreSQL 17.11 来源迁移 **1 passed，2.83 秒**；通用 integration **90 passed / 9 skipped，8.75 秒**，其中 SQLite 来源查询 31、PostgreSQL 来源 32、既有 PostgreSQL 27，不把全部 90 项称为 PostgreSQL；独立 Memory/IM/语义迁移各 1 passed（2.62/3.73/2.10 秒）。
- 增加同租户错配、重复撤销/配置恢复以及待写撤销覆盖后复验：来源迁移 **1 passed，2.74 秒**；通用 integration **100 passed / 9 skipped，9.49 秒**，包含 **36 SQLite 来源 + 37 PostgreSQL 来源 + 27 既有 PostgreSQL**。九项跳过为八项独立迁移开关和一项 SQLite 不支持触发器，不算通过。独立空库 Memory/IM/语义迁移各 **1 passed（2.38/3.68/2.34 秒）**。
- 迁移覆盖旧配置/ACL 保留、两轮空账本 downgrade/re-upgrade、完整 head/check、仅版本事实就拒绝降级、已有来源及双撤销后降级失败仍保持 head 和内容。所有不可变字段/删除/TRUNCATE 断言 SQLSTATE `23000`，跨租户断言 `23503`，固定绑定及重复撤销断言 `23505`，不用其他约束失败冒充通过。
- Memory 迁移测试改为真实当前 head 上做全模型漂移检查与失败回滚断言；保留旧触发器缺陷复现、全部合法/非法状态转换与撤销记录保留检查。新来源迁移加入 CI 独立数据库矩阵并有配置回归；远程 CI 未执行。
- 三次自建容器均使用本地已有 pgvector 镜像、tmpfs、随机 loopback 端口、纯合成凭据、无业务挂载；空 cwd/清理环境，不读取 `.env`/`ci.txt`，已全部清理。

较早启动的完整 Python 回归 **1717 passed / 70 skipped / 6 deselected，318.08 秒**，Ruff/878 文件格式/mypy 235 源码通过；该轮在收集后又补五组测试及 flush 修正，不能作为最终同快照证据。全部源码/测试增量后的最终完整回归 **1722 passed / 75 skipped / 6 deselected，314.43 秒**；全仓 Ruff、**878 文件格式**、四工作区 **mypy 235 源码**与 `git diff --check` 通过。75 项跳过含新增 37 项 PostgreSQL 来源、1 项来源迁移和1项 SQLite 触发器跳过；真实数据库结果已另行记录，不能将跳过算通过。实际 CLI 已通过：96 事件/325 错误、99 报告/99 架构门、8 Agent/16 Connector/14 Skill、38 条评测定义与门、发布说明及候选契约；Secret 零匹配不等于历史凭据已轮换，`artifact_manifest_validated=false`、`promotion_eligible=false`，六个操作方门禁 PENDING。本切片已完成本地验证，只关闭来源持久化前置任务；授权获取任务和 M2 仍进行中。本轮不改前端，未重跑 JavaScript。

仍缺管理面可信版本创建/撤销的 Policy 与审计、厂商身份与实际 scopes、Run commit/tree pin、来源获取、ACL/受众传播、Gateway/Harness/项目传输与真实集群验收。ORM 与合成 fixture 写入不是生产管理流程，不是 M2 完成。

本轮独立审查再次误带 worktree 参数，已撤回且代理零工具执行，没有审查结论；不得计为独立审查通过，也未恢复 M3/M4 被拒操作。

## 2026-09-06 内部来源管理服务增量

新增 [ADR 0092](../adr/0092-policy-audited-project-source-management.md)、`application/project_sources.py` 与 `sandbox/source_configuration.py`。复用四张来源账本及现有 Policy/Audit，无新迁移。四独立 L2 action 要求数据库当前主体权限、显式 ALLOW 且无未处理义务；默认 MASK、ASK、DENY 均拒绝。角色/资源 Policy 查询重载 ORM 旧缓存，Code Graph SQL 授权谓词仅改为公开可复用名称，原授权语义不变。

配置版本从锁定同组织 Connector 获取封闭 git-http 最小声明，拒绝任意调用者配置、额外键、宽泛 grants/egress、URL 凭据和直接 env 引用；只检查同组织 SecretReference 名称存在，不解析值。登记重查 Workspace 写访问、仓库 ACL、固定版本环境、配置漂移与撤销，返回和审计仅内部 UUID/固定状态，不含配置或厂商仓库标识。事实/Policy/Audit 在同一 SAVEPOINT，调用者拥有外层提交；可预期拒绝返回结果以保留拒绝审计，外层回滚仍会回滚全部。没有认证/API 入口，未知组织由未来认证边界另行审计。

### 数据库与失败记录

- 首轮 Ruff 发现未使用 UUID import，已移除；mypy 后通过。首轮 SQLite 管理专项 **54 passed / 2 failed / 56 skipped，13.95 秒**，两项因合成 WorkspaceMember 缺 created_at，已补齐 fixture，不修改生产字段约束。
- 第一次一次性 PostgreSQL 17.11：来源迁移 **1 passed，2.77 秒**，通用 integration **216 passed / 9 skipped，28.56 秒**；Memory/IM/语义独立空库各 **1 passed（2.54/3.54/2.19 秒）**。这是跨环境撤销与无事务补测前的中间证据。
- 补齐固定版本环境撤销检查及七组用例后的数据库 integration **230 passed / 9 skipped，34.04 秒**：**98 SQLite + 131 PostgreSQL + 1 纯契约测试**，不把总数全称为 PostgreSQL。新管理模块共 130 项，其中 62 SQLite、62 PostgreSQL 同类场景、5 PostgreSQL 专项、1 纯契约；包括 12 路并发登记/12 路并发撤销各只有一条事实，以及通过 pg_blocking_pids 确认实际等待行锁期间的配置、Policy、角色权限和版本撤销变化后拒绝。
- 最终独立空库来源迁移 **1 passed（3.00 秒）**，Memory/IM/语义各 **1 passed（2.62/3.36/2.38 秒）**，包含 upgrade/check、往返及非空账本保护。两次本增量自建容器都已精确按 ID/验证标签停止并确认自动移除，无业务挂载或残留容器。
- 新增管理测试由既有 CI integration 集合自动发现，不增加新的迁移开关或数据库结构。真实数据只来自本次合成容器，没有读取 .env/ci.txt、解析 Secret 或连接厂商。
- 第一个全量入口在 auth.py 格式门失败，未执行 pytest；修正后又因补充最后源码/测试主动停止了一次中间回归，不计通过。最终全部源码/测试增量后的完整 Python 为 **1785 passed / 142 skipped / 6 deselected，334.67 秒**，Ruff、**882 文件格式**、四工作区 **mypy 237 源码**及 `git diff --check` 全通过。新增 67 项 PostgreSQL opt-in 在普通入口跳过，已在上述真实数据库集合中执行通过，不把跳过算通过。随后只同步文档，仍是未提交工作树证据，不是 clean candidate。

CLI 实际检查已通过：96 事件/325 错误、99 报告/99 架构门、8 Agent/16 Connector/14 Skill、38 条评测定义及评测门、发布说明和候选契约。OpenAPI 与登记 JSON 完全一致；Secret 扫描零匹配，不表示历史凭据已经轮换。`artifact_manifest_validated=false`、`promotion_eligible=false`，六个操作方门禁 PENDING。没有前端源码变化，本增量未重跑 JavaScript，也未提交、外发、发布或部署。

本次配置定位派工再次误带 worktree 参数，已停止。代理实际执行了主树只读列表/搜索，并因路径排除遗漏遍历了部分隔离树文件路径；未读 .env/ci.txt 内容、未写文件、未测试、未联网。没有有效定位结论，也不是独立审查通过；主会话未继续读取这些隔离路径，不恢复被冻结的 M3/M4 操作。

管理内部服务不等于完整管理产品入口，仍缺厂商身份/实际 scopes、Run commit/tree pin、来源获取与 ACL/受众传播、Gateway/Harness、Kubernetes 项目传输、恢复/配额和真实集群验收；任务8/M2 保持进行中。

## 2026-09-06 受治理来源 REST 与真实事务修复

新增 [ADR 0093](../adr/0093-governed-project-source-rest.md)：现有受保护 `/api/v1/admin/project-sources` 下四个 POST，复用共享认证、当前主体权限、显式 L2 ALLOW、来源状态重查与 Policy/Audit 服务。三请求模型拒绝额外字段，成功仅返回状态、记录和决策 UUID；生产环境依赖层拒绝。不解析凭据、不联网、不暴露 Agent 管理工具，不宣称厂商身份、实际 scopes 或源码已验证。新增三个稳定错误码，目录总数 328；OpenAPI 只追加四路径、四模型，写前逐项验证旧路径/模型不变。无新 schema 或迁移。

### 失败复现与根因修复

- REST/契约首轮 **73 passed / 2 failed / 72 skipped（47.54 秒）**：合成 CodeRepositoryGrant 错用 `subject_id`，以及静态分析器拒绝模块级 ErrorBody 引用。修正 Grant 的 `subject_value`/`created_at`，把路由响应声明放入构造函数后，第二轮 **75 passed / 1 failed / 71 skipped（39.60 秒）**；APIRouter 仍被误判，不能把局部修正称为解决。
- 分析器对 FastAPI/APIRouter 增加相同正反向参数化，红测 **1 failed / 11 passed / 124 deselected（0.27 秒）** 复现合法 APIRouter inline response model 的误报。仅补规范 APIRouter 构造器识别；模块级映射、别名逃逸、非 model 键和构造器遮蔽仍拒绝，不忽略来源路由或放宽未知错误码。
- 随后专项 **211 passed / 1 failed / 71 skipped（63.40 秒）**：main.py 新 import 使精确 forwarding manifest 的 `exc.code` 行号由 391 变为 392，已据实际代码同步，保留精确 origins/forwarding/helper 检查。
- 初版 SQLite REST fixture 自行显式 BEGIN，不能证明实际运行时。改用真实 `Database`，移除测试专用事务配置后，提交前故障红测 **1 failed / 141 deselected（0.78 秒）**：HTTP 已返回 500，但独立 Session 仍看到版本、PolicyDecision、Audit 各一条。根因是 SQLite legacy 模式下 SAVEPOINT 先于真实 BEGIN 并独立提交。首次尝试在运行时 Database 仅对 SQLite 禁用驱动隐式事务，并由 SQLAlchemy begin 事件显式 BEGIN；该方案虽通过来源专项，但后续全量出现并发锁回归，已收窄，见下文。测试只额外开启 SQLite FK，不再替换事务实现。
- 修复后静态分析/精确契约/REST 专项 **212 passed / 71 skipped（60.97 秒）**，包括 136 项静态分析、5 项质量门、71 项实际 SQLite REST；另 71 项 PostgreSQL opt-in 跳过单列。全仓 Ruff、**886 文件格式**、四工作区 **mypy 239 源码**和 `git diff --check` 通过。

71 组双数据库 REST 场景使用真实 ASGI、共享 Cookie/Bearer 认证、Origin、会话撤销、数据库权限/Policy/ACL、运行时 Database 和独立 Session。覆盖四动作成功/幂等/冲突/撤销、权限与 im.delegate-only 拒绝、默认 MASK/obligations 拒绝、跨租户/缺失/ACL/配置污染、认证失败、额外授权字段、封闭仓库 ID、Audit 失败、外层提交前故障与生产拒绝。预期业务拒绝必须先提交审计再返回 403/422/409；500 不回显内部输入。提交前故障回滚不代表网络丢失后提交结果必定失败，后者须先对账，不盲重试版本创建。

第一轮一次性 PostgreSQL 17.11 使用测试专用 engine，通用 integration **372 passed / 9 skipped（104.00 秒）**，来源/Memory/IM/语义四个独立迁移各 **1 passed（2.79/2.53/3.59/2.18 秒）**，容器已精确按 ID/标签清理。它发生在改用运行时 Database 前，不作为最终同源码/测试快照证据；最终复验另记。

第二轮一次性 PostgreSQL 17.11 改用运行时 Database 后，通用 integration **372 passed / 9 skipped（98.00 秒）**，来源/Memory/IM/语义独立迁移各 **1 passed（2.83/2.62/3.72/2.23 秒）**，容器已清理。随后完整 Python 出现既有 Automation/IM/并发 Run 的失败及 teardown 错误；相关 Automation/IM 单独 32 项通过不抵消全量失败。发布加固专项稳定复现 **27 passed / 1 failed（13.48 秒）**：并发 Run 更新报 `sqlite3.OperationalError: database is locked`。全量已主动停止，无完整终态，不计通过。

根因是对所有 SELECT 提前开启 SQLite 事务引入了读锁升级冲突。最终改为 Database 的 SAVEPOINT 事件按驱动 `in_transaction` 补 `BEGIN IMMEDIATE`，已有 DML/嵌套事务不重复开启，普通读取保留原 legacy 行为。新增六项直接 Database 回归覆盖提交/回滚、先前写入、嵌套回滚/连续 SAVEPOINT 和普通读取与独立写入兼容。首轮因两条测试超长行 Ruff 失败，pytest 未执行；格式化后重新验证，不删并发测试、不重试工具副作用。

本切片两次派工错误附带 worktree 参数，均已撤回。第一次代理停止前有 9 次主树只读 Bash 定位；第二次有 8 次主树 Read；全部输出弃用，无有效独立审查结论。没有恢复被冻结的 M3/M4 操作，也未读取 .env/ci.txt 内容、联网、外发、提交或部署。

### 收窄修复后的最终验证

- 六项直接 Database 回归、来源 REST、既有 Automation/IM/发布加固组合 **141 passed / 71 skipped（91.64 秒）**，包括曾失败的并发问候 Run；不把单轮重跑当对先前失败的豁免。
- 第三轮一次性 PostgreSQL 17.11，使用最终源码与真实 Database：通用 integration **372 passed / 9 skipped（107.69 秒）**，准确拆分为 **169 SQLite + 202 PostgreSQL + 1 纯契约**；新 REST 模块为 71 SQLite + 71 PostgreSQL，不把全部 372 项称为 PostgreSQL。九 skip 为八项独立迁移开关、一项 SQLite 触发器不适用，审计迁移按既有 CI 通用入口排除。
- 四独立空库迁移：来源 **1 passed（2.66 秒）**，Memory **1 passed（2.41 秒）**，IM **1 passed（3.36 秒）**，语义 **1 passed（2.41 秒）**，含完整 head/check、往返和非空账本降级拒绝。本 REST 切片共三次自建容器均精确按 ID/标签确认清理，tmpfs、随机 loopback 端口、纯合成凭据，无业务挂载。
- 全部源码/测试增量后的完整 Python **1868 passed / 213 skipped / 6 deselected（382.92 秒）**；全仓 Ruff、**887 文件格式**、四工作区 **mypy 239 源码**及 `git diff --check` 全通过。213 skip 含本轮 71 项 PostgreSQL opt-in，不能与实际数据库结果相加冒充零跳过。本轮新增 71 项本地 REST、6 项分析器、6 项 Database 回归，实际通过数相对前轮增加 83。

本轮实际 CLI 契约/状态/Registry/评测定义与门/发布说明/候选检查通过：96 事件、328 错误、99 报告/99 架构门、8 Agent/16 Connector/14 Skill、3 数据集/38 条评测定义。不是 38 条模型评测执行；Secret 扫描零匹配不代表历史凭据已轮换。六个操作方门禁仍 PENDING，`artifact_manifest_validated=false`、`promotion_eligible=false`。OpenAPI 一致性纳入最终质量门。本轮不改前端、未重跑 JavaScript，最终源码/测试后只同步文档，仍是未提交工作树证据，不是 clean candidate、远程 CI、部署或真实租户验收。

只关闭受治理 REST 管理前置任务。任务8/M2仍缺实际 scopes、厂商身份、Run commit/tree pin、来源获取/ACL传播、Gateway/Harness、项目传输、实例/配额/恢复与真实集群验收；不开放生产来源管理，不自动提交、推送、外发或部署。
