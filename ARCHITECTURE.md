# Obsion Architecture Constitution

本文档是 Obsion 的架构宪法和 Phase 门禁依据。任何实现、评审、自动化任务或 AI 编码任务都必须携带并遵守本文档；违反任意一条，当前 Phase 判定失败并回滚相关增量。

## 产品与运行时边界

Obsion 是 Enterprise Intelligence Workspace 与 Enterprise Agent Harness。系统的持久化领域层级为：

```text
Workspace → Thread → Turn → Run → Step → Event
```

运行时不是单一模型调用，而是：

```text
Harness + Context + Capability + Policy + Memory + Evidence
```

Event 是唯一运行时协议。REST、WebSocket、JSON-RPC、SDK 和 UI 只能投影 Event Store 中的事实，不得建立第二套消息或执行轨迹模型。

## 不可违反的架构规则

1. **Agent ≠ Model。** 运行时是 Harness + Context + Capability + Policy + Memory + Evidence。禁止在 Agent 中写死模型名。
2. **模型只走 Model Gateway。** Agent 只绑定 `ModelProfile`，例如 `reasoning-high`、`fast`、`private`。
3. **Agent 永不直连生产资源。** 所有外部访问必须遵循 `Agent → Capability Gateway → Policy → 执行器`。
4. **MCP 是传输，不是架构。** Capability 可以挂载 MCP、HTTP、gRPC、SQL Proxy；Agent 只认 Capability ID。
5. **每个结论必须可归因。** 结论必须包含 Claim + Evidence + Confidence + Source + Timestamp；禁止无证据的“根据分析”。
6. **每个 Run 必须可 Replay。** 必须保存用户输入、上下文、计划、工具调用、结果、证据、审批、重规划和最终回答。
7. **权限只由 Policy Engine 决定。** Prompt 中的“不要 DELETE”不是安全机制；裁决只有 `ALLOW | MASK | ASK | DENY`。
8. **前台一个 Assistant，后台多 Agent。** 用户不得选择 SQL/Log/Code Agent。
9. **生产默认只读。** V1 的 Code、DB、Logs、Metrics、K8s 只读；Deploy、写库、重启为 `DENY`。
10. **凭证永不进入模型。** Agent 只获得 Capability ID；密钥由 Gateway 从 Vault 取得、短暂使用后销毁。
11. **外部内容一律是 Untrusted Data。** 日志、网页、文档和数据库内容不能作为 instruction。
12. **Sandbox 默认网络 `DENY`。** 只允许访问 Capability Gateway、Model Gateway 和 Artifact Store。
13. **SQL 必须经过 `Parser → AST → Policy`。** 只允许 SELECT/WITH/EXPLAIN，并强制 LIMIT、超时、扫描预算、行列权限和脱敏。
14. **知识检索必须继承 ACL。** 授权链为 `Document ACL → Chunk ACL → Retrieval ACL`。
15. **Memory 写入必须治理。** 状态链为 `Candidate → Policy → 去重 → 敏感分级 → TTL → Persist`；企业知识不得进入普通 Memory。
16. **Event 是唯一运行时协议。** `thread.*`、`turn.*`、`run.*`、`tool.*`、`policy.*`、`evidence.*`、`answer.delta` 全部进入 Event Store。
17. **一个用户入口，一个 App Server，一个 Harness。** Web、IDE、CLI、IM 不得各自实现 Agent 循环。
18. **Skill ≠ Tool。** Tool 是能力；Skill 是方法、所需证据、输出和校验。
19. **确定性流程走 Workflow，非确定性才走 Agent。** 禁止使用 LLM 编排固定 SOP。
20. **未接真实系统且未过黄金集的 Capability 不算完成。** Registry 中存在 YAML 只表示占位，不代表能力已交付。

## V1 全局禁止项

- 生产发布、重启或写库；
- 自动修复；
- Agent Marketplace；
- 无限递归；
- 全系统 MCP 化；
- 以 Kafka 或 ClickHouse 作为底座；
- Java 与 Python 双运行时栈；当前只允许单运行时。

## Phase 顺序和变更范围

开发必须严格按以下顺序推进：

```text
1 契约
→ 2 身份
→ 3 App Server
→ 4 状态机/流式
→ 5 Workbench
→ 6 Model Gateway
→ 7 Harness
→ 8 Registry
→ 9 Gateway/Policy
→ 10 Audit/Replay
→ 11 Evidence
→ 12 Knowledge
→ 13 KnowledgeAgent
→ 14 语义层
→ 15 SQL Gateway
→ 16 DataAgent
→ 17 可观测连接器
→ 18 Git/变更
→ 19 IncidentAgent
→ 20 Critic/控制面硬化
```

硬依赖：

- Phase 9 未通过，Phase 12–19 不得开始。
- Phase 11 未通过，Phase 13、16、19 不得视为完成。
- Phase 14 未通过，禁止开放自由 SQL。
- Phase 13、16、19 是仅有的用户可感知完成场景门。
- Codex/Claude Code 只能实现当前 Phase 的交付物；超出范围最多建立空接口。
- 人负责连接器凭证、指标口径、ACL 来源和安全签字；AI 不得代签。

当前受治理开发范围是 **Phase 16 — DataAgent 问数与图表闭环**。Phase 9 的
Policy/Gateway、Phase 10 的 Audit/Replay、Phase 11 的 Evidence/Claim、Phase 12 的
Knowledge ACL RAG 与 Phase 13 的 KnowledgeAgent 已作为本阶段底座；当前增量只允许
收敛受控语义目录写入、版本化定义、Synonym/TimeDefinition 及稳定 Logical Query →
SQL 编译。不得让 LLM 直接生成 SQL；未注册指标不得进入查询。

## Phase 1 冻结合同

### Event

- Event Store 是唯一事实源；Outbox、REST、SSE、WebSocket 和 JSON-RPC 都使用同一个 Event envelope。
- 事件名、schema version 和 payload schema 必须在机器可读 registry 中登记。
- Event payload 必须先转换为 JSON-safe 表示并脱敏，再依据对应 versioned schema 校验。
- 校验必须在获取数据库锁、递增 Run/aggregate sequence 或写入 Event/Outbox 之前完成。
- 非法事件不得创建或修改 `AggregateHead`，不得递增 `Run.aggregate_version`，不得写入 Event 或 Outbox。
- 已发布事件版本是不可变合同；不兼容变更必须增加 schema version，不能覆盖历史 schema。

### Errors

- REST 错误 envelope 为 `code / message / correlation_id / details`。
- JSON-RPC 使用稳定数字分类，并在 `error.data.code` 中保留同一个领域错误码。
- 所有公开异常、Gateway 结果以及持久化的 `error_code` 必须来自机器可读错误目录；新增未登记错误码必须使静态合同测试失败。

### PostgreSQL

Phase 1 的规范表名包括：

```text
users, workspaces, threads, turns, runs, run_steps, events,
artifacts, evidence, claims, audit_logs
```

历史 `audit_records` 必须通过可逆、保数据的 Alembic 迁移重命名为 `audit_logs`；不得建立并行审计表或双写路径。

### Phase 1 自动化验收映射

- `obsion validate-contracts` 校验 Event registry、envelope、全部版本化 payload schema、schema 摘要以及错误码目录。
- `test_contract_quality_gates.py` 冻结 OpenAPI，并证明生产 Event/Error 生产者与机器可读目录完全对应。
- `test_event_contracts.py` 证明非法 Event 在锁定聚合、递增 sequence、写 Event/Outbox 之前失败，合法 Event 的 API 与 Outbox 投影来自同一 envelope。
- `test_single_event_protocol.py` 阻止第二套消息/轨迹表、Event Store 旁路写入、额外实时传输出口以及未登记的运行时通知，并由 App Server 端到端测试证明 WebSocket 与 REST 投影完全一致。
- `test_postgres_phase1_schema.py` 在真实 PostgreSQL 上确认 Phase 1 规范表及其主键均已迁移，并确认旧 `audit_records` 不再存在。
- `test_postgres_audit_log_migration.py` 在隔离数据库执行升级、降级、再升级，验证数据、约束、索引和不可变触发器均被保留。
- `test_rest_error_contract.py` 与 `test_error_code_runtime_invariants.py` 冻结 REST envelope，并阻止未登记错误码进入公开结果或持久化字段。

