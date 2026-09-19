# ADR 0143：建立可比较的 Harness 候选基线

状态：Accepted（H01 工程与评测基线增量）
日期：2026-09-19

## 问题

既有 P1 Acceptance Profile 能冻结数据集、镜像声明、模型/Agent 标识和部分管理配置，
但没有把 Profile 绑定到候选提交，也没有同时核对 API 实际安装包、Worker 文件数量、
连接器执行配置和独立评测策略。相同 Profile 因而可能被另一个提交复用，API 与 Worker
混装也可能只在任务执行后才暴露。连接器健康字段又是瞬态状态，不能直接作为可比版本。

旧 24 例真实知识基线只有原因级公开记录。若把 `independent_judgment_invalid` 直接解释为
具体引用错误，或在新候选上覆盖原成绩，会制造不存在的诊断精度。当前 JavaScript 工作区
还允许 lint 在 TypeScript SDK 尚未生成声明文件时启动，使干净 CI 与带残留构建物的本机
结果不一致。测试设置也会读取开发者 `.env` 中的模型白名单，破坏测试隔离。

## 决策

### 候选 Profile v2

保留 Profile v1 的字段和序列化语义。对包含控制面源码包的候选根目录，`acceptance freeze`
生成 v2，并强制冻结：

- 完整 40 位候选提交；
- API 与 Worker 的 Python/JSON 安装包 SHA256 和文件数；
- 镜像 digest；
- 模型 Profile、Agent、Prompt、Capability、Skill、Policy 等管理配置摘要；
- 内容不外泄的连接器执行配置摘要；
- 独立答案评测策略 SHA256。

Runner 拒绝用 v2 Profile 验收另一个提交。开始、结束和每个 Run 的实际 Worker 观察继续
分别检查，任何代码、镜像、配置或恢复执行漂移均先 BLOCKED，不能沿用旧成绩。外部工具
构造的不含源码根目录继续得到 v1，历史 Profile 无需改写。

### API 与连接器观察

API 在 lifespan 初始化时观察自己的安装包一次，`runtime-identity` 返回包摘要、文件数和
明确的 `installed_package_at_api_initialization` 标识；`signature_verified=false` 保持
事实，不把内容观察冒充签名证明。

新增管理员只读的连接器配置快照。摘要覆盖类型、状态、环境、Endpoint、配置、凭据引用、
声明授权、出站范围、SPI 支持及插件声明；响应只返回连接器 ID、名称、状态和 SHA256，
不返回 Endpoint、配置、凭据引用或健康结果。健康探测不会造成候选漂移。Connector 配置
仍禁止敏感键和 URL 内嵌凭据，真实凭据继续仅由 Credential Broker 解析。

### 失败诊断与历史分母

新增固定分类：`SOURCE`、`SEMANTIC`、`CITATION`、`MODEL_PROTOCOL`、`ENDPOINT`、
`RESOURCE`、`FRESHNESS`，另保留 `UNCLASSIFIED` 防止未知原因被猜测。优先采用独立评分
返回的有界 machine diagnostic；没有细因时只按稳定 reason 分类。PASS 不进入失败统计。

旧 24 例文件保持原字节与 5 PASS / 1 FAIL / 18 BLOCKED 分母。H01 诊断账本仅引用其
SHA256 并建立原因级投影。旧文件没有 `judgment_diagnostic`，所以 7 条
`independent_judgment_invalid` 只能暂列 `MODEL_PROTOCOL`，不能声称已确认引用根因。

### 工程门与真实案例

根工作区的 lint、typecheck、test 和 build 在执行前统一构建 TypeScript SDK，消除对忽略
目录 `packages/sdk-ts/dist` 的偶然依赖；检查范围与断言不缩减。测试 Settings 禁止读取
仓库 `.env`，并显式不继承本机模型 ID 白名单。

业务定位、精确统计、隔离代码执行三类首批纵向案例仅在仓库中保存合同和所需回执。
问题、真实数据、凭据与 gold 留在受控保留集；至少双人标注、记录访问，诊断过的案例不得
重新宣称未见。没有真实来源或执行环境时记 `NOT_RUN/BLOCKED`，禁止 Mock 顶替。

### 发布镜像安全基线

远端完整发布任务在构建和候选校验通过后，由原有 Trivy 门发现旧 Python/Bookworm 运行层
已积累高危与严重漏洞。保持 `HIGH,CRITICAL`、`ignore-unfixed=false` 和 secret scanner
不变，不增加忽略清单。控制面改用 digest 固定的 Python 3.12.14/Alpine 3.24；Web 改用
digest 固定的 Node 22.23.2/Alpine 3.24，并从最终 standalone 运行层删除不参与服务执行的
npm、Corepack 与 Yarn。构建阶段仍保留 npm，运行阶段只保留 Node 与 standalone 依赖。

基础镜像版本与 digest 由仓库测试固定，升级必须显式评审。最终两个镜像均用 CI 同版本
Trivy 0.70.0、同严重级别和不忽略未修复项的参数验证为零漏洞、零秘密；这是一项候选时点
观察，不代替后续持续扫描、镜像签名或生产证明。

## 兼容、安全与限制

- 不修改数据库 schema、既有 Run/Event 或历史 Acceptance Profile；无需数据迁移。
- 不扩大生产读取、写入、模型或连接器权限；所有快照接口仍需管理员权限。
- 包摘要和配置摘要用于发现漂移，不证明构建签名、硬件远程证明或内存代码完整性。
- 运行镜像以 digest 复现；Web 最终层不携带构建期包管理器，扫描策略不因候选失败而放宽。
- H01 不证明真实问答质量改善，也不使 P1、Phase 99 或生产晋级通过。
- 三类保护案例在没有授权资料时保持未执行，后续 H19 的 60 例校准集和 240 例独立保留集
  仍是发布门。

## 迁移、回滚与验证

数据库迁移：无。回滚代码时，Profile v1 和历史报告仍可读取；Profile v2 应保留为不可变
证据并因旧运行时缺字段而失败关闭，不能降级成 v1 后重跑。连接器快照路由可以随代码
回滚而移除，但已经冻结的 v2 Profile 不得篡改。

验证覆盖候选提交复用拒绝、API/Worker 包漂移、连接器配置内容不外泄、旧 Profile 字节
兼容、七类诊断与未知保留、旧 24 例 SHA/分母、保护案例无正文、`.env` 隔离以及干净
JavaScript 工作区完整链。执行数量和真实回执记录在 H01 阶段报告，不在 ADR 中用计划值
冒充结果。
