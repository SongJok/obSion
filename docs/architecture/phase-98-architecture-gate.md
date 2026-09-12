# Phase 98 architecture review: local operator access and Yunxiao readiness

## 2026-09-11 connection increment

Decision: [ADR 0105](../adr/0105-pat-catalog-and-stream-trust.md). The increment
preserves the single Python control plane, existing PostgreSQL schema, explicit
credential references, Policy/Gateway authorization and immutable delivery
lineage. PAT-wide metadata has a dedicated operator scope with current identity
and Connector checks before and after I/O; it does not automatically grant
repository or Agent access. File normalization still enforces commit/path, size
and blob integrity. Repeated binding cannot change scope or reactivate a disabled
binding. Stream initializes trusted CAs without disabling TLS and emits only
content-free receive diagnostics.

Real test evidence now includes the requested administrator's login, point-to-point
DingTalk Inbox/Run/reply/SUCCESS/READ, complete PAT catalog enumeration (two
organizations, 129 repositories), 129 successful repository reads, and one
successful pinned-commit/file read chain per organization. The earlier
pending lists below are historical requirements; current outcomes and remaining
items are in [M1c validation](../phases/productization-m1c-validation.md).
No schema migration is needed for this increment; isolated PostgreSQL migration
round-trip evidence is included there. Phase 99/production promotion remains closed.

**Status: PASS for repository, local development and the documented test-tenant
connection slice.** Broader UAT and Alpha.1 production promotion remain **PENDING**.

This is the single architecture gate for [Phase 98](../phases/PHASE-98-REPORT.md).
It consolidates the local password credential review and the Yunxiao readiness
review. Their acceptance boundaries and evidence remain separate: local password
validation does not satisfy Yunxiao live validation or production approval.

## Local password credentials

## Review question

Can Obsion establish the first local administrator and a usable Alpha.1 development
session without adding a second identity/authorization path, exposing plaintext,
weakening OIDC, or sending sensitive traffic to an unapproved model endpoint?

**Status: PASS for repository and local-development scope. Alpha.1 production
promotion remains PENDING on the existing operator-owned gates.**

### Delivery contract

- `POST /api/v1/auth/password-session` is a public credential-exchange endpoint only.
  It validates browser origin, resolves an already provisioned identity, rotates the
  ordinary revocable session, and returns the existing `AuthSessionView`.
- `obsion provision-user` is the sole bootstrap enrollment path. It is local CLI only,
  organization-scoped and idempotent, grants only a declared system role, never accepts
  a password argument, and refuses the weak-password override outside development/test.
- `user_credentials` stores only a salted scrypt envelope and bounded lockout metadata.
  PostgreSQL row locking serializes counter changes; rejected attempts commit.
- Unknown, ambiguous, missing-credential, wrong-password, and malformed-credential
  paths fail closed. Nonexistent identities still perform a bounded sentinel scrypt
  derivation so equal response bodies do not hide a timing oracle.
- Workbench password and access-token tabs converge on the same Principal/session.
  Mode changes and failed exchanges remove secrets from component state.
- `.env.example` covers every `Settings` field plus local Compose/operator variables.
  When an ignored `.env` exists, its key set must match the example exactly. Optional
  Compose loading applies only to the API; internal service URLs override host values.
- Model credentials resolve only inside Model Gateway through
  `env://OBSION_AI_API_KEY`. A local non-private endpoint may serve PUBLIC/INTERNAL
  Runs through logical profiles; the private profile is deliberately unbound.

### Automated acceptance map

- `test_phase98_password_authentication.py` covers derivation, normalization, policy,
  parameter bounds, equivalent unknown-user work, enrollment/rotation, lockout,
  origin checks, browser exchange, CLI argument safety, environment completeness,
  and Compose credential scope.
- `session-gate-interactions.test.tsx` drives the password/token tabs, submission,
  rejection, lockout guidance, and secret clearing through the mounted component.
- `test_postgres_phase98_user_credential_migration.py` runs upgrade, downgrade, and
  re-upgrade in a dedicated disposable PostgreSQL database and verifies the complete
  column/constraint/index contract.
- Static Error/OpenAPI gates cover the new public route and error codes. The full
  repository quality, migration, SDK, and candidate-contract gates remain mandatory.

### Local validation evidence

- The requested administrator is active in the local PostgreSQL organization, holds
  the admin role, and has one unlocked scrypt credential row; no plaintext is stored.