## Phase 2 身份与租户冻结合同

### Authentication

- `/api/v1` 的所有 REST 路由统一经过共享认证依赖；业务路由即使遗漏局部
  `Principal` 参数也不能绕过认证。
- App Server 只在 `server.initialize` 调用同一个 Principal resolver；认证前的连接
  不具有 Workspace、Thread 或 Run 权限。
- Development auth 必须提供显式 Bearer credential，并使用常量时间比较。Production
  环境继续禁止 Development auth；无 token 不能回退为永久本地用户。
- OIDC 必须校验 issuer、audience、签名、生命周期、subject 与 organization claim，
  且 subject 必须映射为当前组织中的 active user。

### Users, departments and roles

- `Department` 是 organization-owned 实体，User 只引用 `department_id`，不得保留
  自由文本部门作为第二身份源。
- 系统角色词汇固定为 `admin / engineer / analyst / operator / support / viewer`。
- 只有 `admin` 拥有 `*`；custom role 不得覆盖系统角色名或获得 wildcard。
- Role 提供稳定职责基线，但 Workspace owner/member 和 Policy 决策仍独立执行；
  角色名称不能替代资源授权。

### Tenant boundary

- 所有 protected repository query 必须包含 `Principal.organization_id`。
- Workspace 读写必须继续验证 owner、visibility 或 membership；read membership 不得
  隐式获得 write。
- User department、UserRole、Workspace owner 和 WorkspaceMember 的所有身份边均由
  `(organization_id, id)` composite foreign key 约束。数据库必须拒绝跨组织边，不能只
  依赖 API 检查。
- Unauthorized lookup 默认使用不泄露资源存在性的 Not Found；同组织已知 Workspace
  的未授权写入可返回显式 Deny。

### Phase 2 自动化验收映射

- `test_phase2_identity_api.py` 证明 missing/invalid credential 被拒绝，并冻结 Department
  与六类 system role 合同。
- `test_workspace_membership_is_enforced_for_runs_and_writes` 证明跨 Workspace 读取失败、
  read-only member 写入失败、明确 write membership 才能创建 Turn/Run。
- `test_postgres_phase2_identity_migration.py` 在真实 PostgreSQL 中执行 upgrade、
  downgrade、re-upgrade，验证部门回填、角色种子、composite FK 以及跨租户写入失败。
- `alembic check` 必须证明 ORM metadata 与迁移 head 无差异。
- Phase 1 的 Event、error、OpenAPI 与单一运行时协议门禁必须继续全绿；身份增量不得
  建立第二套消息或执行轨迹。

## 人工签字状态与连续开发

项目负责人要求自动化验收完成后无需等待人工签字即可继续下一 Phase。该流程指令只
解除“等待”阻塞，不构成人工批准；AI 仍不得填写批准人、批准日期或通过结论。

Phase 1 仍等待架构守卫确认：

> 系统不存在第二套消息模型；Event Store 是唯一运行时协议。

**状态：PENDING — AI 不得填写批准人、批准日期或通过结论。**

人工复核材料见 [`docs/architecture/phase-1-architecture-guard.md`](docs/architecture/phase-1-architecture-guard.md)。

Phase 2 仍等待公司角色映射确认：

> `admin / engineer / analyst / operator / support / viewer` 的权限基线能够映射公司职责。

**状态：PENDING — 不阻塞连续开发，也不得被表述为人工批准。**

人工复核材料见 [`docs/architecture/phase-2-identity-design.md`](docs/architecture/phase-2-identity-design.md)。

## Phase 3 App Server 与 Thread 生命周期冻结合同

### Transport boundary

- `/api/v1/app-server` 是 WebSocket/JSON-RPC 入口，REST 是管理与二进制传输入口；两者
  只能调用同一应用服务并投影同一 Event Store，不能实现第二套运行时。
- `obsion.app_server` 只负责协议解析、校验、响应与流式投影；禁止导入 SQLAlchemy、
  database、persistence、Harness 或 Model Gateway，也禁止创建数据库会话、Event Store
  或模型客户端。
- `AppServerApplication` 是传输层外的事务边界，统一承载认证、授权、幂等记录和应用
  服务委派；App Server 适配器不得自行执行业务 SQL。

### Lifecycle contract

- Workspace、Thread 和 Turn 提供 REST 创建合同；Run 提供读取事件和取消合同。
- Thread 支持 create、archive、resume、fork；所有状态变化与 Event、audit 在同一事务
  中提交。
- fork 创建具有明确 parent/fork-turn lineage 的新 Thread，并立即将源 Thread archive
  为只读。源 Thread 只有经过显式 resume 后才能再创建 Turn。
- fork 的继承历史固定在 `forked_from_turn_id`，源 Thread 后续内容不得渗入既有分支。
- Turn 与 Run 是一对多；replay 为同一 Turn 建立新 Run，而不是覆盖原 Run。
- 每个持久化 JSON-RPC 命令要求 principal-scoped `client_request_id`；相同请求重试返回
  同一结果，不同参数复用同一 key 必须冲突。

### Phase 3 自动化验收映射

- `test_phase3_app_server_boundary.py` 通过 AST 守卫阻止 App Server 传输层直连数据库、
  persistence、Harness 或 Model Gateway。
- `test_app_server_api.py` 覆盖统一协议、持久化幂等、可恢复事件投影，以及 fork 后源
  Thread 只读和显式 resume。
- `test_thread_lifecycle_api.py` 覆盖 REST create/archive/resume/fork、事件/audit、冻结
  分支历史、同一 Turn 多 Run、跨租户隔离和 active Run 的手工 archive 保护。
- Phase 1/2 的 Event、error、OpenAPI、认证、租户 composite FK 与迁移门禁必须继续
  全绿。

Phase 3 仍等待 App Server API freeze 人工确认：

> REST 与 `obsion.jsonrpc.v1` 的 Phase 3 生命周期 surface 可以作为后续客户端兼容基线。

**状态：PENDING — 不阻塞连续开发，也不得被表述为人工批准。**

人工复核材料见 [`docs/architecture/phase-3-app-server-gate.md`](docs/architecture/phase-3-app-server-gate.md)。

## Phase 4 流式协议与 Run 状态机冻结合同

### State machine

- Run 状态词汇固定为 `PENDING / RUNNING / WAITING_APPROVAL / WAITING_USER /
  REPLANNING / COMPLETED / FAILED / CANCELLED`。
- 所有状态转换必须经过 `validate_run_transition`；完整邻接矩阵由穷举测试冻结，三个
  terminal 状态没有任何出边。
- Turn 创建持久化 `PENDING` Run；RunWorker 领取后转换为 `RUNNING`，Harness 才能规划、
  调用 Capability、验证和回答。普通 chat completion 不能直接充当 Run。
- 状态与 `run.started / run.resumed / run.state_changed / run.completed / run.failed /
  run.cancelled` 等 Event 在同一持久化边界内表达，不建立第二套运行轨迹。

### Streaming

- `Event.run_sequence` 是 Run 内跨 aggregate 的单调、不变续流游标；REST、SSE、
  WebSocket/JSON-RPC 投影同一 Event envelope。
- WebSocket `run.subscribe(after_sequence)` 在新连接中也可从最后已处理游标继续；客户端
  仅在成功处理 Event 后保存游标，并按 Event ID 去重。
- SSE 同时接受 `after` 和 `Last-Event-ID`，以二者最大值为起点，避免代理或客户端重连
  回退游标。
- `answer.delta / tool.started / tool.completed` 必须是注册过 schema 的 Event，不能作为
  传输层私有消息直接发送。

### Cancellation

