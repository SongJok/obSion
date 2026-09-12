# IM 历史投递迁移架构门禁

日期：2026-09-12。状态：本地迁移修复验证通过，远端整合门禁开放。

[ADR0129](../adr/0129-legacy-im-reconciliation-migration.md)只修复历史数据要求及回退约束。
单一Python控制面、PostgreSQL交易账本、Gateway/Policy与审计边界不变；不发送消息、
不修改厂商回执、不增加重发资格，不移除触发器或降低对账标准。

新增数据迁移b4c6d8e0f2a4，在现有保护下仅补缺失的UNKNOWN对账要求时间；回退保留
该事实，重新升级幂等。旧c9d1回退包含前置版本已有的UNKNOWN，使a82c的明确安全门禁
正常执行。真实PostgreSQL完整往返和数据不变断言通过，临时库已清理。

详见[验证报告](../phases/productization-im-migration-validation.md)。普通运行环境已升级；远端CI仍待完成，不将本项标为完整企业能力验收。