- A local endpoint was created through the audited admin API and bound to `fast`,
  `reasoning-high`, and `coding-high` only. A Harness Run completed through the durable
  Workspace → Thread → Turn → Run → Step → Event model with a successful ModelCall,
  `answer.delta`, and `run.completed`.
- The opt-in, non-sending live Feishu suite passed with process-injected operator
  credentials. That original validation did not cover DingTalk or WeCom;
  DingTalk test-tenant results were subsequently added in the increment above.

### Remaining gates

Clean staging deploy/UAT, staging-scoped timed restore, HIGH/CRITICAL registry policy,
artifact signatures, live OIDC/secret manager/read replica, security/data-owner
approval, and maintainer publication authority remain PENDING. No local test or CI
result is allowed to fabricate those approvals or publish with `ci.txt`.

## Yunxiao readiness

## Review question

Can a Yunxiao personal access token list every repository it can see without
giving an Agent the token, a remote MCP session, or a write-capable tool catalog?

**Current status: test-tenant catalog/repository and sampled source reads PASS.**
The original contract-only review was pending. Secret-manager injection, complete
scope inventory, staging deployment, security review and accountable production
approval remain operator-owned gates; local PAT evidence does not satisfy them.

### Contract

```text
active binding -> Principal -> Policy -> connector grant -> schema/rate limit
  -> CredentialBroker -> X-Yunxiao-Token REST GET -> normalized repositories
  -> output validation -> masking/Evidence -> audit and telemetry
```

- The only capability is `yunxiao.repositories.list` (`code.read`, `CODE`, L1,
  HTTP, `NONE`). Its input permits only operation, page, per-page, and search.
- It is first-party HTTP, rather than a remote Streamable HTTP or stdio MCP
  integration. Remote MCP violates the in-process MCP contract and the official
  default tool set includes writes.
- The PAT is an opaque secret-manager value. It is sent only as
  `X-Yunxiao-Token`, never as a query parameter or generic Bearer credential.
- The central endpoint is `https://openapi-rdc.aliyuncs.com`; the connector first
  enumerates PAT-visible organizations and then reads each fixed Codeup repository
  path. A regional endpoint must be HTTPS, explicitly allowlisted, and declares the
  organizations it is permitted to read.
- Repository output is allowlisted metadata with bounded descriptions and sanitized
  URLs. Vendor error text, avatars, unrecognized fields, credentials, and response
  bodies are not exposed.
- `connectors/examples/yunxiao-read-only.yaml` is a `DRAFT` declaration. Builtin
  registration creates no Yunxiao Connector or CapabilityBinding.

### Automated acceptance map

- `services/control-plane/tests/test_phase98_yunxiao_read_only.py` verifies the
  fixed PAT request header, no Bearer/request body/query secret, all visible
  organization discovery, bounded normalization, exact egress, opaque credential
  contract, malformed response rejection, no inline configuration credential, and
  Policy denial before credential resolution or executor invocation.
- Existing Gateway tests continue to pin Policy, grants, schema validation,
  rate-limiting, output validation, audit, telemetry, masking, and Evidence around
  all HTTP connector execution.

### Pending operator evidence

The repository provides `make validate-yunxiao-live` and the operator procedure
in `docs/operators/yunxiao-live-validation.md`. This bounded PAT-visible
repository smoke test is readiness input, not a promotion gate by itself. It
must be run with an operator-injected secret before claiming live validation.

- Store a least-privilege PAT under a `secret://` reference without committing it.
- Create a DRAFT connector using the example, approve its exact endpoint/egress,
  then explicitly activate and bind it in a staging organization.
- Demonstrate allowed and denied Principals, PAT-visible organization enumeration,
  empty-result behavior, pagination, audit records, and token redaction.
- Attach staging/UAT, secret-manager, OIDC, recovery, signing, vulnerability, and
  human security/data-owner evidence to the Phase 98 promotion record. Local mocks
  do not satisfy these gates.
# 2026-09-11 问答可用性配套门禁

Phase 98 当前问答增量使用 [ADR 0106](../adr/0106-answer-publication-and-chinese-retrieval.md)
与 [专项架构门](productization-answer-quality-gate.md) 验证中文召回、严格模型结果和真正执行的
WITHHOLD 发布屏障；其结果不覆盖下述既有生产门禁或人工签署。

日常问答增量正在验收：见 [验证记录](../phases/productization-everyday-answer-validation.md) 与
[架构门禁](../architecture/productization-everyday-answer-gate.md)。不代表完整 M1 或生产晋级。