- cancel 在锁定 Run 后线性化：记录原状态、设置 `cancellation_requested_at`、转换
  `CANCELLED`、清除 worker lease、取消所有 `PENDING / RUNNING / WAITING_APPROVAL`
  Step，并顺序追加 `run.cancellation_requested` 与 `run.cancelled` 及 audit。
- Run 锁必须先于 Step 锁。Harness 在计划生成、每一调度波次、replan、Step 完成和
  response 提交前检查取消；取消提交后不得开始下一 Step，也不得把已取消 Step 覆盖回
  完成态。
- 已经跨过外部执行边界的调用只能合作式收尾；其实际成本仍可记录，但结果不能重新打开
  Run、启动后续 Step、产生回答或产生 `run.completed`。

### Phase 4 自动化验收映射

- `test_run_state.py` 穷举全部状态对并冻结精确允许/拒绝矩阵。
- `test_phase4_run_cancellation.py` 使用阻塞 Capability 边界证明并发 cancel 后依赖 Step
  从未调用，Run/Step 保持取消终态且没有 answer/completed Event。
- `test_postgres_phase4_runtime.py` 在真实 PostgreSQL 事务中证明 waiting Run、active
  Steps、两个有序 Event 和 audit 原子收敛，并在回滚隔离事务中不污染测试数据库。
- `test_app_server_reconnect_resumes_from_a_durable_run_cursor` 使用两个真实 WebSocket
  连接证明断线后无重复、无缺口续流。
- `test_governed_knowledge_run_is_replayable` 证明真实 Run 产生 schema-governed
  `tool.started / tool.completed / answer.delta`。
- Event/error producer 静态分析、OpenAPI freeze、单一 Event 协议、PostgreSQL cursor
  并发门禁和前序 Phase 门禁必须继续全绿。

Phase 4 仍等待运行时协议人工确认：

> 状态矩阵、取消线性化语义与 reconnect cursor 合同可以作为客户端和 worker 的长期兼容基线。

**状态：PENDING — 不阻塞连续开发，也不得被表述为人工批准。**

人工复核材料见 [`docs/architecture/phase-4-runtime-gate.md`](docs/architecture/phase-4-runtime-gate.md)。

## Phase 5 Workbench 壳冻结合同

### One product surface

- 首页必须先经过真实会话检查，再显示一个 `app-shell`；未登录不能渲染 Workspace、
  Thread 或 Run 数据。
- 左栏只负责 Workspace/Thread 导航和生命周期入口，中栏只负责 Turn/回答，右栏只投影
  Runtime 的状态、Plan、持久化 Step、最新 Event 与 Cost。Workbench 不得实现 Agent 循环。
- 当前 Phase 不新增 SQL 编辑器、Dashboard builder、Agent/Model 选择器或大而全管理功能；
  已提前存在的后续页面不作为 Phase 5 验收依据。

### Browser session boundary

- 浏览器访问令牌只允许提交到 `POST /api/v1/auth/session` 做一次交换；不得进入
  `localStorage`、`sessionStorage`、URL、日志、WebSocket params 或前端环境变量。
- 服务端只保存随机 opaque session 的 SHA-256 digest，并通过 `HttpOnly`、
  `SameSite=Strict` Cookie 返回；production/staging Cookie 必须启用 Secure。
- REST 与 App Server 必须从同一 `auth_sessions` 记录解析同一 Principal。CLI/SDK 的显式
  Bearer 仍可用，但不存在缺 credential 时回退到本地管理员的路径。
- unsafe cookie request 除 SameSite 外还必须验证 Origin；logout 必须先持久化 revoke，
  expired/revoked session、inactive User 和跨组织身份边均不能获得权限。

### Runtime projection and mobile shell

- Turn 创建后立即把 Run 放入右侧；Event 通过 App Server durable cursor 流入，Run、Step、
  Evidence、Artifact 和 Cost 通过 REST 对账。两条通道只投影 Event Store/数据库事实，
  不建立第二套前端状态机。
- Runtime timeline 必须按持久化 Step ordinal 展示 name/status/kind，并可见 Run status、
  latest Event、step count 与 cost；尚未规划时显示明确 pending 项。
- 880px 及以下将左右栏变为带关闭遮罩的 drawer。页面 shell 必须采用百分比宽度、
  `min-width: 0` 和边界 overflow，满足 `scrollWidth <= clientWidth`；表格/代码只允许在
  自身内容容器横向滚动。

### Phase 5 自动化验收映射

- `test_phase5_workbench_auth.py` 覆盖 session exchange、Cookie 属性、REST/WebSocket 共享
  身份、Origin 拒绝、revoke，以及真实 Turn 的 Plan/Steps/Event cursor/Cost 合同。
- `test_phase5_workbench_boundary.py` 冻结 SessionGate、三栏组合、Step/status/cost 渲染、
  浏览器零 token persistence 和移动端无页面级横滑 CSS 边界。
- `test_postgres_phase5_auth_sessions.py` 在真实 PostgreSQL 中验证 digest-only、tenant FK、
  resolve 与 revoke。
- Web lint/typecheck/production build、真实浏览器桌面流程和移动 viewport 几何检查、
  OpenAPI/error/static boundary、Alembic drift、SDK、Compose/Helm 与 Phase 1–4 门禁必须全绿。

Phase 5 仍等待 Workbench 产品与安全体验人工确认：

> 一个登录入口和左/中/右信息层级适合作为员工唯一 Obsion 入口，会话与移动端行为符合组织要求。

**状态：PENDING — 不阻塞连续开发，也不得被表述为人工批准。**

人工复核材料见 [`docs/architecture/phase-5-workbench-gate.md`](docs/architecture/phase-5-workbench-gate.md)。

## Phase 6 Model Gateway 冻结合同

### Agent 与 Provider 边界

- Agent、Skill、Harness、App Server、SDK 与前端只允许传递逻辑 `ModelProfile`；Provider、
  model ID、base URL、credential、pricing 与协议细节只能存在于 Model Gateway/管理配置。
- `ModelGateway.complete` 是统一补全与工具调用入口。Provider adapter 可替换，但不得把
  厂商 SDK 或 wire contract 扩散到 Harness。
- 工具声明和返回值是 provider-neutral 合同；返回的 tool call 只是一条未执行请求，必须
  通过 JSON Schema 校验，后续仍只能经过 Capability Gateway/Policy 执行。

### Profile 路由与 private 强制

- 必需逻辑 Profile 为 `fast / reasoning-high / private`。换 Profile 只能改变 Gateway
  的有效路由，不得要求修改 Harness 或 AgentSpec 中的厂商/model ID。
- 路由同时检查 organization、enabled、classification、capabilities、provider、region、
  minimum context window 与 private endpoint 标记。
- `OBSION_MODEL_FORCE_PRIVATE_FOR_SENSITIVE=true` 时，`CONFIDENTIAL / RESTRICTED` 无条件
  改用配置的 private Profile；缺 Profile、缺 `private=true` 要求或缺私有 endpoint 都要在
  Provider 调用前失效关闭。
- Profile 仅在 `routing_policy.fallback=true` 时按绑定优先级 fallback，且 fallback 不得
  跨 Profile 放宽分类、地域、工具、上下文或 private 约束。

### 使用量、成本与安全

- 输入/输出 token 与 cost budget 在调用前检查；Provider usage 决定最终 token/cost。
- 每一次 Provider 尝试独立写入 `model_calls`：effective profile、endpoint、operation、
  redacted request fingerprint、input/output tokens、latency、cost、outcome。失败 primary 与
  成功 fallback 不得合并或改写。
- `model_calls` 不保存 prompt、tool argument 原文或 credential。credential 仅通过
  `credential_ref` 临时解析；模型 egress 必须满足 allowlist，非本地环境强制 TLS。
- JSON mode 必须返回 object；undeclared tool、非法 arguments、schema mismatch、重复 call
  ID 或违反 tool choice 均失效关闭，模型输出不能直接成为权限裁决或工具执行。

