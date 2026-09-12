# Phase 98 report: local operator access and enterprise connections

Architecture review: [Phase 98 consolidated gate](../architecture/phase-98-architecture-gate.md),
including local password credentials and enterprise connection validation.

## 2026-09-11 connection validation increment

[ADR 0105](../adr/0105-pat-catalog-and-stream-trust.md) adds the governed PAT-wide
inventory REST endpoint, Stream CA initialization and content-free diagnostics,
idempotent capability binding with explicit conflict responses, and strict
compatibility for the real Codeup decimal-string file-size representation.
Existing local edits from ADR 0104 remain part of this validation snapshot.

The designated administrator's password login succeeded. Two actual DingTalk
messages were admitted into persistent Inbox and Harness Runs; the second returned
a cited answer and a vendor SUCCESS/READ receipt. The cloud catalog exhausted two
organizations with 129 unique repositories. All 129 were explicitly registered
with restricted personnel ACLs and passed native repository reads through the
deployed REST/Policy/Gateway; the administrator's cookie session sees all 129.

No schema change is required. An isolated PostgreSQL IM migration round trip
(historical upgrade, downgrade, upgrade to head and drift check) passed, and its
temporary container was removed. Validation counts, file-read results, failure
history and remaining product gates are maintained in the
[M1c validation record](productization-m1c-validation.md).
These are local deployment/test-tenant results, not production promotion or
completion of M1's group, four-intent creation and wider UAT gates.

## What was implemented

Alpha.1 had no way for an administrator to sign in. The only credential
exchange was `POST /auth/session`, which accepts an OIDC access token in
`OIDC` mode and a shared development token in `DEVELOPMENT` mode. A
deployment that federates its people through OIDC therefore had no path
for the local administrator who has to configure that provider in the
first place. Phase 98 closes that gap with a locally enrolled password
credential.

- **Derivation** (`security/passwords.py`): scrypt through
  `hashlib.scrypt`, stored in a self-describing
  `scrypt$n=…,r=…,p=…$<salt>$<key>` encoding so cost parameters can be
  raised later without invalidating enrolled credentials. Verification
  is constant-time (`hmac.compare_digest`). Input is NFKC-normalized
  before derivation so a credential enrolled from one input method
  verifies from another. Requested parameters are memory-bound
  (`128 × r × n ≤ 128 MiB`) so configuration cannot turn login into a
  local denial of service.
- **Policy** (`PasswordPolicy`): length bounds from settings plus a
  refusal of universally breached secrets. The policy is enforced at
  enrollment, never at verification — an already-enrolled credential
  does not become unusable when the policy tightens.
- **Storage** (`persistence/user_credentials.py`): one credential row
  per identity. `UserCredentialStore.enroll` creates or rotates it;
  `verify` resolves the identity by case-insensitive email, loads the
  row `with_for_update()`, and returns a `CredentialVerification`
  carrying `VERIFIED`, `REJECTED`, or `LOCKED`. Derivation is offloaded
  with `asyncio.to_thread` so a deliberately expensive KDF never blocks
  the event loop other requests share. A successful login whose stored
  cost parameters no longer match the configured ones is rehashed
  opportunistically.
- **Enumeration and resource-exhaustion defense**: unknown, ambiguous,
  and unenrolled identities execute the same configured sentinel scrypt
  work as a wrong password. Decoded and configured cost, block-size,
  parallelism, memory, plaintext-size, salt, and derived-key inputs are
  bounded before native derivation, so a corrupted row cannot request
  arbitrary CPU or memory.
- **Authentication** (`security/auth.py::authenticate_password_principal`):
  password login is an additional credential exchange, not a new
  `AuthMode`. It resolves a Principal through the same
  `load_principal_by_id` path as every other exchange, so roles,
  permissions, and organization scope are identical regardless of how
  the session was obtained.
- **HTTP surface** (`POST /auth/password-session`): validates browser
  origin, exchanges the credential, and rotates the same revocable
  HttpOnly session cookie `POST /auth/session` issues. The response is
  the ordinary `AuthSessionView`.
- **Provisioning** (`security/provisioning.py` +
  `obsion provision-user`): a CLI-only path that finds or creates the
  User inside a resolved organization, grants a system role, and
  enrolls the password. It is deliberately never HTTP-exposed, because
  it grants a role with no authenticated caller. The password is never
  accepted as an argument — arguments are readable by any local user
  through the process table — so it is read from piped stdin, then
  `OBSION_PROVISION_PASSWORD`, then an interactive `getpass` prompt.