Stream 建连稳定性增量见 [验证记录](../phases/productization-stream-stability-validation.md) 与
[架构门禁](../architecture/productization-stream-stability-gate.md)；真实收件闭环仍在验证。

企业知识金额与比例来源检查见 [验证记录](../phases/productization-quantity-grounding-validation.md) 与
[架构门禁](productization-quantity-grounding-gate.md)。其范围不包含完整语义核验或生产晋级。

独立原文复核使用 [专项门禁](productization-independent-review-gate.md)，部署模型 ID 限制使用
[专项门禁](productization-model-allowlist-gate.md)。真实问题可用性与失败拦截分别验证，生产门禁保持。

知识正文投影与精确选源使用 [专项门禁](productization-precise-source-gate.md)，不以全部检索命中代替实际引用支持。

组织绑定的 Wiki v2 目录增量使用 [专项门禁](productization-dingtalk-org-documents-gate.md)。
正文和权限完整性与目录成功分开验证，不推进正式阶段或生产晋级。

2026-09-11 补充 ADR0114：开发受管钉钉正文已完成10篇真实Gateway/Policy读取，
3篇解析完整、7篇有明确缺口，81项专项/兼容/契约通过。来源正文入库、持久自动同步、
撤销检索屏障及真实企业问答仍在实施；不推进阶段或生产门禁。详见
[组织文档验证](../phases/productization-dingtalk-org-documents-validation.md)。

2026-09-11 ADR0115 增加受管知识入库与来源访问屏障，以及持久扫描状态/过期任务token；
真实3篇在隔离PostgreSQL入库可检索，关闭来源后拒绝，7篇PARTIAL不用于问答。28项双库回归
与迁移往返通过。自动循环、持久blob及点仔企业文档问答仍未完成，见组织文档专项报告。

2026-09-12：ADR0116接上ADR0115持久来源事实的开发宿主Worker；全部远端操作经Gateway/
Policy，进度与租约位于PostgreSQL，MinIO保存正文，DWS凭据仍在宿主。真实恢复和撤销证据
见钉钉组织文档专项门禁。正常环境接入、发布再授权和完整产品化尚未验收，不推进生产。


2026-09-12 ADR0117：来源管理API/Web支持同主体连接选择、分页状态和暂停/恢复/重扫；恢复需重新核验，页面可用数量取实际访问屏障。隔离浏览器与真实zziv后台完成3可用→暂停0可用，3篇访问均拒绝。专项57passed/17显式跳过、Web221passed，复用e1f3a6b8c5d7；详见 [ADR0117](../adr/0117-knowledge-source-management.md) 与来源文档专项验证。正常运行环境、最终发布/历史再校验和完整M1仍未完成。


2026-09-12 ADR0118：受管文档模型作者前、复核前及复核后重新检查Policy、来源权限和版本，变化时阻止候选答案发布。9个合成Harness场景和契约最终33passed，241源码Mypy与Ruff1014格式通过；Web221passed及最新构建通过。无新增迁移或真实模型调用。历史访问、派生副本、IM发送和最后检查后的并发撤权尚未关闭；[ADR0118](../adr/0118-managed-answer-access-checkpoints.md)明确这些普通环境接入前置项。


2026-09-12 ADR0119：历史答案、证据、产物、事件与平台派生副本重新核验当前来源；对话只继承实际使用的来源，普通问答不受无关旧资料影响。钉钉排队及发送前同时核验来源和可信组织，变化时阻止Gateway调用。任务状态保留，失权的计划/上下文与前端缓存答案隐藏。PostgreSQL发现并修复回放父子记录写入顺序，五个真实数据库场景全部通过；14项合成Harness、Web222项、244源码Mypy及Ruff1018格式通过。复用既有迁移。完整Python回归2502passed/240skipped/7deselected（820.64秒），1处旧测试夹具未装配新增权限组件；补真实组件并修复AppServer重试缓存权限后，最终组合68passed（87.55秒）、五项PostgreSQL重验与全包269源码Mypy/Ruff1019格式通过。该复测不冒充另一次全量全绿；正常运行环境尚未部署，最后检查至提交/网络发送间的并发撤权仍待实现与验证。详见[ADR0119](../adr/0119-historical-managed-source-access.md)。


