# 云效 Codeup 只读连接接入手册

日期：2026-09-06。适用协议：`codeup.read.v1`，云效 Central 接入点。

本手册对应已实现的原生 HTTP 适配器、Capability Gateway、项目查询 REST 和工作台界面。
本地自动测试使用合成仓库、合成令牌与 MockTransport。2026-09-07 已通过隔离验证实例完成真实云效目录查询，
源码四类读取与正式部署仍待验收，详见[验证记录](../phases/productization-codeup-validation.md)。

## 用户操作

1. 打开工作台“企业代码图”，在“云效项目查询”选择自己有权访问的仓库。
2. 首次选择“仓库信息”查询；成功只说明此仓库在本次请求下可读。
3. “提交记录”接受分支或标签，每页 20 条，用户点击下一页才再次请求。
4. 查询“指定提交”或“文件内容”前，从提交记录复制完整的 40 位提交编号。文件路径是仓库内相对路径。
5. 查询失败时查看处理提示和查询编号。管理员可在审计记录中用查询编号定位 Policy 和连接错误。

工作台不收集令牌，不主动发起远程读取。切换仓库、修改查询条件或新查询时清空旧结果并取消等待；迟到响应不能覆盖当前项目。
取消浏览器等待不等于服务端已停止读取；服务端仍由能力超时与审计管理。文件内容以纯文本展示，不执行 HTML、脚本或命令。

## 管理员需要补齐的信息

| 配置 | 来源与用途 |
| --- | --- |
| 云效组织 ID | 云效组织设置 / OpenAPI 参数；不是 Obsion 组织 UUID，也不是应用 ID |
| 云效仓库数字 ID | 云效仓库信息；不能用代码托管 URL 或路径代替 |
| 本地仓库 UUID 与规范名称 | 在 Obsion Code Repository 登记，如 `example/project`；名称必须与映射键一致 |
| 服务端只读访问令牌 | 云效个人访问令牌及该身份的仓库访问权限；由部署密钥注入到控制面进程环境 |
| 当前环境与能力绑定 | development/staging/production 必须与部署环境一致；测试环境映射为 development |
| 人员、角色与仓库 ACL | 当前主体需通过 `code.read`、Policy 和仓库 ACL；显式 DENY 优先 |

`OBSION_CODEUP_APP_ID` / `OBSION_CODEUP_ORG_ID` 即使非空也不会自动建连接，应用 ID 不会被当成访问令牌。
不要在聊天、源代码、浏览器表单、连接器 `configuration` 中填写令牌。现有 `CredentialBroker` 支持 `env://NAME`
或组织内 `secret://name` → 外部引用；当前实现最终读取进程环境，不宣称已安装云 KMS/Vault 提供者。仅向 `.env` 写入任意字段不保证它进入进程环境。
部署管理员应在现有 Secret 管理中注入自定环境变量，例如 `OBSION_CODEUP_READ_TOKEN`，连接器只保存 `env://OBSION_CODEUP_READ_TOKEN`。

## 配置步骤与 API

### 不知道仓库数字 ID 时

管理员可创建独立目录连接器：`connector_type=codeup-catalog`，固定 Central endpoint，
`configuration={"protocol":"codeup.catalog.v1","organization_id":"实际组织 ID"}`，
`declared_grants=["connectors.read"]`，凭据引用与固定 egress 的配置方式同下文。
这里的 `connectors.read` 是 Obsion 管理权限，不是云效令牌的 scope。

通过能力列表找到 `codeup.repositories.discover`，使用下列资源选择器绑定该连接器：

```json
{"connector_id":"REPLACE_WITH_CONNECTOR_UUID","source":"codeup-catalog"}
```

调用 `POST /api/v1/admin/codeup/connectors/{connector_id}/repositories`：

```json
{"operation":"codeup.repositories.discover","search":"project","page":1,"limit":20}
```

`search` 可省略；查询最多每页 20 条、100 页，返回数字 ID、名称、命名空间路径、可见范围及归档状态。
按云效路径升序、排除归档仓库；满页只表示可能还有结果，不能当作完整目录。
该接口通过 operator Gateway、Policy、当前管理权限与审计，Agent Run 不可调用。
目录查询成功不代表文件可读，也不会自动登记仓库、配置 ACL 或扩大 Agent grants。
选择实际目标后，继续下方仓库映射步骤；不要把无关仓库作为项目映射。

### 已知目标仓库后

所有下述管理操作使用既有 Obsion 管理员认证与权限，不是提供给机器人调用的安装工具。

