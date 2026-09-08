# ADR 0088：有界项目副本与补丁交付基础契约

- 日期：2026-09-06
- 状态：内部基础实现；未开放 Agent/HTTP 能力，M2 验收未完成
- 编号：主树现有编号到 0084；0085/0086 为尚未集成的自主循环/反馈学习 ADR，0087 预留给 Outbox，本 ADR 不占用这些编号。

## 背景

`second_goal.txt` 要求在授权的固定仓库版本副本中修改、验证并输出补丁，禁止挂载开发者工作目录或凭据，也不自动 push、创建 PR、merge、deploy。

现有 Git 工程能力只查询变更，Code Graph 的 `commit_id` 来自调用者且不保存完整文件正文，不能当作可信项目版本获取服务。现有 `ArtifactService.get_metadata/content` 仅检查组织与 Workspace 权限，未重新检查来源仓库 ACL；因此不能直接把更窄授权的项目源码发布为普通 Workspace Artifact。

ADR 0084 的 Kubernetes 后端目前只有空工作盘。本次先实现上游可信入口可复用的文件字节契约、真实文件操作和补丁生成；不以内部数据类绕过 Gateway、Policy 或来源授权。

## 决策

### 1. 来源固定引用不等于授权

`ProjectRevision` 包含 organization、workspace、repository、connector version UUID，以及完整的 40/64 位小写 commit/tree hash。同一版本中的两种 hash 长度必须一致，不接受 branch、tag、latest、短 SHA、零 UUID 或自由 URL。

这些只是结构严格的**来源引用**，不是厂商证明、权限票据或已通过授权的声明。当前实现不验证 Git 对象、不解析 ref、不调用网络、不读取宿主仓库，不把调用者填的 SHA 冒充真实来源证据。上游必须通过现有 Gateway/Policy，核验当前主体、应用 scopes、来源 ACL、Connector grant、Agent 能力与任务/环境预算，并以真实提供商返回值固定版本。

每次文件操作、命令执行、产物发布及下载仍须重查授权。来源撤权、Artifact classification/ACL 传播及不可变 pin/账本尚未接入，当前不提供新的公开能力或下载路由。

### 2. 有界、确定性、不可执行的项目载荷

新增 `sandbox/project.py`：

- 仅 UTF-8 普通文件；单文件至多 256 KiB、文件至多 500 个、文件及目录条目合计至多 2000 个、内容合计至多 8 MiB。
- manifest 至多 512 KiB；归档至多 9 MiB。超限明确拒绝，不截断内容后声称完整副本。
- 路径为相对 POSIX 路径，NFC 规范化，限制长度/深度；拒绝绝对路径、`..`、空段、反斜杠、冒号、引号、控制/双向格式字符、尾部空格/点、Windows 设备名，以及文件/目录、大小写碰撞。
- 拒绝 `.env*`（包括模板）、Git 元目录/凭据、SSH、云配置、Kubernetes、DWS 登录路径和常见私钥/证书容器后缀。不以“这是 tests/fixture”跳过输入检查。
- 复用现有 Secret 正则，提取纯文本 `scan_secret_text`；原文件扫描行为保持。匹配只产生位置/类型，不回显内容。检测不是完整 DLP，不保证识别所有编码、改名、混合文本中的秘密。被拒路径不做静默排除；可信来源获取流程需显式选择可交付文件，并说明省略项。
- v1 归档固定为 ZIP_STORED，无压缩、链接、特殊文件、额外字段或注释。manifest 固定排序、UTF-8 JSON 与 SHA-256；文件模式只有 0644/0755。解码先核对上游固定的归档校验和，再逐项核对文件名、模式、长度、摘要、精确字段和总预算；重复 JSON key、孤立/多余文件均拒绝。
- 在 `ZipFile` 分配中央目录对象前先线性检查真实条目数、目录范围与 EOCD 一致性，拒绝通过伪报数量造成无界元数据分配。最终重建规范归档必须逐字节一致，拒绝附加前缀、尾随载荷和未登记容器变化。归档是本产品的版本化传输格式，不是接受任意 GitHub ZIP 的通用解压器。
- 不调用 `extractall`、Git、shell 或项目解析插件；不执行项目内容。

### 3. 独占目录中的实际文件操作

新增 `sandbox/workspace.py`：

