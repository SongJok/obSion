# 企业知识金额与比例架构门禁

日期：2026-09-11。状态：VERIFIED_REPOSITORY_LOCAL。

依据 [ADR 0109](../adr/0109-knowledge-quantity-grounding.md)。

- 正确 Evidence ID、主题重合或模型置信度不能覆盖无来源金额和比例。
- 逐 Claim 只使用关联 DOCUMENT 正文，不能使用标题、评分和未关联材料。
- 候选正文独立检查；错误内容不能进入答案、报告或 answer.delta。
- 等值金额表达可用，GENERAL 与非知识路线保持原有合同。
- 无新外部调用、权限、迁移或事件版本，不改写历史记录。
- 不把数值存在检查描述为完整语义核验；验证与限制见对应报告。