### Phase 6 自动化验收映射

- `test_phase6_model_gateway.py` 覆盖 Profile 无侵入切换、统一工具/JSON 合同、schema
  失效关闭、敏感分类 private 强制、缺 private 拒绝、fallback 尝试记账、成本计算、管理
  API，以及 Agent/registry/frontend 中无 Provider/model ID。
- `test_model_embeddings.py` 保持同一 Profile/egress/usage 边界下的 embedding 兼容性。
- OpenAPI、255 个稳定错误码与 error producer manifest 必须冻结新增管理合同。
- 真实 PostgreSQL 必须验证 effective Profile、endpoint、usage/cost、fingerprint 和 fallback
  outcome 的持久化；Ruff、mypy、全量测试、Alembic、SDK、Web、Compose/Helm 与 Phase 1–5
  门禁必须继续全绿。

Phase 6 仍等待 Model Gateway 安全与成本策略人工确认：

> 逻辑 Profile、private endpoint 认定、地域/分类、pricing、credential、egress 与 fallback
> 策略符合公司模型治理要求。

**状态：PENDING — 不阻塞连续开发，也不得被表述为人工批准。**

人工复核材料见 [`docs/architecture/phase-6-model-gateway-gate.md`](docs/architecture/phase-6-model-gateway-gate.md)。

## Phase 7 Harness 核心循环冻结合同

### GeneralAgent 与 AgentSpec

- GeneralAgent 是唯一默认用户入口；用户不得选择 SQL、Log、Code 等内部 Agent。
- AgentSpec 必须是声明式、可版本化配置，包含 description、`modelPolicy.profile`、
  maxSteps、timeout、skills、capabilities、riskPolicy、memory 与 sandbox。AgentSpec 只能
  绑定逻辑 ModelProfile，不能包含 provider、model ID、base URL、credential 或 API key。
- 创建 Run 时必须从已提升的 GeneralAgent 版本解析默认 ModelProfile、maxSteps 与 timeout；
  用户显式选择 Profile 只能选择已注册启用的逻辑 Profile，不得绕过 Model Gateway。

### 核心循环与 Step 执行器

- 普通 Run 必须把 `Observe → Understand → Plan → Act → Verify → Respond` 写入持久化
  RunStep 图。Act 阶段由零个或多个 `CAPABILITY` Step 表达；Observe、Understand、Plan、
  Verify 与 Respond 均是一等 Step。
- Step 执行器只负责确定性 DAG 调度：识别 ready Capability 波次、依赖失败的 blocked
  Step，以及未解析或循环依赖的 deadlock。它不能导入连接器、HTTP 客户端或数据库驱动，
  也不能执行工具本身。
- Harness 只能通过 Capability Gateway 执行 Capability Step。缺失 Capability、缺失绑定、
  policy 拒绝或 schema 拒绝都必须留下 RunStep 状态、error_code 与 Event 轨迹，不得用模型
  生成内容掩盖失败。
- 非事实寒暄可在无 Evidence、无 Claim 的情况下完成 Verify/Respond；事实性回答仍必须
  经过 Claim + Evidence 检查。Memory 与历史对话只作为上下文，不能替代当前 Run Evidence。

### Phase 7 自动化验收映射

- `test_phase7_harness_core.py` 证明 “你好” 会完成无 Capability 的完整核心循环，并且不产生
  Evidence、Claim 或 tool event；也证明 “查生产库” 被路由为资源访问请求，只能计划
  `data.query`，不内置 SQL，因无生产 Capability 绑定而失败，Verify/Respond 被跳过且事件完整。
- `test_step_executor.py` 冻结 Step 执行器对 ready、blocked 与 deadlock 的 DAG 行为。
- `test_registry_manifests.py` 与 `AgentSpec.from_dict` 证明 AgentSpec 只能绑定 ModelProfile，
  且内置/声明式 Agent 配置走同一套校验。
- `test_critic.py` 证明非事实响应可无 Claim 验证通过，同时事实性空证据/空 Claim 仍失败。
- OpenAPI、Event/Error 合同、Ruff、mypy、全量测试、PostgreSQL 迁移、SDK、Web、Compose/
  Helm 与 Phase 1–6 门禁必须继续全绿。

Phase 7 仍等待 Harness 核心循环人工确认：

> GeneralAgent、AgentSpec、Step 图、无 Capability 失败语义与无证据非事实响应例外可以作为
> 后续 Capability Registry、Policy Gateway 与 Evidence Fabric 的长期基线。

**状态：PENDING — 不阻塞连续开发，也不得被表述为人工批准。**

人工复核材料见 [`docs/architecture/phase-7-harness-gate.md`](docs/architecture/phase-7-harness-gate.md)。

## Phase 8 Capability Registry 冻结合同

### CapabilityDescriptor 与版本

- Capability 只能通过组织隔离的 `CapabilityDefinition` 与不可变
  `CapabilityVersion` 注册；运行时和 Planner 不得依赖代码中的能力白名单。
- 每个可发现版本必须投影为 `CapabilityDescriptor`，至少包含稳定身份、版本、传输、
  `inputSchema`、`outputSchema`、`risk`、`sideEffect`、`permission`、`timeout`、
  `dataClassification`，以及明确映射到 `Evidence` 的输出合同。
- 输入和输出 JSON Schema 在注册投影边界校验；Evidence mapping 必须声明非空类型，
  否则能力不能进入公开 Registry surface。
- 能力版本、权限动作和组织边界由数据库记录决定；REST 只返回当前 active 且当前
  Principal 有权查看的版本，不暴露其他组织或隐藏版本。

### Planner 与运行时边界

- Harness 在生成计划前解析当前 AgentSpec 的 capability IDs，并与当前组织 active
  Registry 的能力集合取交集；未注册、未 active、未授权或不在 AgentSpec 中的能力均
  不得成为计划 Step。
- Registry 只描述能力，不执行能力。首批 `knowledge.search`、`data.query`、
  `metric.query`、`log.search`、`git.diff` 是占位 descriptor；实际执行仍必须经过后续
  Capability Gateway/Policy 边界。
- 无匹配能力时，Planner 必须产生无能力计划或明确的 `capabilities_unavailable`
  失败轨迹；不得合成 SQL、HTTP 请求或模型答案来掩盖 Registry 缺失。

### Phase 8 自动化验收映射

- `test_phase8_capability_registry.py` 冻结首批占位 descriptor 的 schema、Evidence
  输出、风险/副作用/权限/超时字段、详情版本和 Planner 注册能力过滤行为。
- `test_contract_quality_gates.py` 同时冻结新增 descriptor 的错误码生产者、转发路径和
  helper 调用映射，防止 Registry 校验错误绕过机器可读错误目录。
- OpenAPI、Event/Error 合同、租户/认证、Harness、数据库迁移、SDK、前端、Compose 和
  Helm 质量门禁必须继续全绿；Phase 9 前不得把占位 descriptor 表述为真实连接器能力。

Phase 8 仍等待 Capability Registry 契约人工确认：

> CapabilityDescriptor 的版本、schema、风险、权限、Evidence 输出和 Planner 过滤语义
> 可以作为后续 Capability Gateway、Policy 与真实连接器注册的长期兼容基线。

**状态：PENDING — 不阻塞连续开发，也不得被表述为人工批准。**

人工复核材料见 [`docs/architecture/phase-8-capability-registry-gate.md`](docs/architecture/phase-8-capability-registry-gate.md)。

## Phase 9 Policy Engine 与 Capability Gateway 冻结合同

### Policy decision

- 每一次 Capability 请求都以结构化 `WHO/WHAT/RESOURCE/CONTEXT/RISK` 输入评估：
  Principal 的用户、部门、角色、权限和属性，Agent/AgentVersion，能力动作，目标资源，
  环境/时间/设备上下文，以及声明的 RiskLevel 均不可由 Prompt 替代。