- 可信入口传入绝对 root；逐层目录 fd + `O_DIRECTORY/O_NOFOLLOW` 打开，拒绝 root/祖先符号链接。拒绝 `/` 本身。
- 文件读、写、删均从 root fd 逐段解析，不拼接任意宿主目标。读取前后 `lstat/fstat` 验证普通文件、单硬链接、受限 mode、大小与变更，拒绝 symlink、hardlink、FIFO、特殊对象。`O_NONBLOCK` 防止替换成 FIFO 后阻塞打开。
- 写入使用同目录独占创建的随机临时文件、固定模式、fsync 与 replace；失败仅清理本次创建的临时文件，不递归删除调用者目录。旧目标损坏前失败时原文件保持。
- materialize 仅接受空目录，不覆盖已有项目。输入失败可留下本次部分副本，调用者不能当作完成，须丢弃该隔离实例；不在错误处理里擅自清理可能不属于本次操作的内容。
- capture 逐项扫描、限制条目/深度/总字节，重新执行文件与 Secret 检查，构造完整快照。空目录不属于 Git 文件快照。

该对象仅是**可信入口内部文件工具**，不是本地沙箱后端、授权层或文件系统容量隔离。要求目录由本任务独占且无新增挂载，读写/导出阶段没有并发项目进程。fd 检查不能消除恶意并发 rename/内容变更的全部竞争，也不能证明所有进程已终止；必须由 gVisor、容器、实例生命周期和持久租约保证。多次写入期间的硬磁盘配额仍由运行时执行；捕获预算只约束交付，不冒充 cgroup/磁盘限额。

### 4. 补丁与验证事实分离

新增 `sandbox/delivery.py`：

- 基础与结果快照必须是同一 revision，输出包含基础提交、文件前后摘要、模式、base/result fingerprint、补丁 SHA-256 和长度。
- 生成确定性的 Git unified patch，支持文本增删、空文件、可执行位变化、中文/空格路径、CRLF、末行无换行。路径采用 Git UTF-8 字节 C 风格转义，不使用 Git 不支持的 JSON `\u` 转义。
- 采用整文件替换 hunk，避免不可信大量重复行触发最坏二次复杂度 LCS；补丁有硬字节上限。代价是补丁较大，不做智能 rename/binary diff。
- `CommandObservation` 引用 Step/Evidence、结构化命令、真实退出码或 `unknown`。unknown 必须没有退出码；timeout/cancelled 使用已有可信入口的 124/143 约定。引用是否真实、是否同租户同 Run，仍须上游数据库重新验证；不接受模型文本作为验证事实。
- 最多 40 条观测，Step 不重复。命令中的已知 Secret 形状拒绝，不把凭据写入复现命令。
- `ProjectDelivery.manifest()` **始终返回 `verification=NOT_EVALUATED` 与 `independent_verification_required`**。即使全部退出 0、补丁非空或能 apply，也不宣称测试通过、问题已修复或任务可关闭。测试结果评估、未解决任务清单及独立 Critic 接入属于后续闭环。

## 验证与边界

主树新增专项 **104 passed（0.43 秒）**，其中两项在空临时合成目录真实运行 `git apply --no-index --check` 与 `git apply --no-index`，验证修改结果逐文件字节/模式重建，包含文件/目录互换。命令剥离 Git 全局/系统配置，禁止交互；未 init/commit、未读取真实仓库、未联网或执行项目代码。

测试另覆盖归档篡改、重复/额外条目、压缩/链接/特殊文件、Unicode 路径、Secret、大小/manifest/条目预算、root/祖先 symlink、读写/删除外部链接拒绝、读取竞态及原子写失败。macOS 本地文件行为不是 Linux/gVisor 网络或内核隔离证明。

截至本轮：全源码 mypy **232 个文件通过**；全仓 Ruff 与格式门通过，**862 个文件已符合格式**。早期局部检查曾因格式与 variadic UUID 构造类型失败；修复后通过。另一次静态命令与尚未完成的局部缓冲区重构重叠，命中未定义旧变量，现已修正并复验；这些失败不计通过。完整 Python 回归另行记录。

后续完整回归发现 `ProjectDelivery` 的 `object.__setattr__` 触发静态错误分析器误报；修复 builtin 未绑定调用的参数解析后，静态/精确契约/项目合并 **240 passed，28.29 秒**，动态 Error 写入与被遮蔽的 builtin 仍然拒绝。同期既有 Workflow 事件竞争修复见 [ADR 0089](0089-atomic-event-sequence-allocation.md)；完整复验终态由 [M0 验证](../phases/productization-m0-validation.md) 记录，不以专项替代。

无需迁移：没有新增数据库、API 或第二套对象存储。未写 ArtifactService/Gateway/runtime，未改变通用副作用拒绝门禁。

**仍未交付**：真实授权仓库获取与 Git 对象验证、Kubernetes 项目注入和输出传输、固定沙箱镜像构建、逐次来源撤权/产物下载检查、Gateway 能力注册、持久实例与 quota/fencing/sweeper、真实项目命令验证和集群验收。M2/M3/M4 及生产晋级保持未完成。
