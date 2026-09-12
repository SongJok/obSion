# 本地模型限制验证

日期：2026-09-11。状态：VERIFIED_REPOSITORY_LOCAL。

依据 [ADR 0111](../adr/0111-model-id-allowlist.md)。用户指定仅使用 kimi-k3-kimi 和 kimi-k2.7-kimi。
环境 OBSION_AI_MODEL 已改为前者，但数据库旧端点仍指向其他模型；尚未发起新调用时发现此差异。
短暂停止本地开发 API 后，经共同模型网关增加准确 ID 限制，管理员 API 创建两个授权模型端点，
绑定五个既有逻辑 profile。旧调用/端点记录保留；旧模型被部署限制排除。fallback 仍维持原 false。

新增 8 项调用级测试证明聊天、嵌入、准确大小写、显式 []、优先级与备用端点不能绕过限制；
3 项环境测试区分 null、[] 和明确列表。组合配置/模型限制/密码与环境一致性 **45 passed，12.96秒**。
首轮新测试的嵌入断言错误假定其异常包含聊天专用 no_model_route 标记，3 项失败；已按既有异常
合同校正，保留所有零网络请求断言。没有修改嵌入异常语义，也没有放宽模型限制。
Ruff 980 文件格式与 Mypy 254 源文件通过。整合模型限制的完整 Python 回归 **2378 passed、223 skipped、7 deselected，639.72 秒**。
运行过程中追加的四行作者提示规则已另经知识/发布/契约组合 74 passed 和真实 README 重测；
不将这次全量描述为包含后加提示规则的冻结快照。

运行中 API 已核实 allowlist 恰为上述两个 ID；当前无临时源码挂载，最终 `obsion-api:m1h-kimi-provenance-20260911` 镜像 229 个控制面 Python
源文件与仓库完全一致，Web 使用 `obsion-web:m1h-kimi-review-20260911`。构建等待上游期间使用的两文件只读临时挂载已撤除。
实际翻译 Run `01a0903d-caaa-7b02-90ef-05f02140d9f8` 返回 `Thank you for your help.`，
ModelCall 为 kimi-k3-kimi / SUCCESS，2493 输入、100 输出 token，零企业引用、verified=false。
本地密码会话已撤销（204）。用户限制更新之后没有观察到其他模型的新调用。

kimi-k3-kimi 的隔离原文复核 8/8 有效执行并符合预期。kimi-k2.7-kimi 的 8 个调用均失败，
单独正例诊断返回 HTTP 503；不能将安全弃答统计为语义通过，也不能宣称 K2.7 可用。
当前实际正常问答使用 K3，不自动切换到其他模型。证据见
[模型复核账本](../release/evidence/productization/20260911-knowledge-review-kimi.json)。

无数据库迁移或生产部署。回退只能使用同样执行模型限制的版本，禁止无保护恢复旧网关。
该切片不代表完整企业问答、自主任务或生产验收已完成。