- Policy 规则按组织隔离、版本化并以最强效果裁决；显式 `DENY` 胜过其他效果。所有
  非 `DENY` 结果仍必须满足 Principal 的基础 permission，策略不能提升越权主体。
- `L0/L1` 可在授权后自动执行；`L2` 默认 `MASK` 并携带可执行 obligation；`ASK` 只能
  通过 durable Approval 进入 `WAITING_APPROVAL`；通用 Capability 的 L3–L5 或任何副作用
  始终 `DENY`，其中 L5 无条件拒绝。
- PolicyDecision 持久化脱敏输入、效果、匹配策略、obligation、reason code 和稳定
  fingerprint；fingerprint 绑定 Principal 权限/属性、AgentVersion、能力版本与策略匹配，
  防止批准后上下文漂移。

### Gateway execution boundary

- Gateway 只解析 active、租户一致、环境一致且有 enabled binding 的 Registry version；
  Connector 必须声明对应 permission grant。未注册、未绑定、未授权、未声明 grant 或
  AgentSpec 不允许的能力不得到达执行器。
- 固定顺序为 `resolve → policy → grant/schema → approval/rate-limit → credential →
  timeout-bounded executor → output schema → masking → Evidence → Event/Audit`。Agent、
  Model 和 Tool 均不能绕过 Gateway 直接接触生产资源。
- 凭证由 Credential Broker 短暂解析，只传给 Connector，执行完成或失败后清理；HTTP
  egress、TLS、SQL read-only 和 DLP 约束继续由 Connector/Gateway 边界执行。
- 每次请求写入 `capability.requested`、`policy.decided` 及相应 tool/approval/rate-limit
  Event，并写入带 actor、resource、risk、policy、outcome、latency 的 AuditLog。连接器
  的注册错误保持稳定 error code，不被笼统成功或模型文本掩盖。
- 分布式限流在非测试环境默认 fail-closed；超时、限流服务不可用、schema/connector
  失败都留下可回放的 Step/Error/Event 轨迹，不得继续执行后续能力。

### Phase 9 自动化验收映射

- `test_phase9_policy_gateway.py` 覆盖完整策略输入、基础 permission 不可被策略提升、
  L5 默认拒绝、connector grant、ALLOW/MASK/ASK/DENY、AgentSpec 风险重检、限流、超时和
  typed connector errors；执行器在拒绝/等待路径中必须为零调用。
- `test_policy.py` 与 existing Gateway/API tests 继续冻结策略优先级、租户隔离、masking、
  approval resume 和审计/事件轨迹；Error/Event producer、OpenAPI、迁移、SDK、前端、
  Compose/Helm 以及 Phase 1–8 门禁必须全绿。

Phase 9 仍等待 Policy/Gateway 安全人工确认：

> `WHO/WHAT/RESOURCE/CONTEXT/RISK` 到 `ALLOW/MASK/ASK/DENY` 的决策语义、L5 默认拒绝、
> Gateway 单一执行边界、限流/超时/审计和 durable approval 行为可以作为后续 Audit、
> Evidence 与真实连接器阶段的长期安全基线。

**状态：PENDING — 不阻塞连续开发，也不得被表述为人工批准。**

人工复核材料见 [`docs/architecture/phase-9-policy-gateway-gate.md`](docs/architecture/phase-9-policy-gateway-gate.md)。

## Phase 10 Audit、Trajectory 与 Replay 冻结合同

### AuditLog

- `audit_logs` 是单一、append-only 的安全与执行审计表；不得建立并行审计表或让
  连接器自行写审计。审计写入与对应的状态/Event/证据结果处在同一事务边界。
- 每条记录保留组织、correlation、actor（who/when）、action/resource、outcome、
  policy/approval、risk、latency，并在 redacted metadata 中保留 agent version、model
  profile、capability version、资源选择和结果 classification 等标准维度。
- 结构化和文本 metadata 在落库前递归脱敏。密码、token、API key、Bearer、凭证 URI、
  私钥块及敏感键值不得以原文出现在 Audit、Event、Replay 或上下文快照。
- Run 完成、失败、取消、Capability 允许/拒绝/等待/失败和 Replay materialize 均必须
  留下可关联的审计轨迹；审计查询只返回当前租户且继续受 `audit.read` 管辖。

### Trajectory 与 Replay

- Run 的 Event 序列是 append-only 事实源；Replay 复制同一不可变快照中的计划、步骤、
  结果、证据、Claim、Artifact、Memory/Conversation snapshot 与事件顺序，并建立新的
  Run/资源 ID 与显式 lineage。
- Replay 使用稳定 SHA-256 snapshot fingerprint；相同源快照重复 Replay 必须得到相同
  fingerprint。Replay 过程只能写 `run.replay.*` 事件与 Replay 审计，不得调用
  Model Gateway、Capability Gateway、外部网络或生产资源。
- Replay 的 source event 通过受约束的事件包装保留原顺序、schema version、actor、时间和
  脱敏 payload；原 Run 与 Replay Run 的资源边界和租户边界不可互相污染。

### Phase 10 自动化验收映射

- `test_phase10_audit_replay.py` 覆盖 Turn prompt 脱敏、Run 完成审计 canonical
  dimensions 及租户可见的 correlation/latency/policy 字段。
- `test_security.py` 覆盖文本 assignment、Bearer、凭证 URI、私钥块和结构化 key-aware
  redaction；现有 API、Replay、Event、PostgreSQL 与静态契约测试继续冻结轨迹完整性。
- 完整 Phase 1–9 contract、identity、Gateway、OpenAPI、SDK、frontend、migration、
  Compose 与 Helm 门禁必须全绿。

Phase 10 仍等待 Audit/Privacy/Replay 安全人工确认：

> 审计维度、脱敏边界、append-only 轨迹和不触达外部执行器的 Replay 行为可以作为
> Evidence Fabric 与真实连接器阶段的长期可追责基线。

**状态：PENDING — 不阻塞连续开发，也不得被表述为人工批准。**

人工复核材料见 [`docs/architecture/phase-10-audit-replay-gate.md`](docs/architecture/phase-10-audit-replay-gate.md)。

## Phase 11 Evidence Fabric 与 Claim 冻结合同

### Canonical Evidence

- 所有文档、SQL/数据、日志、Git、部署和工具观察结果都通过 `EvidenceFabric`
  归一化为同一结构：`type/source/resource/observed_at/ingested_at/content/content_fingerprint/
  confidence/classification/permissions/lineage`。Transport payload 不得直接进入 UI 或
  Claim 层。
- Fabric 在持久化前递归脱敏 content/lineage，校验非空 source/resource、JSON object
  content、0–1 且有限的 confidence，并对脱敏内容生成稳定 SHA-256 fingerprint；权限
  标签去重、排序后保存。
- 生产入口（Capability Gateway 与附件 Evidence）共享同一 Fabric；Replay 只复制已归一化
  的不可变 Evidence，不再次重算或连接外部系统。

### Claim linkage and verification

- Claim 是当前 Run 的原子结论，必须通过 `ClaimEvidence` 多对多关系引用同一 Run 的
  Evidence；Evidence/Claim/Link 均按组织和 Run 隔离。
- Critic 检查 required Evidence 类型、引用 ID 合法性、来源多样性、重复 fingerprint
  和冲突；事实性回答缺 Evidence 或 Claim 引用不完整时不得 VERIFIED 或高置信。只有
  明确的非事实寒暄允许无 Claim/Evidence。
- Run inspection API 返回 Claim 的 evidence IDs，并能按 Evidence ID 读取安全内容；
  Workbench 点击结论可直接打开对应 Evidence 详情，不复制私有 connector 结果结构。

### Phase 11 自动化验收映射

- `test_phase11_evidence_fabric.py` 覆盖来源/资源规范化、递归脱敏、稳定 fingerprint、
  权限去重和 confidence 边界。
- `test_critic.py`、Harness/API Replay、Run inspection 和前端静态门禁覆盖无证据高置信
  拒绝、Claim↔Evidence 链接、跨租户隔离与结论导航。
