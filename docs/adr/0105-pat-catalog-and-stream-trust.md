# ADR 0105：PAT 跨组织目录与正式 Stream 证书初始化

日期：2026-09-11。状态：接受。范围：Phase 98 / M1 当前连接验收，不推进生产晋级。

## 背景

现有 Codeup 目录固定一个组织；已有 `yunxiao.repositories.list` 可发现 PAT 可见组织，但没有管理 REST 入口。
正式 `obsion-im stream` 与兼容 Stream 使用不同启动函数，正式入口遗漏已实现的可信 CA 初始化。
本机 Python 默认 CA 数量为零，启动进程和取得连接票据均不足以证明 WebSocket 消息链路可用。

## 决定

1. 正式 Stream 复用 `ensure_stream_tls_trust_store`，在 SDK 建连前执行。保留系统信任库和显式配置，
   仅在缺少默认信任根时使用 certifi；不关闭 TLS 校验。保留 SDK 私有日志禁用，增加仅含本地固定阶段码的接收诊断。
2. 新增 `POST /api/v1/admin/yunxiao/connectors/{connector_id}/repositories`，复用已有能力、原生 GET、
   CredentialBroker、限流、超时、Policy 和审计。不创建第二后端，不接入任意远程 MCP 工具。
3. 管理模式要求当前主体同时具备 `admin.read`、`connectors.read`、`code.read`，资源精确绑定 Connector。
   调用前后重查当前权限、组织启用状态和完整连接配置，撤销期间取得的结果不交付。
   保留已有 Run 模式兼容性；不向 Agent 自动开放跨组织目录管理权限。
4. 省略 `organization_ids` 表示在已配置 Central 端点发现该 PAT 的组织；显式配置仍限定其范围。
   返回组织 ID、仓库 ID、名称、路径、归档标记及分页计数，省略描述、账号字段和配置。
   完整性仅指本次声明范围的分页，不代表远端目录的事务快照。调用方跨页核对总数与复合 ID，达到预算不视为取全。
5. 全部仓库的本地接入使用现有管理 API，显式限定本地人员 ACL，按仓库建立独立的连接与精确资源绑定。
   目录本身不自动创建仓库、授予组织成员访问或读取代码。每次源码读取仍经过 Codeup Gateway 与仓库 ACL。
6. 现有唯一约束为能力版本、连接器、环境，一个连接器不能通过重复创建绑定来表达多个资源选择器。
   相同绑定重放返回原 ID；已禁用或不同范围返回 `capability_binding_conflict`（409），不静默改写或扩权。
   使用事务保存点处理唯一键竞争，保留原数据库约束和审计；修复批量配置时暴露的 500。
7. 真实文件接口把 `size` 返回为十进制字符串，官方文档声明为 integer。兼容有界、规范的非负十进制
   字符串与整数；拒绝空白、符号、小数、非 ASCII 数字和超长值。仍验证实际字节数、大小预算、路径、
   固定 commit 与 Git blob hash，不使用最后修改文件的提交冒充请求提交。

## 迁移与回退

无新增表、字段或事件，无需 schema 迁移；已有前向迁移链继续适用。新增 REST/OpenAPI 为兼容性增量。
部署时更新 Stream 进程和控制面版本；密钥沿用外部环境引用。新连接与权限规则通过管理 API 显式配置并审计。
回退先停止新目录调用、停用对应绑定；不删除 Inbox、Outbox、来源账本或审计记录。未知发送结果不重发。

## 验证

覆盖真实 Policy/Gateway 的跨组织分页、敏感字段剔除、无自动授权、非法参数、权限缺失、读取期间撤销和缺项提示；
Stream 覆盖初始化顺序、CA 失败时不建连及无敏感信息诊断。真实租户证据与限制记录在
[M1c 验证记录](../phases/productization-m1c-validation.md)。模拟通过不替代真实消息、源码、scope 或生产验收。

## 官方协议参考

- [云效 MCP 工具说明](https://help.aliyun.com/zh/yunxiao/developer-reference/cloud-effect-mcp-tool-instructions)：用于核对能力边界；当前阶段沿用 Python Capability Gateway 下的固定只读接口。
- [GetFileBlobs](https://help.aliyun.com/zh/yunxiao/developer-reference/getfileblobs)：文件路径、版本与内容返回契约；文档类型和真实响应差异按上述严格兼容处理。