2026-09-12 ADR0120：最终发布与新版DingTalk Outbox使用同组织PostgreSQL共享事务屏障；23类来源/权限事实变更由数据库触发器取得排他屏障，撤权与发布按提交顺序生效。真实并发10项通过（9.37秒），包括Harness、Outbox、等待超时后的同条任务恢复，以及连接JSON序列化顺序；f2a4b6c8d0e1迁移往返和无差异通过。来源/Worker/读取/回放/Outbox/错误契约118项通过（89.99秒）。新增单聊/群聊30秒总发送时限及投递/错误契约最终119项通过（41.56秒），270源码Mypy及Ruff/1023格式通过。普通环境未部署；远端租约、厂商POST前新鲜度、旧IM跨进程明文边界及真实文档问答继续验收。见[ADR0120](../adr/0120-source-publication-serialization.md)。


2026-09-12 ADR0121：单聊/群聊取得令牌后、消息POST前重查租约、资料、组织及群受众；群答案对每个成员核验来源，无权成员存在时仅发固定状态。旧IM明文接口拒绝受管正文，业务回滚后另事务保留Policy关联拒绝审计。最终167项通过（110.97秒）、9项隔离PostgreSQL场景通过；270源码Mypy、Ruff/1025格式与秘密扫描通过。复用f2a4b6c8d0e1，无新迁移或普通环境部署；真实点仔文档问答和完整M1继续验收。 详见[ADR0121](../adr/0121-final-dingtalk-send-authorization.md)与[验证账本](../release/evidence/productization/20260912-final-dingtalk-send.json)。


2026-09-12 ADR0122/0123：真实zziv持续同步+Kimi问答发现并修复例行扫描清除有效授权、状态审计等待模型Run锁两处问题。新增read_generation及a3b5c7d9e1f2迁移，6项同步PostgreSQL和12项并发/状态通过；最终79项回归（105.44秒）、270源码Mypy/Ruff1029通过。真实7次K3调用：日常翻译通过，两次知识答案及逐条原文复核通过，第二次持续状态可读且约44秒完成；未知数字未编造但弃答笼统，未计质量验收。暂停后企业历史拒绝、日常历史保留；隔离服务已停，普通环境未部署。此前因状态修复停止的全量不计通过；最终ADR0122/0123完整Python2532passed/252skipped/7deselected（678.51秒），该源码快照早于ADR0124；完整M1、点仔文档投递与格式覆盖继续推进。 详见[真实问答账本](../release/evidence/productization/20260912-managed-source-qa.json)、[ADR0122](../adr/0122-preserve-source-lease-during-refresh.md)和[ADR0123](../adr/0123-nonblocking-managed-run-status.md)。


2026-09-12 ADR0124：企业问答区分资料不足、生成格式错误和模型不可用；缺项主题仅允许摘取当前用户问题的原文，最终提示由本地生成，不发布模型弃答正文。真实zziv资料与一次K3调用约24秒返回明确的容量上限缺项说明；仍为WITHHOLD/未核验、无事实引用，不冒充独立复核。暂停后该历史答案拒绝且计划隐藏，日常历史保留。最终151项通过（99.47秒）、6项隔离PostgreSQL通过，271源码Mypy、Ruff/1031格式、秘密扫描通过；无新迁移，隔离服务已停止。前版完整2532项通过与本次增量分开记账；普通环境、点仔文档投递、完整格式和M1继续推进。 详见[ADR0124](../adr/0124-controlled-insufficient-evidence-replies.md)与[验证账本](../release/evidence/productization/20260912-controlled-abstention.json)。


2026-09-12 ADR0125：补齐普通启动的受管目录/正文能力注册，测试改用正式描述符，并验证管理员接口完整注册流程。整合Python2548passed/252skipped/7deselected（633.88秒），注册专项56项、最终HTTP2项及隔离PostgreSQL2项通过；271源码Mypy、Ruff/1033格式通过。已备份、真实备份恢复试升级并将日常API/Web升级到a3b5c7d9e1f2，修复本机5432端口冲突，项目宿主端口改为56324；246后端源码与运行镜像一致。zziv/Joony来源经正式API注册并由受监督宿主进程持续同步，3篇可用、7篇adoc不完整、1篇AI表格不支持、1篇空正文失败。点仔24/25/26均真实入站并单次成功投递，聊天正文与产物一致：四类内容逐项原文复核、容量问题明确弃答、同私聊正常英文翻译；共4次K3调用。三条回执UNREAD，不声称已读；两条上游入站约60秒延迟，完整格式、时延与M1/自主项目仍继续推进。 详见[ADR0125](../adr/0125-register-managed-source-capabilities.md)与[日常环境验收账本](../release/evidence/productization/20260912-normal-managed-source-qa.json)。