- 完整 Phase 1–10 contract、Policy/Gateway、Audit/Replay、OpenAPI、SDK、frontend、
  migration、Compose 与 Helm 门禁必须全绿。

Phase 11 仍等待 Evidence/Claim 安全人工确认：

> Evidence 归一化、脱敏 fingerprint、Claim 引用和无证据降级行为可以作为 Knowledge、
> Data 与 Incident 三条生产场景的证据总线长期基线。

**状态：PENDING — 不阻塞连续开发，也不得被表述为人工批准。**

人工复核材料见 [`docs/architecture/phase-11-evidence-claim-gate.md`](docs/architecture/phase-11-evidence-claim-gate.md)。

## Phase 12 Knowledge 管道（ACL RAG）冻结合同

### Ingestion

- 文档通过受支持解析器进入版本化 `Document`/`DocumentVersion`，再按有界字符窗口
  切成带 heading path 的 `DocumentChunk`；原始 checksum、parser version、filename 和
  source 元数据保留用于完整性与 Replay lineage。
- 每个 Document 必须显式提供 ACL。ACL 归一化为 organization/users/roles/departments
  及对应 deny 列表；同一 ACL 同步写入当前 Chunk grants。重复 checksum 不是跳过授权变更
  的理由：重新摄入会原子重绑当前版本的 classification、ACL 和 grants。
- embedding 只能通过 Model Gateway 写入 PostgreSQL/pgvector 的 Chunk 向量；不引入
  独立向量数据库。Embedding profile、endpoint 和 classification 受模型路由及凭证边界
  约束，失败时不产生半成品索引。

### Retrieval

- 检索查询始终带 organization、当前 Document version、未删除和 Chunk ACL grant
  过滤；deny 优先于 direct/role/department/organization allow，classification permission
  不能跨租户提升访问。PostgreSQL 使用受 ACL 约束的 lexical/vector 候选集合，再做确定性
  rerank；SQLite 仅用于测试的 lexical 等价实现。
- 召回结果只返回 `SearchHit` 的安全字段（document/chunk/version/title/source/heading/
  content/score/classification），随后由 Capability Gateway 归一为 DOCUMENT Evidence；
  不把向量命中或私有连接器 payload 直接交给 UI/Claim。
- 未授权文档在候选阶段即为零召回；文档下载和详情也复用相同的 tenant/ACL 判断。

### Phase 12 自动化验收映射

- `test_phase12_knowledge_pipeline.py` 验证 identical-content ACL 收紧会重建 Chunk
  grants，并对拒绝用户返回零召回。
- `test_api_e2e.py` 验证解析、切块、版本/完整性、ACL 过滤、知识 Evidence、引用和 Replay；
  Model Gateway embedding、PostgreSQL integration、静态 error/event、OpenAPI、SDK、
  frontend、Compose 与 Helm 门禁继续有效。

Phase 12 仍等待 Knowledge ACL/数据源人工确认：

> 文档 ACL 来源、classification 口径、embedding profile 和解析器适用范围需由负责人员
> 确认；自动化测试不替代这些人的安全与数据治理签字。

**状态：PENDING — 不阻塞连续开发，也不得被表述为人工批准。**

人工复核材料见 [`docs/architecture/phase-12-knowledge-gate.md`](docs/architecture/phase-12-knowledge-gate.md)。

## Phase 13 KnowledgeAgent 打穿冻结合同

### Internal route and Skill

- 用户始终面对 GeneralAgent；当 Understanding 路由为 `KNOWLEDGE` 时，Harness 在同一
  事务中解析当前组织 active 的 `knowledge-agent` AgentVersion 与 `knowledge-qa`
  SkillVersion，并将 Agent/Skill 名称、版本、checksum、指令、required Evidence 和
  verification 写入不可变 Run plan 快照。
- KnowledgeAgent 只允许 `knowledge.search` 与 `document.read`，风险上限为 L1；
  `knowledge-qa` 禁止切换到 SQL、指标、日志、Trace、代码、工单或生产资源。Agent/Skill
  选择不向用户暴露，也不能由 Prompt 覆盖。
- 既有 v1 `intent.detected`/`plan.created` Event 仍只发送注册 schema 的公共投影；内部
  Agent/Skill 快照保留在 Run/API/Replay，不以未登记字段污染 Event 合同。

### Citation and unknown boundary

- Knowledge 回答只能由当前 Run 的授权 DOCUMENT Evidence 生成；Claim 的 evidence IDs
  必须来自实质性（非零召回）Evidence，并被 Critic 验证。
- 每个可回答的 Knowledge Run 在回答 Markdown 和结构化 artifact 中生成稳定引用，保留
  source、document title、version、chunk 和 Evidence ID；无可引用证据时不得保留模型
  结论，回答必须明确“不知道”，且不能伪造 Claim 或 citation。
- `v1-knowledge-qa` Golden Dataset 固定 20 个问题，覆盖组织、用户、角色、部门 allow
  以及 deny/零召回用例；无引用回答在 citation/faithfulness 门禁中不得通过。

### Phase 13 自动化验收映射

- `test_phase13_knowledge_agent.py` 验证内部路由、Skill 快照、能力收敛、结构化引用和
  无授权证据的未知回答。
- Knowledge、Critic、Replay、Evidence、API/SDK、前端、静态 Event/Error、OpenAPI、
  PostgreSQL、Compose 和 Helm 门禁继续有效；`uv run obsion validate-evaluations` 必须
  报告包含 20 个 KnowledgeAgent Golden cases。

Phase 13 仍等待 KnowledgeAgent/Golden Dataset 人工确认：

> 负责人员需确认知识问答范围、引用展示口径、未知回答语言与 20 问黄金集的实际文档
> fixture/ACL 来源；自动化通过不替代场景验收签字。

**状态：PENDING — 不阻塞连续开发，也不得被表述为人工批准。**

人工复核材料见 [`docs/architecture/phase-13-knowledge-agent-gate.md`](docs/architecture/phase-13-knowledge-agent-gate.md)。

## Phase 14 语义层冻结合同

### Catalog contract

- `Metric`、`Dimension`、`SemanticEntity`、`SemanticRelation`、`BusinessRule`、
  `TimeDefinition`、`SemanticSynonym`、`DataSource`、`DataTable` 与 `DataColumn` 是
  组织隔离的语义目录；Metric/Dimension/Entity/Rule/TimeDefinition 按名称递增版本，
  已写入版本不可覆盖。
- 管理接口以人工定义或受信来源写入表达式、过滤、时间列、主键、关系和业务规则；
  Synonym 只能引用同组织、已存在的语义对象。API 返回和目录汇总继续受
  `data.catalog.write/read` 授权与租户范围约束。
- “付费人数”等自然语言只通过已登记 Metric 的 name/display_name/synonyms 或
  SemanticSynonym 解析到稳定 Metric ID；解析不到已验证指标时，查询接口返回
  `metric_not_resolved`，不猜测 schema 或表达式。

### Stable compilation

- DataAgent 只能提交 `metric_id`、有序 `dimension_ids`、时间范围和受控 filters 的
  Logical Query；DataIntelligenceService 验证指标已 validated、数据源同组织且只读、
  维度属于同一来源表，然后由目录表达式生成 SQL。
- 维度按 Logical Query 顺序、Metric filters 按字段名排序，参数位置和 SQL 文本因此对
  同一逻辑计划稳定；重复/跨表维度、未知列、非法操作符均在编译前拒绝。
- 编译完成仍进入既有 SQL AST policy 与 Query Gateway；本阶段不开放自由 SQL、主库或
  写操作。

### Phase 14 自动化验收映射

- `test_phase14_semantic_layer.py` 覆盖 Entity/Relation/BusinessRule/TimeDefinition/
  Synonym 的组织隔离与版本写入、“付费人数”解析、同一逻辑计划 SQL 稳定性及未注册
  指标拒绝。
