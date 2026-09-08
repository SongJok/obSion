# ADR 0099：项目来源绑定清单与安全对账

- 日期：2026-09-07
- 状态：已实现本地切片；不代表云效源码、真实租户或生产验收
- 关联：[ADR 0092](0092-policy-audited-project-source-management.md)、[ADR 0093](0093-governed-project-source-rest.md)、[ADR 0095](0095-codeup-operator-discovery.md)

## 问题

云效目录发现已经能返回仓库元数据，来源管理也已经能冻结 Connector 配置、登记项目绑定和撤销记录，
但管理员只能通过写操作回执或数据库对账确认当前绑定。缺少受治理的清单会让“目录发现 → 显式映射 → 读取/撤销”
流程在产品层断开，也容易诱发重复登记或把已撤销的来源误认为可用。

## 决策

在现有 `/api/v1/admin/project-sources` 下增加两个只读清单接口：

| 路径 | 用途 |
| --- | --- |
| `GET /versions` | 列出冻结 Connector 版本及版本撤销状态 |
| `GET /` | 列出 Workspace/本地仓库到厂商仓库标识的固定绑定及双撤销状态 |

两个接口只接受当前租户的查询过滤：`include_revoked`、`workspace_id`、`repository_id`。输出使用专用投影模型，
仅包含本地 UUID、Connector 名称/类型、环境、创建者/时间、云效仓库标识和撤销元数据；不返回 endpoint、完整
configuration、credential_ref、allowed egress、token、源码或实际厂商 scope。默认隐藏撤销记录，显式
`include_revoked=true` 才用于运维对账。

读取需要主体具备 `project_source.read`。接口复用共享认证、租户过滤和固定项目来源事实表，不接受请求中的组织、
actor、权限或连接配置。写入仍由 ADR0092/0093 的 ProjectSourceService 处理，继续经过 Policy、ACL、锁定配置、
审计和提交后响应；清单本身不创建来源、不自动绑定 Codeup 目录条目，也不扩大 Agent grants。

## 安全与兼容性

- 查询跨租户 UUID、不存在的过滤值和撤销状态只返回空清单，不确认其他租户资源。
- 版本撤销会使关联绑定显示 `active=false`；来源撤销只影响对应绑定。清单状态不是厂商身份、scope 或源码可读性证明。
- 没有数据库 schema 变化和迁移；使用现有不可变版本与双撤销账本。
- OpenAPI 新增两个 GET 和两个投影模型；既有四个 POST 的请求/响应和错误契约保持不变。
- 真实云效成员关系、凭据管理、PostgreSQL 多 Worker、来源获取和沙箱传输仍由 M2/M1/Phase 99 门禁负责。
