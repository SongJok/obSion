# P1 冻结验收入口

当前实现状态：IN_PROGRESS。入口执行知识答案子集；独立模型评分已接线，真实评分校准、Worker 执行版本证明、
完整阶段硬门和签名部署证明尚未完成。任何子集成绩均不能推进 P1 或 Phase 99。

## 边界

`obsion acceptance freeze` 只通过受认证的 Obsion API 读取模型、提示、Agent、Skill、
Capability、Policy 和完整资料，固定配置摘要并逐条检查金标引文。它不访问供应商 API，
不向云效写入，也不会创建候选任务。

Profile读取现在包含组织内Endpoint绑定与优先级，纳入配置摘要；新增绑定也会使旧冻结
配置失效。响应继续隐藏供应商URL和凭据引用，不能由该快照推断密钥或完整部署版本证明。
详见[ADR0141](../adr/0141-freeze-model-bindings-and-clarify-score-quotes.md)。

`obsion acceptance run` 使用既有 Workspace → Thread → Turn → Run 入口，每个独立
案例新建 Thread。只传问题和模型配置名；答案、来源引文、预期结果不传入候选任务。
部署和配置在开始、每个案例前后及结束时复核，资料在任务前后经当前权限重新读取。
来源变化、撤权、模型/Agent 不符均阻断评分；等待用户、失败、取消、超时计入失败，
超时和意外等待会请求取消并保留取消响应，不把“请求已发送”当作已完成清理。

仅允许 HTTPS API 源站或 HTTP 回环地址；不接受 URL 内凭据，不跟随重定向，不继承
环境代理。凭据从 `OBSION_ACCEPTANCE_TOKEN` 读取，只发送给指定的 Obsion API。
该主体须有管理员只读目录权限，以及目标 Workspace/资料的正常任务权限。

## 冻结与执行

部署方设置 `OBSION_RELEASE_REVISION` 为完整 40 位提交 SHA，
`OBSION_RELEASE_IMAGE_DIGEST` 为不可变镜像 `sha256:…`。新增只读管理接口
`GET /api/v1/admin/runtime-identity` 如实返回这两项及环境；未配置返回 null。
它仅证明 API 服务的部署声明，**不证明签名有效、工作进程与 API 相同或源码工作区干净**。
这些限制明确记录为阶段阻断项，不能由运维自行填写字段后宣称生产验收通过。

已有审核任务集及摘要后，执行（尖括号均需替换为实际值；不要填占位模型/仓库冒充真实配置）：

示例路径中的企业保留集由操作者在授权环境提供，不随公开源码分发；缺少原文与冻结输入
时保持未验收，不使用合成资料冒充。真实试点清单和含原文引文的文件保留在本地忽略目录。

```text
obsion acceptance freeze --profile <new-profile.json> --candidate <full-commit-sha> \
  --api-url <obsion-api-origin> --image-digest <immutable-image-digest> \
  --workspace-id <workspace-uuid> --model-profile <registered-model-profile-name> \
  --dataset evaluations/productization/p1-heldout-v1.json \
  --dataset-sha256 <pre-reviewed-dataset-sha256>

obsion acceptance run --phase P1 --profile <new-profile.json> \
  --candidate <full-commit-sha> --output <new-private-evidence-directory>
```

冻结不会覆盖已有 profile；执行不会覆盖已有证据目录。任务集必须位于 `--root` 内，
摘要、阶段、保留集类型、唯一案例 ID 和每个必需来源必须匹配。不可把失败项改成可选项。
当前仅实现 P1 命令合同；P2—P5 驱动随对应阶段扩展。

## 报告与独立评分

新目录权限 0700，`inputs.json`、`profile.json`、`report.json` 权限 0600。报告在创建
Thread、创建 Run 和每项完成后检查点保存；保留失败与未执行的全部案例。任务创建不
自动重试，响应丢失时保持 BLOCKED，避免重复创建；已知任务支持下文的跨进程续查，
无标识的任务创建自动对账仍待补齐。

报告记录最终答案、答案摘要、Run/Thread/Artifact、Step、Evidence 引用、提示版本和
成本。导出统一脱敏；`answer_sha256` 绑定实际发布文本，`answer_export_sha256` 绑定
导出文本，`answer_export_redacted` 表示二者是否不同。完整来源正文只用于校验/评分，
不写进普通控制台输出；证据目录仍包含企业答案与金标，按对应资料权限保管。