2026-09-12 ADR0126：保留编号、合并表格、分栏和重复代码，检索不拆开结构片段；最终Python2579passed/252skipped/7deselected（636.10秒）、85项专项及PostgreSQL版本/撤权/恢复通过。日常API247源码与固定宿主版本一致，授权可用文档3→6，原文档标识保留。5次真实K3调用：表格含义经独立复核通过，但答案出现内部证据编号；点仔额度问题明确弃答并成功投递；合同分类三个Claim均有据却被整体复核拦截，不计问答通过。另有一条已发送消息未见入站，一条约60秒发生在本地回调前；完整质量、格式、M1和自主项目仍在推进。 详见[结构与问答验证](../phases/productization-dingtalk-layout-validation.md)与[ADR0126](../adr/0126-preserve-dingtalk-document-layout.md)。

2026-09-12 ADR0127：统一正文复核与平台引用职责，保留经本地校验的整体支持/问题完成判断；内部编号触发重验权限后至多重写一次，仍须事实复核。五条内容读取路径不再等待模型Run锁，最终PostgreSQL正常/撤权10项通过；整合Python2596passed/262skipped/7deselected（704.80秒）、专项86passed、273源码Mypy与Ruff/1044格式通过。日常API248源码与固定宿主快照匹配，6篇可用且标识保留。隔离及点仔30/31共8次K3调用全部成功，四条答案完整复核通过；两条点仔聊天正文与产物一致、单次SUCCESS/UNREAD，分别103秒和80秒，其中合同入站前60.700秒。真实样例未触发重写，不宣称真实纠错验收。按用户新要求质量优先于速度，继续发展受控多轮分析、工具/文档调用和复核；完整M1和自主项目未完成。 详见[复核与纠错验证](../phases/productization-grounding-contract-validation.md)和[ADR0127](../adr/0127-align-grounding-and-citation-presentation.md)。


2026-09-12 ADR0128：受控搜索/逐页读取、自主重规划及完整复核已部署到日常开发环境，修复资料路由、模型不可用伪验证和密级丢失。最终Python2650通过/262跳过/7排除（751.92秒），13项真实PostgreSQL及1项真实备份演练入库通过；278源码Mypy、Ruff/1057格式和秘密扫描通过。真实K3完成虚构资料READ后复核、企业差旅表原文复核，以及企业容量问题三轮查阅后明确资料不足。两个Kimi端点按用户许可经Policy/API扩大密级范围，真实企业资料保持RESTRICTED；日常API253源码匹配、宿主版本固定、6篇资料恢复且标识保留。点仔32已完成三轮查阅，7次K3调用；34日常翻译1次K3，两条实际聊天与产物一致、单次发送并获SUCCESS；33未观察到入站，不计通过。完整M1/M2/M3、全部文档格式及自主项目仍未完成。 详见[ADR0128](../adr/0128-governed-knowledge-investigation.md)与[验证报告](../phases/productization-investigation-validation.md)。

2026-09-12 ADR0129：修复历史UNKNOWN投递缺失对账要求时间，以及旧c9d1回退约束遗漏UNKNOWN。新增数据迁移b4c6d8e0f2a4；真实PostgreSQL往返1项通过（7.53秒），覆盖原数据保留、幂等、非法状态变更拒绝、三种非SENT状态回退阻断和事务回滚。临时库已删除；日常迁移已升级，远端CI待完成；依赖扫描缺口及发现的漏洞已修复。 详见[ADR0129](../adr/0129-legacy-im-reconciliation-migration.md)。

2026-09-12 ADR0130：修复Next.js、sharp、pypdf和js-yaml已知漏洞；新增完整Python锁定依赖SBOM扫描，补足Trivy无法解析多根uv工作区的缺口。修复后文件系统及Python SBOM高危/严重发现均为0，原扫描门槛不变；前端277项及构建通过，最终Python2651通过/262跳过/7排除（839.67秒）、真实PostgreSQL集成405通过/10跳过，另有独立IM迁移往返通过。普通API/Web修复镜像及新迁移已部署，6篇文档可用且标识保留；远端CI待重验。 详见[ADR0130](../adr/0130-complete-dependency-security-scan.md)。
