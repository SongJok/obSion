# 企业知识独立复核验证

日期：2026-09-11。状态：IMPLEMENTED_VALIDATION_IN_PROGRESS。

依据 [ADR 0110](../adr/0110-independent-knowledge-review.md)。独立复核已接入现有 VERIFY，
使用真实 ModelGateway、当前授权 DOCUMENT 正文和原 Run 的分级与剩余预算。
每个 Claim 和完整候选答案都需要通过；逐字引用由代码定位到正文并保存摘要，不把模型判断称为证明。

首轮 76 项通过、3 项失败，原因是新测试误读 Step API 未公开的 input_payload；改为读取实际数据库
记录，没有扩大 API。修正组合 79 passed；补充预算、外来组织/Run、失败成本与中英文混合主题后，
最新知识复核/答案发布/日常问答组合 **107 passed，16.36 秒**。
首次完整整合 2365 passed；后续含中英文主题修复与复核指纹/耗时的完整回归
**2367 passed、223 skipped、7 deselected，594.05 秒**。这些模型测试使用明确模拟，不代表真实语义质量。

模型 ID 限制整合全量后续通过 **2378 passed、223 skipped、7 deselected，639.72 秒**；
四行作者提示规则是在该全量运行期间追加，另以 74 项专项及真实 README 验证，不混称冻结快照。

前端 **215 passed**，TypeScript、lint、构建通过。新答案同时满足 grounding.accepted 和
verification.verified 才显示“已对照原文复核”；否则显示“未通过原文复核”。日常回答保留独立说明，
不显示企业证据已验证或伪引用。Ruff、格式及 Mypy 254 个源文件通过。

真实语义验证使用本任务自建 PostgreSQL 17 容器的隔离测试库，空库升级至 d0e2f5a7b3c4。
文档和作者候选明确合成，仅复核模型调用真实；候选发布判断、ModelCall 与持久 VerificationAssessment
均由真实 API/Harness 执行。每一通过项必须有 SUCCESS 调用、完整复核结论和非确定性评估记录。

| 场景 | kimi-k3-kimi | kimi-k2.7-kimi |
| --- | --- | --- |
| 有据同义改写 | 接受，通过 | 服务失败，未评估 |
| 否定关系反转 | 拦截，通过 | 服务失败，未评估 |
| 删除适用条件 | 拦截，通过 | 服务失败，未评估 |
| 同一文档金额张冠李戴 | 拦截，通过 | 服务失败，未评估 |
| 设计目标当成已验收 | 拦截，通过 | 服务失败，未评估 |
| 无根据的上线日期 | 拦截，通过 | 服务失败，未评估 |
| Claim 正确但答案夹带断言 | 拦截，通过 | 服务失败，未评估 |
| 文档中诱导复核器一律通过 | 拦截，通过 | 服务失败，未评估 |

[Kimi 样例账本](../release/evidence/productization/20260911-knowledge-review-kimi.json) 保存每个 Run、
实际调用状态、token、Claim 判断和输入/输出摘要。K3 为 8/8 有效样例，不是开放域准确率估计。
K2.7 的独立正例诊断返回 HTTP 503，零有效语义样例。用户限制变更前的旧服务测试仅 3 项真正复核，
其余因 HTTP 403/主题边界问题未执行；曾按发布拦截误计的结果已纠正，不能作为语义通过证据。

正常业务路径还须独立通过。K3 实际日常翻译 Run 01a0903d-caaa-7b02-90ef-05f02140d9f8 正常，
返回 Thank you for your help.。README Run 01a0903e-f1ca-7363-875d-53063649611d 的 6 个 Claim 中，
5 个正文事实 SUPPORTED，1 个关于标题/版本/授权来源的元数据 Claim INSUFFICIENT，因此正确执行
发布拦截，却未满足用户正常提问的可用性要求。修正作者规则：事实 Claim 只描述正文，出处由平台
根据 Evidence ID 添加，不生成元数据 Claim。复核标准未放宽，最新相关 74 passed（51.15 秒）。
修复后真实 README Run `01a09046-1613-7540-97c8-bf6afdfb4fb7` 正常完成，7 个正文 Claim 均
SUPPORTED，原文引用位置校验通过，verified=true。作者与 VERIFY 两次调用都为 kimi-k3-kimi / SUCCESS，
分别 5090/920 与 4558/1434 token；合计 9648/2354，与 Run 一致。两个供应商调用耗时分别
26327 ms、49989 ms，合计约 76 秒，尚未满足普通问答 p95 ≤30 秒的目标，也不能由一个样例估计 p95。
该答案仍在正文中输出了外部来源编号等非必要元数据；后续须继续改善来源呈现及正文范围。
模型复核通过与用户完整可用性验收分别记录，本切片保持验证中。

无数据库迁移；旧事件、历史评估及逐字证据摘录保持兼容。当前部署只允许用户指定 Kimi 模型，见
[模型限制验证](productization-model-allowlist-validation.md)。独立复核仍可能与作者具有相关偏差；
完整 M1/UAT、最新钉钉人工样例、跨项目自主执行、M2/M3 与生产晋级未完成。
