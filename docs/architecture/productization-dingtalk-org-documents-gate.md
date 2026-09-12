# 钉钉组织文档架构门禁

日期：2026-09-11。状态：GOVERNED_INGEST_VERIFIED_AUTOMATIC_SYNC_IN_PROGRESS。

- 当前 Wiki v2 浏览经过已有 Gateway、Policy、凭据代理与审计；没有第二个后台。
- 组织由配置和真实返回 corpId 双向校验，操作者为已查询到的 unionId；不凭当前界面或第一个组织猜测。
- 仅组织 TEAM 空间，根节点来自同组织空间响应；新旧编号不混用。
- 完整分页、有界递归、循环与缺失集合拒绝已验证；图片、表格不假装是文字文档。
- 真实应用通过隔离 PostgreSQL REST 发现两个知识库和 14 个节点，有三条 Policy 关联审计。
- 正文入库、来源 ACL、持久自动同步与撤销、格式覆盖及问答验收仍待完成。不得推进完整 M1 或生产门禁。

参见 [验证记录](../phases/productization-dingtalk-org-documents-validation.md) 与
[ADR 0113](../adr/0113-organization-bound-dingtalk-wiki-discovery.md)。

ADR0114 增加显式开发SDK正文执行器；没有改造旧SDK/MCP echo为任意命令执行器。
固定宿主DWS只读命令和profile，密钥不经过模型。10篇实际Gateway读取与审计已通过；
3篇解析完整、7篇有缺口。读取前后权限核验不等于入库后撤权控制，持久同步与检索门禁
仍未通过。81项组合及233控制面源码Mypy通过，无schema迁移、部署或生产验收。

ADR0115：新增同组织来源事实、持久继续位置及读取授权租约；普通知识ACL之外的强制SQL
屏障阻止管理员、来源关闭、配置/凭据/grants替换、绑定/安装/连接版本撤销和过期授权绕过。
真实3篇入库可检索及关闭后拒绝已在隔离PostgreSQL验证，7篇PARTIAL不用于问答。28项双数据库
验证、迁移往返/无差异及静态检查通过。自动Worker、持久blob、最终发布再校验与真实点仔
文档问答仍为未通过项；完整M1/生产晋级不推进。

ADR0116 开发宿主Worker已真实完成逐页发现/正文读取/持久MinIO入库/重新连接后下载，来源
停用后访问拒绝。固定Connector pin防止跨组织同名能力选错连接，DWS登录过期可按指定账号
自动刷新并再核对身份。7篇内容仍PARTIAL、1篇为空，AI表格不假冒普通文档。单元和双数据库
回归及真实账本证明此增量；公开管理、普通运行环境部署、最终发布权限与点仔文档答案
仍未通过，完整M1及生产门禁保持未完成。见ADR0116和专项验证记录。


ADR0117 管理REST与Web来源操作在隔离环境通过：Policy/当前主体约束、拒绝审计、恢复不续期旧授权、可用数量复用检索屏障、多连接显式选择。实际浏览器恢复→真实后台扫描→3可用→窄屏暂停→0可用与3拒绝通过。专项57passed/17显式跳过、Web221passed；复用既有迁移。最终发布/历史权限屏障与普通环境部署尚未通过，因此该管理入口验证不替代完整M1门禁。见ADR0117与对应真实元信息账本。


ADR0118增加模型作者前/复核前/复核后当前来源访问检查，支持权限/版本变化时有审计地WITHHOLD，并刷新文档ORM权限状态。9个合成Harness场景与错误/契约最终33passed；Mypy241和Ruff1014格式通过。不等同于历史/副本/Outbox权限门禁或并发原子撤权；这些仍为正常环境接入前置项，无新的真实模型/IM验证。


2026-09-12 ADR0119：历史答案、证据、产物、事件与平台派生副本重新核验当前来源；对话只继承实际使用的来源，普通问答不受无关旧资料影响。钉钉排队及发送前同时核验来源和可信组织，变化时阻止Gateway调用。任务状态保留，失权的计划/上下文与前端缓存答案隐藏。PostgreSQL发现并修复回放父子记录写入顺序，五个真实数据库场景全部通过；14项合成Harness、Web222项、244源码Mypy及Ruff1018格式通过。复用既有迁移。完整Python回归2502passed/240skipped/7deselected（820.64秒），1处旧测试夹具未装配新增权限组件；补真实组件并修复AppServer重试缓存权限后，最终组合68passed（87.55秒）、五项PostgreSQL重验与全包269源码Mypy/Ruff1019格式通过。该复测不冒充另一次全量全绿；正常运行环境尚未部署，最后检查至提交/网络发送间的并发撤权仍待实现与验证。详见[ADR0119](../adr/0119-historical-managed-source-access.md)。


