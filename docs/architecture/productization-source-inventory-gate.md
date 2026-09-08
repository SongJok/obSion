# 项目来源清单架构门

状态：本地实现切片通过，生产门保持关闭。

来源清单只读取 PostgreSQL 事实表中的冻结 Connector 版本、项目来源和撤销账本，并通过当前租户 UUID 过滤。
它是运维对账投影，不是厂商身份、scope、源码版本或沙箱授权证明。任何远程读取仍必须重新经过 Capability Gateway、
Policy、Connector、凭据、仓库 ACL 和来源状态检查。

新增 GET 接口不创建来源、不修改 ACL、不自动绑定 Codeup 目录，不把清单内容暴露给 Agent。撤销记录默认不返回，
显式对账也只返回固定撤销原因和时间。endpoint、configuration、credential_ref、egress、token 和源码永不进入投影。

本门不证明真实云效、DingTalk、PostgreSQL 多 Worker、沙箱集群、UAT 或 Phase 99 晋级条件；详见
[验证记录](../phases/productization-source-inventory-validation.md) 和 [ADR 0099](../adr/0099-project-source-inventory.md)。