1. 用现有 `POST /api/v1/code/repositories` 登记本地仓库，配置分类与 ACL。记录真实返回的 UUID。
2. 在部署环境注入只读令牌。根据云效当前权限界面选择仓库、提交和文件的读取权限，核对令牌所属身份的仓库成员关系。保留实际 scope 清单的脱敏核验记录。
3. 用 `POST /api/v1/admin/connectors` 创建连接器。管理 API 返回连接器 UUID；`ACTIVE` 只表示本地启用，初始健康仍为 unknown，不能当作已联通。
4. 用 `GET /api/v1/capabilities` 获得四个 Codeup 能力的定义 ID，逐个通过 `POST /api/v1/admin/capabilities/{definition_id}/bindings` 绑定。
5. 回到工作台执行四类读取验收。若要在 Agent Run 中使用，还需在对应 Agent 的能力契约中明确声明这些能力并通过既有版本发布流程；本次不会自动扩大任何 Agent 的 grants。

### 从目录条目创建验证映射

如果本地连接器尚未登记目标云效仓库，可在目录查询后使用验证映射入口，避免手工复制过期或错误的数字 ID：

```json
{
  "catalog_connector_id": "REPLACE_WITH_CATALOG_CONNECTOR_UUID",
  "repository_id": "REPLACE_WITH_LOCAL_REPOSITORY_UUID",
  "provider_repository_id": "123",
  "provider_path": "example/project"
}
```

请求：

`POST /api/v1/admin/codeup/connectors/{codeup_connector_id}/mappings`

服务端会先通过目录 Connector 重新读取当前目录，只有数字 ID、完整路径和 `archived=false` 同时匹配时才继续。随后由
`project_source.codeup.map` Policy 和 `connectors.write` 权限保护的事务更新 Codeup Connector，并创建不可变配置版本。
同一映射重放返回 `UNCHANGED`；同一个云效数字 ID 试图绑定另一个本地仓库返回冲突。映射结果不会自动创建 Workspace 来源、修改仓库 ACL、授予 Agent 或读取源码，管理员仍需在项目来源管理入口显式绑定 Workspace。

响应只包含本地/供应商 ID、冻结版本 ID、结果和 Policy decision ID，不包含 endpoint、完整配置、凭据引用、令牌、scope 或源码。
生产环境仍不开放该管理切片；真实云效成员关系、令牌 scopes 和目标仓库读取必须单独记录验收证据。

### 核对已登记来源

管理员可通过 `GET /api/v1/admin/project-sources/versions` 查看冻结连接版本，通过
`GET /api/v1/admin/project-sources` 查看本地 Workspace/仓库与云效仓库标识的固定绑定。两个清单默认只显示当前
未撤销记录；排查回退或撤权时使用 `include_revoked=true`。清单不会返回端点、完整连接配置、凭据引用、源码或
厂商 scope，也不会自动登记目录结果。`active=true` 只表示 Obsion 本地撤销账本仍有效，远程读取时仍会重新检查
Connector、Policy、仓库 ACL、来源版本和云效返回结果。

以下请求为结构示例，所有组织/仓库/UUID 占位值必须替换：

```json
{
  "name": "codeup-central-reader",
  "connector_type": "codeup",
  "status": "ACTIVE",
  "environment": "development",
  "endpoint": "https://openapi-rdc.aliyuncs.com",
  "credential_ref": "env://OBSION_CODEUP_READ_TOKEN",
  "configuration": {
    "protocol": "codeup.read.v1",
    "organization_id": "REPLACE_WITH_YUNXIAO_ORGANIZATION_ID",
    "repositories": {
      "example/project": {
        "id": "123",
        "repository_id": "00000000-0000-0000-0000-000000000000"
      }
    },
    "allowed_repositories": ["example/project"],
    "rate_limit_per_minute": 60
  },
  "declared_grants": ["code.read"],
  "allowed_egress": ["openapi-rdc.aliyuncs.com:443"]
}
```

每个能力绑定请求：

```json
{
  "connector_id": "REPLACE_WITH_CONNECTOR_UUID",
  "environment": "development",
  "resource_selector": {"repository": "example/project", "source": "codeup"}
}
```

`POST /api/v1/code/repositories/{local_repository_uuid}/remote-read` 的四种请求体：

```json
{"operation":"codeup.repository.get"}
```

```json
{"operation":"codeup.commits.list","ref":"main","page":1,"limit":20}
```

```json
{"operation":"codeup.commit.get","commit_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
```