2026-09-12 ADR0120：最终发布与新版DingTalk Outbox使用同组织PostgreSQL共享事务屏障；23类来源/权限事实变更由数据库触发器取得排他屏障，撤权与发布按提交顺序生效。真实并发10项通过（9.37秒），包括Harness、Outbox、等待超时后的同条任务恢复，以及连接JSON序列化顺序；f2a4b6c8d0e1迁移往返和无差异通过。来源/Worker/读取/回放/Outbox/错误契约118项通过（89.99秒）。新增单聊/群聊30秒总发送时限及投递/错误契约最终119项通过（41.56秒），270源码Mypy及Ruff/1023格式通过。普通环境未部署；远端租约、厂商POST前新鲜度、旧IM跨进程明文边界及真实文档问答继续验收。见[ADR0120](../adr/0120-source-publication-serialization.md)。


2026-09-12 ADR0121：单聊/群聊取得令牌后、消息POST前重查租约、资料、组织及群受众；群答案对每个成员核验来源，无权成员存在时仅发固定状态。旧IM明文接口拒绝受管正文，业务回滚后另事务保留Policy关联拒绝审计。最终167项通过（110.97秒）、9项隔离PostgreSQL场景通过；270源码Mypy、Ruff/1025格式与秘密扫描通过。复用f2a4b6c8d0e1，无新迁移或普通环境部署；真实点仔文档问答和完整M1继续验收。 详见[ADR0121](../adr/0121-final-dingtalk-send-authorization.md)与[验证账本](../release/evidence/productization/20260912-final-dingtalk-send.json)。


2026-09-12 ADR0122/0123：真实zziv持续同步+Kimi问答发现并修复例行扫描清除有效授权、状态审计等待模型Run锁两处问题。新增read_generation及a3b5c7d9e1f2迁移，6项同步PostgreSQL和12项并发/状态通过；最终79项回归（105.44秒）、270源码Mypy/Ruff1029通过。真实7次K3调用：日常翻译通过，两次知识答案及逐条原文复核通过，第二次持续状态可读且约44秒完成；未知数字未编造但弃答笼统，未计质量验收。暂停后企业历史拒绝、日常历史保留；隔离服务已停，普通环境未部署。此前因状态修复停止的全量不计通过；最终ADR0122/0123完整Python2532passed/252skipped/7deselected（678.51秒），该源码快照早于ADR0124；完整M1、点仔文档投递与格式覆盖继续推进。 详见[真实问答账本](../release/evidence/productization/20260912-managed-source-qa.json)、[ADR0122](../adr/0122-preserve-source-lease-during-refresh.md)和[ADR0123](../adr/0123-nonblocking-managed-run-status.md)。


2026-09-12 ADR0124：企业问答区分资料不足、生成格式错误和模型不可用；缺项主题仅允许摘取当前用户问题的原文，最终提示由本地生成，不发布模型弃答正文。真实zziv资料与一次K3调用约24秒返回明确的容量上限缺项说明；仍为WITHHOLD/未核验、无事实引用，不冒充独立复核。暂停后该历史答案拒绝且计划隐藏，日常历史保留。最终151项通过（99.47秒）、6项隔离PostgreSQL通过，271源码Mypy、Ruff/1031格式、秘密扫描通过；无新迁移，隔离服务已停止。前版完整2532项通过与本次增量分开记账；普通环境、点仔文档投递、完整格式和M1继续推进。 详见[ADR0124](../adr/0124-controlled-insufficient-evidence-replies.md)与[验证账本](../release/evidence/productization/20260912-controlled-abstention.json)。


2026-09-12 ADR0125：补齐普通启动的受管目录/正文能力注册，测试改用正式描述符，并验证管理员接口完整注册流程。整合Python2548passed/252skipped/7deselected（633.88秒），注册专项56项、最终HTTP2项及隔离PostgreSQL2项通过；271源码Mypy、Ruff/1033格式通过。已备份、真实备份恢复试升级并将日常API/Web升级到a3b5c7d9e1f2，修复本机5432端口冲突，项目宿主端口改为56324；246后端源码与运行镜像一致。zziv/Joony来源经正式API注册并由受监督宿主进程持续同步，3篇可用、7篇adoc不完整、1篇AI表格不支持、1篇空正文失败。点仔24/25/26均真实入站并单次成功投递，聊天正文与产物一致：四类内容逐项原文复核、容量问题明确弃答、同私聊正常英文翻译；共4次K3调用。三条回执UNREAD，不声称已读；两条上游入站约60秒延迟，完整格式、时延与M1/自主项目仍继续推进。 详见[ADR0125](../adr/0125-register-managed-source-capabilities.md)与[日常环境验收账本](../release/evidence/productization/20260912-normal-managed-source-qa.json)。


2026-09-12 ADR0126：保留编号、合并表格、分栏和重复代码，检索不拆开结构片段；最终Python2579passed/252skipped/7deselected（636.10秒）、85项专项及PostgreSQL版本/撤权/恢复通过。日常API247源码与固定宿主版本一致，授权可用文档3→6，原文档标识保留。5次真实K3调用：表格含义经独立复核通过，但答案出现内部证据编号；点仔额度问题明确弃答并成功投递；合同分类三个Claim均有据却被整体复核拦截，不计问答通过。另有一条已发送消息未见入站，一条约60秒发生在本地回调前；完整质量、格式、M1和自主项目仍在推进。 详见[结构与问答验证](../phases/productization-dingtalk-layout-validation.md)与[ADR0126](../adr/0126-preserve-dingtalk-document-layout.md)。