- **Workbench** (`components/session-gate.tsx`): the login card now has
  an accessible `tablist` with 账户密码 selected first and 访问令牌
  retained. Switching modes clears the entered secret and any stale
  error. A failed attempt drops the secret from component state so it
  is not recoverable from a devtools snapshot.
- **Local environment and Model Gateway**: `.env.example` now covers
  every control-plane `Settings` field plus local operator inputs, and
  the ignored `.env` has the identical key set. Compose loads that file
  optionally into the API only; explicit container-network URLs remain
  authoritative. The local model credential is available only as
  `env://OBSION_AI_API_KEY`, and the configured non-private endpoint is
  bound to `fast`, `reasoning-high`, and `coding-high`, never `private`.

## Architecture decisions

- **Password login is a credential exchange, not an `AuthMode`.** Modes
  select how a deployment federates identity; the local administrator
  credential has to coexist with that choice rather than replace it.
  Both exchanges converge on one Principal loader and one session
  cookie, so authorization has a single implementation.
- **Enumeration resistance is a shared code path, not a convention.**
  Unknown email, enrolled-but-no-credential, and wrong password all
  return `CredentialOutcome.REJECTED` and surface as one
  `invalid_credentials` error. An email that matches users in more than
  one organization is rejected rather than guessed.
- **Lockout accounting lives on the credential row.** The failed-attempt
  counter and `locked_until` are columns on the same row the
  verification locks, so concurrent attempts cannot interleave into a
  lost increment.
- **A rejected attempt still commits.** `create_password_browser_session`
  commits inside its `except ObsionError` block before re-raising.
  Relying on request-scoped rollback would discard the failed-attempt
  counter and make lockout unreachable by simply retrying.
- **Equivalent negative work is part of authentication semantics.**
  Matching HTTP errors are insufficient when one path skips the KDF.
  Identity misses use a non-secret sentinel envelope with the configured
  parameters.

## Migration

`d7f31a9c4b28_add_local_password_credentials` adds `user_credentials`:
organization-scoped, unique per user, holding the encoded secret, the
failed-attempt counter, `locked_until`, `must_change`, and rotation
timestamps. It is additive; no existing column or row is rewritten.

## Validation

- `tests/test_phase98_password_authentication.py` — 28 tests covering
  encoding round-trips, NFKC normalization, memory bounds, policy
  enforcement at enrollment only, breached-secret refusal, rotation,
  opportunistic rehash, indistinguishable rejection plus equivalent KDF
  work, lockout threshold and expiry, cross-organization ambiguity,
  disabled-password-auth refusal, the full `POST /auth/password-session`
  cookie exchange, CLI argument safety, complete environment-key parity,
  API-only optional Compose injection, and Phase/status/release/CI bookkeeping.
- `apps/web/tests/session-gate-interactions.test.tsx` — 7 interaction
  tests: password mode first, trimmed-email exchange, inert submit until
  both fields are supplied, field-agnostic rejection message with the
  password cleared, lockout explained as temporary, token mode still
  functional, and mode switching clearing both error and secret.
- `test_postgres_phase98_user_credential_migration.py` — isolated
  PostgreSQL upgrade/downgrade/re-upgrade with complete column,
  constraint, and index snapshots; the CI migration matrix owns a
  distinct disposable database.
- The destructive migration jobs now upgrade from their historical test
  revision to the current head before Alembic drift detection. The prior
  ordering checked an intentionally old schema and could report later
  migrations as false drift after a successful round trip.
- Error catalog grew to 320 registered codes. The static
  error-producer manifest is checked against `analyze_error_producers()`;
  the gate confirms exact producer/code coverage with no unregistered or
  reserved-code drift.
- `docs/api/openapi.json` regenerated; the diff is purely additive.
- The phase-25 release-hardening gates were fixed to resolve repository
  fixtures from `__file__` instead of the invoking directory. Five of
  them previously passed only when pytest was started from the
  repository root.
- Local Alpha.1 validation completed one real Harness Run through the
  configured Model Gateway endpoint: seven Steps, a successful persisted
  ModelCall, `answer.delta`, and `run.completed`. The non-sending live
  Feishu suite passed four probes with credentials injected only into the
  child process.
- `make check` passed: 1,075 Python tests with 28 documented opt-in
  skips; 188 Web tests in 23 files; 17 Desktop, 12 IDE, and 24
  TypeScript SDK tests; strict formatting/lint/type checking; 38 Golden
  Dataset contracts; zero secret-scan findings; and no Alembic drift.