`IndependentScorer` 合同只接收问题、审核原文、最终发布文本和 Run 引用；绝不接收
候选的 VERIFIED 作为分数。评分必须绑定最终答案摘要。CLI默认使用 `PendingScorer`，
所有已完成但未独立评分的答案保持 NOT_RUN。新增 `--scorer-model-profile <已登记配置名>`
可启用真实独立模型评分；该配置必须存在于冻结时的模型目录中。模型端点和权限仍由平台
管理，不能传供应商URL或额外凭据。测试中的显式合成评分器不随 CLI 启用。

评分经受认证的 `POST /api/v1/admin/evaluations/answer-score` 执行，需 `evaluations.write`
及当前任务/原文访问权限。服务器自己读取最终答案、问题和原文，对逐项规则、事实正确性、
任务完成度评分；权限、摘要、规则覆盖、引用精确性或模型输出异常均不能得分。调用前后
重新检查权限与内容，候选Run不发生状态或费用变化。每次评分网络预算45秒，最多32000
输入、4000输出和0.25费用；时间预算由模型网关管理，数据库失败账本正常保留。

报告包含评分模型/政策摘要，逐项结果关联Policy、模型调用及审计；恢复会重新评分并保留
原报告摘要，不能把重新评分后的不同结果冒充原来的结果。评测器校准、签名部署及Worker
版本证明尚未完成，因此即使知识子集PASS，完整阶段仍BLOCKED，退出码仍为2。
参见[ADR0138](../adr/0138-independent-published-answer-scoring.md)。

根据[ADR0139](../adr/0139-independent-score-publication-fence.md)，模型完成后到最终评分
审计提交之间复用既有数据库权限锁；模型执行期间不持锁。历史评分不能授予当前访问权限。
无效判断的最后一条证据和审计新增`judgment_diagnostic`固定分类，见
[ADR0140](../adr/0140-bounded-independent-judgment-diagnostics.md)。没有原始输出或异常全文，
保留原BLOCKED/reason；重复JSON键也拒绝。诊断分类不是恢复通过条件，不自动重复评分。

## 中断后的任务续查

新报告采用 schema_version=2，提交请求前写入 admission_state，观察到标识后保存
Thread/Run 及首次提交的截止时间。进程中断后，可以继续观察已知任务：

```text
obsion acceptance run --phase P1 --profile <same-profile.json> \
  --candidate <same-full-commit-sha> --resume-from <previous-evidence-directory> \
  --output <new-private-evidence-directory>
```

恢复要求原始资料、案例清单、profile 和部署摘要完全匹配。通过应用接口重新检查任务
所属空间、问题和上下文，以及当前来源权限和配置；实际答案重新读取和评分，旧分数不继承。
恢复后的报告保存原报告摘要，原文件保留不变。恢复可以再次使用新的报告作为前报告。
即使前置检查被配置漂移阻断，报告也保留任务定位与原始失败原因，修复配置后仍可继续核验。

恢复不会新建 Thread/Turn。创建响应丢失且没有 Run 标识时保持 BLOCKED，未开始的案例
保持 NOT_RUN；不会为补齐报告而重复提交。旧 v1 报告缺少可靠提交阶段和截止时间，因此
不自动恢复，也不补造历史字段。需要新的尝试时显式执行不带 --resume-from 的新验收。

等待预算从首次提交开始，恢复不重置。旧的超时或意外等待用户仍计 FAIL，即便后来完成。
取消失败记 `cancel_status=UNKNOWN`，应继续核查该 Run，不能认定资源已回收。
此能力未解决无 Run 标识的跨进程自动发现，不等于完整的任务创建幂等协议。

`answerable_accuracy` 分母保留全部可答案例，包括错误拒答、阻塞和未执行项；资料不足
单独统计。`status` 仅代表知识答案子集；`phase_status=BLOCKED` 和
`promotion_eligible=false` 表示完整阶段验收尚不具备。CLI 返回退出码 2，不能通过只读
子集 status 或手工修改报告实现晋级。

无数据库 schema 或历史数据迁移；新增 API 为兼容扩展，OpenAPI 已同步。后续持久化
工作进程版本和独立语义评分时，须另行补齐相应迁移、证据和门禁。
