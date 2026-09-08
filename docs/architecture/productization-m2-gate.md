# M2 沙箱与项目交付架构门

日期：2026-09-06

状态：**进行中，不通过完整 M2 验收；不改变 Phase 99 晋级阻塞。**

## 已有实现

- [ADR 0084](../adr/0084-kubernetes-sandbox-http-slice.md)：内部 Kubernetes HTTP 后端与可信 Linux 入口，生产关闭，未接入 Harness。
- [ADR 0088](../adr/0088-bounded-project-copy-delivery.md)：内部有界文本项目副本、实际文件操作、确定性补丁及未验证交付清单；不读取宿主仓库，不执行项目代码。
- [ADR 0090](../adr/0090-git-project-content-integrity.md)：内部原始 commit 与完整文件 tree 校验，兼容 Git SHA-1/SHA-256；完整性不证明仓库身份、应用 scopes 或授权，尚无来源获取接线。
- [ADR 0091](../adr/0091-project-source-version-ledger.md)：不可变 Connector 配置版本、同租户 Workspace/仓库来源绑定与独立撤销账本；内部状态重查不代替授权，尚无管理面/来源获取接线。
- [ADR 0092](../adr/0092-policy-audited-project-source-management.md)：内部来源管理服务从锁定 Connector 冻结封闭配置，经独立权限/显式 Policy ALLOW、项目 ACL 与原子审计登记和撤销；没有公开管理入口、厂商 scopes 或来源获取。
- [ADR 0093](../adr/0093-governed-project-source-rest.md)：四个受保护的来源管理 REST POST，封闭输入、元数据响应、生产拒绝，业务拒绝在审计提交后映射错误；运行时 SQLite 在 SAVEPOINT 前按需开启真实事务，修复提前提交。不是来源获取或实际 scopes 验证。
- [ADR 0094](../adr/0094-native-codeup-read-gateway.md)：云效 Central 原生四类只读能力、同一 Gateway/Policy/仓库 ACL、REST 与工作台查询指引。源码内容仅有界读取与 blob 校验，尚未生成来源 pin 或接通沙箱。真实租户与完整 Agent 接线待验收；[本地查询验证](../phases/productization-codeup-validation.md)单列。
- [本地验证记录](../phases/productization-m2-validation.md)：明确区分 MockTransport、本地文件/Git、真实集群及未完成证据。
- [ADR 0095](../adr/0095-codeup-operator-discovery.md)：管理员受控目录发现与首次真实只读目录验证；独立协议及 operator-only 限制，不自动授权源码。目录成功不构成源码、来源 pin、沙箱或生产验收。

## 必须保持的不变量

1. 单一 Python 控制面、Model Gateway、Capability Gateway、Policy Engine 与 PostgreSQL 事实源。沙箱不是第二个任务后台。
2. Agent 只能提议既有已注册能力；内部 adapter、文件类和 hash/UID 不是权限凭证，不直接暴露给模型。
3. 有效授权为主体、应用 scopes、资源 ACL、Connector grants、Agent 能力、当前任务与环境预算的交集。每次执行、外部创建、发布/下载/最终发送重新检查。
4. 沙箱写入例外必须是明确的隔离副本能力，不能删除普通 Agent 副作用拒绝门禁，也不能把写操作标为 `SideEffect.NONE`。
5. 授权仓库版本必须由可信 Gateway 路径确认，不能把 caller-supplied commit/tree 字符串或 Code Graph 元数据当来源证明。
6. 来源仓库权限可能窄于 Workspace：产物列表、下载、证据、上下文均不得绕过撤权。普通 Artifact 访问尚未提供该传播，不接线发布项目源码。
7. 不挂载 `.env`、Git/SSH/云/DWS 凭据、宿主工作目录或 Docker socket；不以日志承载未治理的项目归档，不给沙箱对象存储或 Kubernetes 管理凭据。
8. 模型“完成”、命令 exit 0、Pod/Job 状态、可应用补丁均不能代替独立结果验证。当前交付清单只标记 `NOT_EVALUATED`。
9. UNKNOWN 创建/执行状态先对账，不盲目重试。取消须保留真实 UID 和进程终止/临时数据回收证据，不以 DELETE ACK 当作已清理。
10. 首版只交付补丁、说明、测试结果与未解决问题，不自动 push、PR、merge、deploy。

## 完整验收出口

以下均未被本地契约测试替代：

| 出口 | 当前状态 |
| --- | --- |
| 授权项目与真实不可变 commit/tree 获取，内容/应用 scopes/pin 一致性 | 内部内容核验、配置版本/来源/撤销账本、Policy/Audit 管理服务及受保护 REST 元数据管理入口已实现；获取、厂商身份/实际 scopes、来源授权传播与 Run commit/tree pin 仍待实现和验证 |
| 不带凭据的 Kubernetes 输入注入与输出收集，完整 Artifact lineage | 待实现和验证 |
| Capability Gateway/Harness 逐步执行、撤权、取消、独立 Critic | 未接线 |
| 持久实例账本、租约 fencing、三沙箱配额、公平调度与孤儿回收 | 待实现和验证 |
| 真实固定摘要镜像构建/扫描/签名，可信入口不可替换 | 未验证 |
| 目标 Linux/gVisor/CNI 的网络、CPU/内存/进程/磁盘限制及越界攻击集 | 未验证 |
| 真实项目建立基线、修改、运行测试、修复、补丁交付 | 未验证 |
| Worker 宕机、模型/命令超时、节点失联、取消、恢复不重复效果 | 未完成 |
| 来源撤权/删除/版本变化后上下文与产物访问立即阻断 | 未完成 |
| 10 并发任务/3 沙箱混合运行 24 小时及人工数据/安全签署 | 未执行 |

## 迁移与兼容性

前期内部沙箱、项目副本与 Git 完整性切片无需数据库迁移。后续来源账本新增前向迁移 `a84e25f36a47`：四张不可变事实表、同租户复合 FK、UPDATE/DELETE/TRUNCATE 保护与非空账本降级拒绝；没有回填所谓可信来源，不修改历史迁移。真实 PostgreSQL upgrade/check、往返及不变量验证见 M2 记录。ADR0092 的内部管理服务复用这些表以及现有 PolicyDecision/Audit，不新增 schema，故无新迁移；身份与资源 Policy 查询仅增加旧缓存重载，Code Graph SQL 授权谓词改为可复用名称，既有授权语义不变。后续 Run commit/tree pin、实例、配额/租约与产物关联仍须前向迁移及真实数据库验证；不能借用可变 JSON 覆盖不可变事实。ADR0093 仅追加四个 REST POST、四个请求/响应模型和三个稳定错误码，旧路径/模型不变，无新迁移；SQLite Database 的真实 BEGIN 修正不改变 PostgreSQL。旧 Harness 规则规划、AgentSpec 沙箱声明和既有 REST/SDK 契约保持兼容。