- A disposable PostgreSQL head schema passed 16 integration tests. Five
  isolated destructive migration databases (audit, Phase 2, Phase 5,
  Phase 79, and Phase 98) each passed their round trip, forward upgrade
  to head, and drift check, then were deleted.
- The optimized Next.js production build passed, the Java SDK passed six
  tests, and the Alpha.1 candidate contract retained all six PENDING
  operator gates with `promotion_eligible=false`.
- Control-plane, migration, and Web images built from the locked sources;
  Compose migrated and restarted to healthy API/Web services. Password
  exchange succeeded against the container API, and a containerized
  Model Gateway retry completed a seven-Step Harness Run with persisted
  input/output token accounting. One earlier provider response failed
  closed as a failed ModelCall while the Run published an evidence-safe
  response, demonstrating rather than hiding provider non-compliance.

## Remaining operator gates

Password login does not reduce any existing operator gate. Production
promotion, staging deploy, UAT, human security sign-off, live OIDC, and
the HIGH/CRITICAL CVE policy remain operator-owned and fail-closed.
`OBSION_PASSWORD_AUTH_ENABLED` lets a deployment refuse local passwords
entirely and rely only on its identity provider.

## 2026-09-11 问答可用性增量

[ADR 0106](../adr/0106-answer-publication-and-chinese-retrieval.md) 修复中文整句无法检索、模型非法引用
被部分接受及 WITHHOLD 仍发布原始答案的问题。实施与最终验证结果独立记录在
[问答验证记录](productization-answer-quality-validation.md)，对应
[架构门禁](../architecture/productization-answer-quality-gate.md)。本增量无需数据库迁移，
不重写历史答案，不代表完整 M1、语义正确率、自主项目任务或生产晋级已通过。

日常问答增量正在验收：见 [验证记录](../phases/productization-everyday-answer-validation.md) 与
[架构门禁](../architecture/productization-everyday-answer-gate.md)。不代表完整 M1 或生产晋级。

Stream 建连稳定性增量见 [验证记录](../phases/productization-stream-stability-validation.md) 与
[架构门禁](../architecture/productization-stream-stability-gate.md)；真实收件闭环仍在验证。

企业知识金额与比例来源检查见 [验证记录](productization-quantity-grounding-validation.md) 与
[架构门禁](../architecture/productization-quantity-grounding-gate.md)。这是有界的错误发布拦截，
不等于完整语义核验，也不推进生产阶段。

企业知识逐 Claim 原文复核见 [验证记录](productization-independent-review-validation.md) 与
[门禁](../architecture/productization-independent-review-gate.md)。本地模型限制见
[验证记录](productization-model-allowlist-validation.md) 与 [门禁](../architecture/productization-model-allowlist-gate.md)。
两者不代表完整 M1 或生产晋级。

正文投影和精确选源增量见 [验证记录](productization-precise-source-validation.md) 与
[门禁](../architecture/productization-precise-source-gate.md)。来源列举必须对应实际复核引用，历史记录不重写。

钉钉组织文档增量使用 [ADR 0113](../adr/0113-organization-bound-dingtalk-wiki-discovery.md) 与
[专项验证记录](productization-dingtalk-org-documents-validation.md)。已取得真实应用目录证据，
完整正文、来源权限及持久自动同步仍在继续；不能把目录发现计为企业文档问答验收。

2026-09-11 补充 ADR0114：开发受管钉钉正文已完成10篇真实Gateway/Policy读取，
3篇解析完整、7篇有明确缺口，81项专项/兼容/契约通过。来源正文入库、持久自动同步、
撤销检索屏障及真实企业问答仍在实施；不推进阶段或生产门禁。详见
[组织文档验证](../phases/productization-dingtalk-org-documents-validation.md)。

2026-09-11 ADR0115 增加受管知识入库与来源访问屏障，以及持久扫描状态/过期任务token；
真实3篇在隔离PostgreSQL入库可检索，关闭来源后拒绝，7篇PARTIAL不用于问答。28项双库回归
与迁移往返通过。自动循环、持久blob及点仔企业文档问答仍未完成，见组织文档专项报告。

2026-09-12产品化延续（ADR0115/0116）：增加来源访问租约、可恢复的单Python宿主文档同步、
同组织Connector pin及指定账号过期登录刷新。真实15节点后台周期中3篇完整正文持久化、
重建数据库/MinIO连接后读取通过、停用来源后访问拒绝；其余缺口显式记录。没有推进正式
阶段或部署当前API，完整企业问答与生产门禁仍待完成。详见钉钉组织文档专项报告/账本。


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
