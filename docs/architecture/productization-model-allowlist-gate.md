# 模型 ID 限制架构门禁

日期：2026-09-11。状态：VERIFIED_REPOSITORY_LOCAL。

- 共用 ModelGateway 过滤所有候选；没有新的供应商调用或凭据处理路径。
- 限制同时作用于聊天、独立复核、嵌入、私有路由与 fallback，不能扩大原权限。
- null 兼容原部署，显式 [] 关闭模型调用，具体列表使用准确 ID。
- 当前本地仅允许用户指定两种 Kimi 模型；配置变更经管理员 API 审计，历史记录不重写。
- 模型不可用的弃答不得计为回答或语义验收通过，供应商模型身份不能仅凭名称独立证明。
- 无迁移；回退也必须保留模型限制。

依据 [ADR 0111](../adr/0111-model-id-allowlist.md) 和
[验证记录](../phases/productization-model-allowlist-validation.md)。