```json
{"operation":"codeup.file.read","commit_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","path":"README.md"}
```

示例 SHA 不代表真实提交。API 不接受租户、远程仓库 ID、URL、凭据或额外操作字段。
返回 `operation/repository/repository_id/items/count/next_page/complete/policy_decision_id`。
非 Run 查询产生 Policy 与 Audit，不伪造 Run 或 Evidence；有 Run 的 Gateway 调用产生 RESTRICTED CODE Evidence。

## 结果与失败解释

| 状态/错误码 | 处理 |
| --- | --- |
| 目录查询成功但 0 条 | 本次筛选未匹配当前令牌可见的非归档仓库；核对组织、仓库路径和成员关系，按需省略搜索词查询一页，不据此推断组织没有仓库 |
| `codeup_configuration_invalid` | 检查本地绑定、协议、固定接入点、映射和只读 grants；配置格式错误也可能在资源检查阶段表现为拒绝 |
| `credential_unavailable` | 检查密钥引用与控制面进程实际可读环境；不要通过日志打印令牌 |
| `codeup_repository_denied` | 检查 Obsion 组织/用户/角色/仓库 ACL，以及读取期间连接配置是否被撤销或修改 |
| `codeup_upstream_denied` | 厂商 401/403/404 统一映射；检查令牌、成员关系和资源是否存在，不泄漏厂商原始响应 |
| `codeup_rate_limited` / `capability_rate_limited` | 稍后重试并缩小查询范围；客户端不自动重复请求 |
| `codeup_upstream_unavailable` / `capability_timeout` | 检查固定出口网络和厂商可用性；不会跟随重定向 |
| `codeup_response_invalid` | 身份、字段、时间、文件大小或 Git blob hash 校验失败；不交付该内容 |
| `codeup_response_too_large` | 响应超过 2 MiB 预算；选择较小文件或查询范围 |

请求使用 identity 编码，压缩响应在消费正文前拒绝，避免解压扩张绕过内存预算。

文件仅支持不超过 256 KiB 的 UTF-8 文本；拒绝路径穿越、`.git`、`.ssh`、`.env` 和常见密钥文件路径。
`blob_id` 是原始字节的 Git blob 标识，`content_sha256` 是脱敏后展示内容的摘要。敏感内容扫描是规则匹配，不保证识别所有秘密。
这些 hash 校验不能代替原始 commit/tree 的完整验证，也不能当作沙箱 checkout 授权。

提交列表每页最多 50 条、最多 100 页。满页的 `complete=false`；第 100 页满页时 `next_page=null` 但仍不完整。
不要把“没有下一页可请求”写成“全部历史已读取”。文件和指定提交只接受完整 SHA，避免分支移动造成读取版本漂移。

## 真实验收与回退

真实验收至少记录：部署版本、时间、脱敏组织/仓库标识、实际只读 scope 核验、四类读取结果的安全摘要、Policy/Audit 关联 ID、
无权仓库拒绝、令牌失效拒绝、撤权后拒绝及分页边界。文件验收用专门的无敏感测试文件。
不在验收记录中保存令牌、鉴权头、完整厂商响应或未治理源码。

停用该 Connector 或其能力绑定会阻止后续解析；撤销仓库授权会阻止后续资源检查。网关在远端响应后再次读取当前主体/仓库 ACL 和连接配置；
发现变化时丢弃内容。此检查不等于整个 Policy/绑定图在交付时有数据库级线性化保证，已有浏览器展示也不会被服务端远程擦除。
无数据库迁移或历史回填，回退应用版本前先停用新连接与绑定；保留审计。不要删除来源账本或已有证据来模拟回退成功。

本切片不提供云效 Region 接入点、AK/SK 签名、流水线执行、工单创建、push/PR/merge/deploy、仓库归档下载或沙箱项目传输。
这些需求须继续通过各自明确的能力和阶段契约实现，不能改固定只读 GET 路由为任意 OpenAPI 逃生舱。

官方协议依据：[接入点](https://help.aliyun.com/zh/yunxiao/developer-reference/service-access-point-domain/)、
[仓库信息](https://help.aliyun.com/zh/yunxiao/developer-reference/getrepository-query-the-code-base)、
[指定提交](https://help.aliyun.com/zh/yunxiao/developer-reference/getcommit-query-commit-information)、
[提交列表](https://help.aliyun.com/zh/yunxiao/developer-reference/listcommits-query-the-submission-list)、
[文件读取](https://help.aliyun.com/zh/yunxiao/developer-reference/getfileblobs)。核对日期：2026-09-06。
