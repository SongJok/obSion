# ADR 0095：云效安装阶段的受控仓库目录发现

日期：2026-09-06。2026-09-07 更新：本地实现、专项/完整回归及真实目录查询通过；源码与部署验收未完成。

## 问题与决策

用户提供只读令牌后仍可能不知道仓库数字 ID，本地未登记仓库时，ADR0094 的严格仓库映射不能用于目录发现。
不能为了安装方便放宽既有仓库 ACL，也不能绕过 Capability Gateway 直接调用云效。

新增独立 `codeup.repositories.discover` 元数据查询能力，使用 `codeup.catalog.v1` 配置和 `codeup-catalog` 连接器。
其本地权限与 grants 为 `connectors.read`，L2、无副作用、RESTRICTED，仅允许 operator Gateway；普通 Agent Run 即使声明也不能调用。
该本地管理权限不是厂商 scope，实际访问仍需云效代码库只读授权。管理员通过同一 Policy/Audit/Gateway 读取目录，随后逐一登记并绑定明确仓库。

只支持固定 Central origin 的 GET repository list，配置仅含组织 ID、协议与可选限流；封闭搜索/页码/数量字段，最大每页 20 条、100 页。
只交付仓库数字 ID、名称、命名空间路径、可见范围及归档状态，不返回成员、作者、访问令牌、任意 URL 或源码。
发现结果不自动登记仓库或授予用户权限；内容读取继续走 ADR0094 的映射和当前本地仓库 ACL。

请求和响应前均重查当前主体、组织和连接配置。HTTP 超时、压缩扩张、2 MiB 响应预算、严格状态码和凭据反射脱敏复用受限 Codeup 传输。
目录发现不证明令牌的完整 scope 清单或每个仓库的文件权限。满页按未完整处理，不无限自动分页。

新增管理 REST 只接受连接器 UUID 与查询条件，通过同一 Gateway 执行；不另建后端、不接受请求体中的组织或令牌。
不改变旧四种仓库查询、旧 Agent grants 或生产发布门禁。无数据库 schema 变化，无新迁移。

官方依据：[ListRepositories](https://help.aliyun.com/zh/yunxiao/developer-reference/listrepositories-query-code-base-list)，2026-09-06 核对。
85 项专项、后续验证映射增量和隔离验证实例的两次真实只读目录结果见
[验证记录](../phases/productization-codeup-validation.md)。这些证据不替代目标源码读取或生产验收。
验证映射的独立决策和契约见 [ADR 0100](0100-codeup-verified-mapping.md)。
