# 企业知识金额与比例核验记录

日期：2026-09-11。状态：VERIFIED_REPOSITORY_LOCAL。正式阶段仍为 Phase 98。

依据 [ADR 0109](../adr/0109-knowledge-quantity-grounding.md)。

先加入复现测试，原代码得到 **8 failed、7 passed**：其中完整 API/Harness 用例把文档中的 500 元
回答为 1000000 元，仍标 verified=true。错误文本具有正确主题和当前 Evidence ID，因此缺陷不在召回。

修复后，金额/比例、原答案发布和原知识 Agent 组合 **56 passed，9.21 秒**。
随后增加逐 Claim 隔离及 GENERAL 兼容测试。最终同一源代码快照完整 Python 回归
**2337 passed、223 skipped、7 deselected，614.78 秒**，包括日常问答、Stream 修复与本增量。
跳过的显式基础设施测试及排除的真实租户测试不记为通过。

独立 PostgreSQL 17 验证复用本任务自建容器中新建的 `obsion_quantity_test` 数据库，
从空库升级到 `d0e2f5a7b3c4`。复用同一个 API/Harness 发布屏障场景，以明确模拟的错误模型输出
验证 PostgreSQL 上答案、报告与 answer.delta 均拒绝 1000000 元；场景通过，Alembic check 无差异。
该数据库与实际开发问答库分离，未修改业务知识文档或历史答案。

Ruff 与 973 个文件格式检查通过；Mypy 228 个控制面源文件通过。
契约（343 错误码、98 事件、101 版本）、项目状态校验通过，秘密扫描 0 项。
完整回归通过后部署 `obsion-api:m1g-quantity-grounding-20260911`；运行中 API 的 228 个 Python 源文件
与受测仓库逐一散列一致，API/Web 健康检查通过。上一版本
`obsion-api:m1e-everyday-20260911` 可用于回退，无迁移或历史数据改写。
重新读取 GitHub main 后，HEAD 与 origin/main 无提交差异；修改仍在 `codex/answer-verification`
本地工作树，未提交或推送。

部署后经指定管理员密码会话、真实 PostgreSQL 与 ModelGateway 复查：

| 用例 | Run | 实际结果 |
| --- | --- | --- |
| 根据 README 解释 Obsion，区分设计目标与验收 | 01a09019-c337-78f0-b1a4-50d8b62d01eb | 正常完成，引用已授权资料，正文明确设计描述不代表生产验收；2930 输入 / 589 输出 token |
| 已授权资料中不存在的火星量子通信协议 | 01a09019-ff85-7cb1-8e4a-f37970641a70 | 正常完成，返回不知道，verified=false，零引用；2936 输入 / 48 输出 token |

验证会话已撤销（204）。这两个实际模型用例用于检查原知识路径兼容性，不替代模拟错误数值的
确定性拦截测试，也不构成开放域准确率或钉钉最新消息闭环证明。

实现只读取当前 Run 已授权 Evidence 的 DOCUMENT 正文，按每个 Claim 的关联检查，再核对候选答案。
拒绝错误币种、量级、正负号和无来源数值，允许已支持的千分位、小数、全角、万/亿与币种同义表达。
冲突沿用现有 WITHHOLD 屏障，最终答案、报告、answer.delta 均不能泄出被拒绝正文。
未调用真实租户或生产服务来复现错误；不把测试中的模拟模型输出记为真实模型质量评估。

无数据库变更，无迁移；事件 schema、接口形状和历史 Run 保持原样。
该检查无法证明同一来源中相同数值与所述指标匹配，也不覆盖所有中文数词、否定关系、条件或派生计算。
完整语义核验、M1 UAT、自主项目执行与生产门禁仍未完成。