- Data catalog、SQL policy、Harness/Data route、Evidence/Claim、OpenAPI、SDK、前端、
  PostgreSQL、静态 Error/Event、Compose 与 Helm 门禁继续有效。

Phase 14 仍等待语义目录人工确认：

> 指标表达式、时间定义、实体关系、业务规则的所有者、版本发布流程与生产数据源
> 映射需由业务/数据负责人确认；自动化编译稳定性不替代语义正确性签字。

**状态：PENDING — 不阻塞连续开发，也不得被表述为人工批准。**

人工复核材料见 [`docs/architecture/phase-14-semantic-layer-gate.md`](docs/architecture/phase-14-semantic-layer-gate.md)。

## Phase 15 SQL Gateway 冻结合同

### Compiler and policy boundary

- Logical Query 只允许引用已验证的语义 Metric/Dimension；编译器生成参数化 SQL，
  再经过 sqlglot Parser、AST 只读检查、表/列权限和确定性 LIMIT 策略。模型永远不
  接触连接串，也不能绕过 Capability Gateway 发送自由 SQL。
- `SqlPolicyValidator` 允许 SELECT/WITH 和只读 EXPLAIN，拒绝多语句、SELECT INTO、
  写操作、危险函数、未知表/列与通配符投影；`EXPLAIN ANALYZE/BUFFERS` 明确拒绝，避免
  通过解释语句执行或窥探运行时数据。
- 受控编译路径可注入默认 LIMIT；外部校验和 Query Gateway 使用显式 LIMIT 模式，
  超过来源 `max_rows` 或全局上限时收敛到更小的上限。超时通过只读事务的
  `statement_timeout` 与应用层 deadline 双重约束。

### Replica, budget, and masking

- PostgreSQL 执行器只接受组织内只读 DataSource 绑定的 Connector；Connector 标记为
  primary 或明确 `read_only=false` 时 fail-closed。执行始终在只读事务中进行，凭证由
  Gateway 短暂解析并在 finally 清除。
- Query Gateway 在执行前调用 PostgreSQL `EXPLAIN (FORMAT JSON)` 计算扫描估算，并与
  DataSource/Connector `scan_budget` 比较；预算不可用或超限均拒绝，不以“查询成功”代替
  安全检查。执行结果按 DataColumn `mask_policy` 进行 mask/hash，再交给统一 Evidence。
- `sql.validate` 返回规范化 SQL、表列集合、LIMIT、扫描估算和警告；`sql.explain` 返回
  同一策略快照以及 `audit_id`，审计记录只保存脱敏的策略维度，不保存凭证或参数原文。

### Phase 15 自动化验收映射

- `test_phase15_sql_gateway.py` 覆盖显式 LIMIT、危险 SQL、EXPLAIN/ANALYZE 边界、扫描
  预算和可审计解释计划；既有 SQL policy 测试保持兼容自动 LIMIT 编译路径。
- Data catalog、Harness/Data route、Evidence、Audit、OpenAPI、Python/TypeScript SDK、
  PostgreSQL、静态 Error/Event、Compose 与 Helm 门禁继续有效。

Phase 15 仍等待 SQL 安全与数据平台人工确认：

> 只读副本拓扑、扫描预算阈值、行策略、列脱敏策略、EXPLAIN 权限和生产连接器凭证需由
> 数据平台/安全负责人确认；自动化拦截测试不替代生产安全签字。

**状态：PENDING — 不阻塞连续开发，也不得被表述为人工批准。**

人工复核材料见 [`docs/architecture/phase-15-sql-gateway-gate.md`](docs/architecture/phase-15-sql-gateway-gate.md)。

## Phase 16 DataAgent 打穿冻结合同

### Governed DataAgent route

- 含已登记 Metric 的问数（包括“为什么某指标下跌”）由 Harness 内部固定到
  `data-agent` 与 `governed-analytics` Skill；用户仍只看到 GeneralAgent。没有已验证
  Metric 的问题继续返回 `metric_not_resolved`，不能退化为自由 Text2SQL。
- DataAgent 只能使用 `metric.describe`、`schema.search`、`sql.validate`、`sql.explain`、
  `data.query` 和 `data.preview` 等已注册能力；问数根因只在语义维度上分段，绝不从
  日志/Trace 推断原因。计划快照保存 Agent/Skill 版本和 checksum，能力执行仍经过
  Gateway/Policy。

### Result and artifact contract

- Data 请求遵循 Understanding → Agent Routing → Semantic Resolution → Logical Plan →
  SQL AST → Policy → Query Gateway → DATA Evidence。回答的每一条结论绑定当前 Run 的
  DATA Evidence；Evidence lineage 保留指标定义、来源表、Query fingerprint 与策略事实。
- 成功问数始终生成 SQL、TABLE 和可选 CHART Artifact。表格含稳定列顺序、受控行数和
  指标定义；带日期/时间维度的数值结果生成可审计 Vega-Lite 趋势线，其 usermeta 绑定
  Metric、Evidence 和 SQL fingerprint。
- 结果为空、查询失败或缺少 DATA Evidence 时，不生成事实性高置信 Claim；不使用模型
  猜测填充数据。SQL 与指标定义可从同一 Run 的 Artifact/Evidence 链路复核。

### Phase 16 自动化验收映射

- `test_phase16_data_agent.py` 验证“为什么指标下跌”仍固定 DataAgent、Skill 和 DATA
  能力边界，不触达日志/Trace；`test_data_artifacts.py` 验证 SQL/表/趋势图 Artifact、
  指标元数据和时间维度编码。
- Phase 12–15 的 Knowledge、语义编译、SQL AST、Evidence、Audit、OpenAPI、SDK、前端、
  PostgreSQL、Compose、Helm 与静态 Error/Event 门禁继续有效。

Phase 16 仍等待 DataAgent/数据产品人工确认：

> 黄金问数集、指标口径、维度选择、空结果/异常结果语言、执行成功率阈值和生产数据源
> 质量需由数据产品负责人确认；自动化链路通过不替代业务验收签字。

**状态：PENDING — 不阻塞连续开发，也不得被表述为人工批准。**

人工复核材料见 [`docs/architecture/phase-16-data-agent-gate.md`](docs/architecture/phase-16-data-agent-gate.md)。

## Phase 17 只读可观测连接器冻结合同

### Bounded connector surface

- `metric.query`、`metric.compare`、`metric.anomaly`、`log.search`、`log.aggregate` 和
  `deployment.list` 是本阶段唯一新增的可观测操作；它们通过已注册的 HTTP
  `observability.v1` Connector 进入 Capability Gateway。Trace 大盘、Kubernetes
  重启、部署写入和任何其他副作用不属于此阶段。
- 每个请求都携带明确 operation、service、UTC 时间窗和查询边界。Connector 只接受
  只读 operation allowlist，并在 Gateway 的身份、策略、风险、Grant、限流、凭证、
  超时、审计和 Evidence 链路之后访问外部系统；不存在 Agent 直连 HTTP 的旁路。

### ObservabilityEvent contract

- 外部响应先经过 allowlist 归一化，统一输出 timestamp、service、environment、trace_id、
  request_id、脱敏 user/order 标识、deployment_id、commit_id、host、pod、severity，
  业务字段只放在受限 `attributes` 中。
- Prometheus 风格 series、事件/项目列表和嵌套 data/result 响应都映射到同一
  `{operation, events, count, next_cursor}` 结构；未知记录形状、错误 payload、上游
  HTTP 错误和超时返回稳定结构化错误，不把供应商原文或凭证写入 Evidence。
- Gateway 的统一 EvidenceFabric 持久化归一化结果、观察时间、来源、权限、分类和
  connector/policy lineage；`tool.completed` 与审计记录携带 Evidence ID，失败则保留
  `tool.failed`、稳定错误码和审计结果。

### Phase 17 自动化验收映射

- `test_phase17_observability.py` 验证 Prometheus series 和 HTTP 事件的统一字段、操作
  allowlist、边界请求、错误 payload 和无秘密归一化。
