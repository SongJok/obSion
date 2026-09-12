# 问答可用性与发布门禁

日期：2026-09-11。状态：PASSED_REPOSITORY_LOCAL_WITH_TEST_TENANT_EXAMPLES。正式阶段仍为 Phase 98。

决策：[ADR 0106](../adr/0106-answer-publication-and-chinese-retrieval.md)。

必须验证：

- 核验拒绝的正文不会进入最终答案、派生报告或 `answer.delta`；既有 IM 仍只读取该持久答案。
- 明显无关的中英文回答不能由外置 Claim 或引用标题挽救。
- 模型非法引用/非有限置信度不得被部分接受，错误 JSON 不让 Run 意外崩溃，费用不被漏记。
- 模型弃答、无效回答或不可用不能被知识摘录替换并宣称回答了具体问题；真实模型分别验证已知与未知问题。
- 中文自然问句在 SQLite 与真实 PostgreSQL 可召回当前授权资料；撤权和跨组织反向测试通过。
- Event/Error 合同、单控制面边界、静态检查、完整回归及既有迁移链保持有效。
- 测试租户实消息单独记录入站、Run、回复及厂商状态；模拟响应和历史结果不得冒充本轮 UAT。
- 多组织测试核对 corpId、机器人 app key、安装记录、发送身份与 conversation；CLI 当前组织不能代替客户端当前组织。

本门禁不证明语义正确率或完整自主任务成功率。未取得的真实租户、容量、预发、签署和生产证据
继续保持待验收，不因实现或本地回归通过而晋级。

2026-09-12 ADR0127：统一正文复核与平台引用职责，保留经本地校验的整体支持/问题完成判断；内部编号触发重验权限后至多重写一次，仍须事实复核。五条内容读取路径不再等待模型Run锁，最终PostgreSQL正常/撤权10项通过；整合Python2596passed/262skipped/7deselected（704.80秒）、专项86passed、273源码Mypy与Ruff/1044格式通过。日常API248源码与固定宿主快照匹配，6篇可用且标识保留。隔离及点仔30/31共8次K3调用全部成功，四条答案完整复核通过；两条点仔聊天正文与产物一致、单次SUCCESS/UNREAD，分别103秒和80秒，其中合同入站前60.700秒。真实样例未触发重写，不宣称真实纠错验收。按用户新要求质量优先于速度，继续发展受控多轮分析、工具/文档调用和复核；完整M1和自主项目未完成。 详见[复核与纠错验证](../phases/productization-grounding-contract-validation.md)和[ADR0127](../adr/0127-align-grounding-and-citation-presentation.md)。
