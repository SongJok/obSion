# 答案独立复核诊断

`grounding_review_invalid` 表示独立复核结果未通过本地校验，不能发布候选答案。
ADR0136 在新执行的复核中添加可选 `grounding.diagnostic`；旧记录没有该字段时不能推测原因。
从原 Run 的 VERIFY 步骤、VerificationAssessment 或回答产物读取同一份诊断，
只保留响应摘要与固定原因，不保存或公开原始模型响应。

| 诊断 | 需要核查的环节 |
| --- | --- |
| `response_not_json` | 端点是否遵循 JSON 输出要求，响应能否在解析限制内完整读取 |
| `response_truncated` | 本次复核是否在剩余输出预算内完成；不能接受截断的局部判断 |
| `root_fields_invalid`、`answer_flags_invalid` | 顶层字段和布尔判断是否严格符合复核合同 |
| `claim_coverage_invalid`、`claim_fields_invalid`、`claim_index_invalid`、`verdict_invalid` | 是否逐条、唯一、完整评价了候选声明，而非遗漏、重复或自造结论标签 |
| `quote_list_invalid`、`quote_fields_invalid`、`quote_text_invalid` | 引用列表和文本的结构、数量及长度限制 |
| `quote_source_invalid`、`quote_coverage_invalid` | 是否使用声明关联的顶层 Evidence ID，且支持结论覆盖所有关联证据 |
| `quote_not_exact` | 引用是否逐字存在于提供的正文块；不能通过模糊匹配或改写原文制造通过 |
| `quote_not_substantive` | 已匹配引用是否太短，不能支撑声明；完整的短正文保留原例外 |

有效的 `CONTRADICTED`、`INSUFFICIENT`、整体答案不支持或问题未完成属于语义否定，
继续按原 `grounding_not_supported` 与受控调查流程处理，不伪装成结构错误以反复请求通过。
只有第一次复核整体和逐条均声称支持、且失败为 `quote_not_exact` 时，才自动尝试一次
引用修正。原候选和来源保持固定，服务端要求逐字复制单个正文块；不能拼接或改写引用。
第二次无效就停止，结构错误、截断、过短引用与任何语义否定都不触发该重试。

`grounding.attempts` 记录各次策略类型、请求指纹、响应摘要（如有）和固定结果，
不是原始模型响应。费用和令牌逐次计入总预算；预算耗尽、已知取消/截止状态停止修正，
`grounding_repair_stopped` 表示修正被运行边界拦截，迟到响应不支持发布。
修正后仍需原权限、来源再检查和完整发布验证；模型服务故障及来源不可用保留各自状态。

CI 的完整测试若失败，新增步骤会将最多20个失败测试名称与异常类型写入检查注释，
便于无日志下载权限的观察方定位测试；断言正文仍仅在原 `coverage` 附件的
`test-results.xml` 中。原失败退出码、覆盖率门槛与后续构建依赖不变。