- Registry、Gateway、Policy、Evidence、Audit、OpenAPI、SDK、前端、PostgreSQL、
  Compose、Helm 以及静态 Error/Event 门禁继续有效；IncidentAgent 仍只读取已声明能力，
  不会因本阶段连接器而获得写操作。

Phase 17 仍等待可观测性平台与安全负责人确认：

> Prometheus/日志/发布系统的具体字段映射、service 与环境 ACL、时间窗和查询配额、
> 生产 egress/凭证、Evidence 分类与保留周期需由系统所有者签字；自动化归一化与错误
> 测试不替代生产连接器批准。

**状态：PENDING — 不阻塞连续开发，也不得被表述为人工批准。**

人工复核材料见 [`docs/architecture/phase-17-observability-gate.md`](docs/architecture/phase-17-observability-gate.md)。

## Phase 18 只读 Git/变更连接器冻结合同

### Bounded change surface

- `git.commit`、`git.diff`、`git.history`、`deployment.commit` 和最多一个受控的
  `code.search` 操作通过 `engineering.v1` HTTP Connector 提供只读 Commit↔Deployment
  线索；自动 PR、完整 Code Graph、AST 全家桶、部署/配置写入和重启不属于本阶段。
- 请求必须显式携带 operation 与组织批准的 repository；Commit/Deployment 查询还需
  commit/deployment 标识或有界 UTC 时间窗。Connector 可配置 repository allowlist，
  越界在外部请求前 fail-closed，凭证仍只由 Gateway 短暂解析。

### Change Evidence contract

- Provider 响应先归一化为 `{operation, items, count, next_cursor}`；每个 item 固定包含
  timestamp、repository、commit_id、deployment_id、service、environment、author_hash、
  title、status 和受限 attributes。Patch、message 与文件列表只在 allowlist 内保存并
  递归脱敏、限长。
- 归一化后的 CODE/DEPLOYMENT 结果进入统一 EvidenceFabric，保留来源、观察时间、权限、
  connector/policy lineage、审计和 Evidence ID；上游错误、超时、非法响应和不允许的
  repository 返回稳定结构化错误，不把 provider 原文或密钥写入轨迹。

### Phase 18 自动化验收映射

- `test_phase18_engineering.py` 验证 Git diff/Deployment commit 归一化、敏感 patch 脱敏、
  operation 边界和 repository allowlist。
- Registry、Planner、Gateway、Policy、Evidence、Audit、OpenAPI、SDK、前端、PostgreSQL、
  Compose、Helm 与静态 Error/Event 门禁继续有效；IncidentAgent 只能使用 AgentSpec 中
  声明的只读变更能力。

Phase 18 仍等待代码平台、发布平台与安全负责人确认：

> repository/branch ACL、Commit↔Deployment 字段映射、diff 保留与脱敏、时间窗和配额、
> 生产凭证/egress 以及无权限仓库的 DENY 语义需由系统所有者签字；自动化连接器测试
> 不替代生产批准。

**状态：PENDING — 不阻塞连续开发，也不得被表述为人工批准。**

人工复核材料见 [`docs/architecture/phase-18-engineering-gate.md`](docs/architecture/phase-18-engineering-gate.md)。

## Phase 19 IncidentAgent 与证据融合冻结合同

### Bounded investigation contract

- IncidentAgent 只使用 AgentSpec 中声明、并由 Capability Gateway 授权的只读能力，执行
  “指标基线 → 异常窗 → 维度下钻 → 发布关联 → 日志/Trace → 配置/代码差异”的有界依赖
  计划。没有修复、重启、改配置、部署写入或自动 PR 路径。
- 每个外部结果先由 EvidenceFabric 归一化。融合器只读取当前 Run 的不可变 Evidence，按
  时间、service、environment、deployment 和 commit 做可解释关联，最多生成三个候选根因。

### Candidate root-cause contract

- 候选根因始终标记为候选，不等同于已确认结论；输出包含 rank、支持度、reason codes、
  Evidence ID 和 Evidence 类型，另存受限时间线与未解决冲突。
- 根因 Claim 必须绑定至少两种不同 Evidence 类型（例如 METRIC+DEPLOYMENT 或
  LOG+CODE）。只有单一来源或单一类型的信号不能生成根因 Claim；required Evidence 缺失
  时回答仍可展示收集结果，但验证状态必须为 PARTIAL。
- 独立验证在模型可用性之外运行：Claim 链接、Evidence 类型覆盖、重复证据、来源多样性
  和显式冲突都会进入现有 `critic.completed` 与回答 Artifact 的 verification projection。

### Phase 19 自动化验收映射

- `test_phase19_incident.py` 覆盖跨类型融合、Top1/Top3 排名、时间线、冲突保留、Claim
  双类型门槛和有序 Incident 计划；现有 Gateway、Policy、Evidence、Audit、Replay 与
  注册表门禁继续有效。
- 黄金事故 Run 输出支持 `minimum_incident_candidates`、`minimum_cross_type_claims` 和
  `incident_top1_evidence_types` 断言，确保 Top1/Top3 与 Claim 证据覆盖可以回归。

Phase 19 仍等待可观测性、发布平台、代码平台与安全负责人确认：

> 指标基线与异常窗定义、维度 ACL、发布/Commit 关联、日志与 Trace 查询配额、证据保留、
> 候选根因措辞和生产凭证/egress 需由系统所有者签字；自动化融合和测试不替代人工批准。

**状态：PENDING — 不阻塞连续开发，也不得被表述为人工批准。**

人工复核材料见 [`docs/architecture/phase-19-incident-gate.md`](docs/architecture/phase-19-incident-gate.md)。

## Phase 20 Critic、治理控制面与生产硬化冻结合同

### Independent verification contract

- `Critic` 与执行 Agent 解耦，只读取当前 Run 的不可变 Evidence、Claim 链接和脱敏回答，
  不调用 Model 或 Capability。验证规则覆盖问题覆盖、必需证据、Claim 完整性、时间区间、
  指标定义/单位/环境、SQL 只读可靠性、事故替代解释、重复来源和冲突。
- 规则结果保持确定性并可 Replay。冲突保留稳定 reason code；证据不足、冲突或口径不一致
  时只发布 `PARTIAL/WITHHOLD`，不会把模型的高置信措辞升级为事实。事故 Claim 继续要求
  至少两种不同 Evidence 类型。
- 每次验证写入 `verification_assessments`、`claim_verification_results`、
  `verification_evidence_links` 与可关联的 `evidence_conflicts`。PostgreSQL 延迟约束、
  不可变触发器和发布策略外键共同保证 VERIFIED 结果必须覆盖全部 Claim 且具备策略决策。

### Governance and production boundary

- 管理控制面覆盖 Users、Roles、Departments、Models、Agents、Skills、Capabilities、
  Connectors、Data Sources、Policies、Approvals、Audits、Evaluations、Costs、Prompts、
  Knowledge 与 Secrets 元数据；所有读写按组织和管理员权限隔离并写入审计。
- Connectors、Model Gateway 和 Action 审批仍是唯一外部执行路径。生产环境的写入、部署、
  重启、数据库修改和自动修复保持 DENY；配置中的敏感键和值、凭据引用和解析后的密钥不
  返回浏览器或进入模型上下文。
- 审批通过/拒绝在事务内校验操作者、过期时间、PENDING 状态和运行状态；拒绝会终止等待
  Run，批准只恢复原 Capability Step，不扩大能力或环境边界。

### Phase 20 自动化验收映射

- Critic 单元与黄金 Knowledge/Data/Incident Run 回归验证独立规则、降级口径、Claim 链接、
  Replay 一致性和跨类型事故证据。
- 管理 API/控制台、凭据不泄露、所有 Tool Call 经过 Policy、PostgreSQL 验证聚合约束、
  Compose、Helm、OpenAPI、SDK 与静态 Error/Event 门禁继续有效。

人工复核材料见 [`docs/architecture/phase-20-production-gate.md`](docs/architecture/phase-20-production-gate.md)。
