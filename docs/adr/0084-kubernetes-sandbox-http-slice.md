# ADR 0084：M2 独立 Kubernetes 沙箱 HTTP 基础切片

- 状态：基础实现，生产关闭；真实集群验收待完成
- 日期：2026-09-06
- 编号检查：实施前检查当前隔离树未出现 0084；协调者另核对主工作树到 0083，确认 0084 无冲突。
- 范围：仅新增 `obsion/sandbox`、独立测试及本 ADR。不修改 `main.py`、`runtime.py`、`gateway.py`、`models.py`，不接入生产路由或注册 Agent 能力。

## 背景

`second_goal.txt` 的 M2 要求 Linux Kubernetes + gVisor、独立工作副本、资源限制、默认拒绝网络和可靠取消。ADR 0019 仍正确描述既有 Harness 的沙箱字段只是声明；本次不改变旧路径，不宣称整个 M2 已完成。

当前交付的是可调用真实 HTTP API 的后端及可信镜像命令入口。验证使用 `httpx.MockTransport` 模拟 Kubernetes 服务端，而不是把 adapter 替换成返回成功的空壳。未读取任何集群凭据、未连接真实集群、未构建或拉取镜像、未提交代码。

## 决策与契约

### 1. 默认关闭与凭据边界

- `KubernetesSandboxSettings.enabled=False`，环境默认 `production`。本切片即使显式 `enabled=True`，生产环境也一律拒绝；只有明确的 test/development/staging 能运行 adapter。
- 只接受可信控制面注入的 `httpx.AsyncClient`，要求 HTTPS API origin，不接受 URL 用户信息/查询串/路径前缀；每次请求禁止跟随重定向。
- 不读取 kubeconfig、`.env`、云配置、ServiceAccount token 或 CLI 登录目录，不执行 kubectl。TLS CA 验证、认证及轮换由可信 client 工厂负责；它必须启用正确 TLS 校验、禁用环境代理继承/传输自动重试、限制目标 API 地址，不安装泄密日志钩子。adapter 不通过私有 httpx 属性猜测或宣称已核验这些运维条件。
- 凭据不能进入 `SandboxCommand`、`SandboxRequest`、`SandboxHandle`、Agent 上下文、Job 参数/环境/卷或日志。本包不生成任何凭据字段，错误不输出上游 URL、响应正文或请求头。
- 此后接入必须通过现有 Capability Gateway 和 Policy Engine，对 create/status/logs/cancel 每次鉴权并关联主体、Run/Step、预算与审计。资源名和指纹不是授权凭证，不能把内部 adapter 直接暴露给 Agent。

### 2. 命令和执行资源

- 命令固定为 `executable + argv + cwd + limits`，不拼 shell 字符串、不提供环境变量或 Pod 模板覆盖入口。`executable` 为绝对路径，`argv` 保持逐项字面值；显式执行脚本仍允许，命令过滤不承担隔离职责。
- `cwd` 必须是 `/workspace` 内规范路径，可信入口再次 realpath 检查，拒绝符号链接越界。
- 原始 argv 总计最多 32768 UTF-8 字节，完整序列化命令还须不超过 65536 UTF-8 字节（包含 JSON 转义及元数据）。契约 `to_json/from_json` 与可信入口共用此预算；以 `ensure_ascii=False` 保留中文编码，换行/引号/控制字符导致超限则在任何 HTTP 请求前拒绝，不让 Job 启动后才因传输大小必然失败。
- 固定产品上限：2000m CPU、4096Mi 内存、10240Mi 临时盘、128 个进程。调用者只能缩小限制；单命令默认 120 秒，最大 600 秒。
- 镜像由可信部署配置固定为 `image@sha256:<64位摘要>`，Agent 不可指定镜像。测试中的 `registry.invalid/...@sha256:aaaa...` 是合成测试数据，不是已构建、扫描、签名或可拉取镜像。
- 运维需构建包含本包和 `/usr/local/bin/python3` 的不可变镜像；模块安装在系统 site-packages，不从 `/workspace` 加载。Job 固定调用 `/usr/local/bin/python3 -I -B -m obsion.sandbox.entrypoint`，不能用项目内容替换入口。镜像本身不得预置 secret、启动注入文件或依赖不可信 `.pth`。
- 每个 Job 一个 `emptyDir`，仅挂载 `/workspace`，无 PVC、hostPath、Docker socket、secret 或 projected token 卷。子进程固定 HOME/TMPDIR 为 `/workspace`，根文件系统只读。
- 显式非 root UID/GID 65532、drop ALL capabilities、禁止提权/privileged、RuntimeDefault seccomp、关闭 hostNetwork/hostPID/hostIPC、关闭自动 SA token 与 service links。`runtimeClassName=gvisor` 且创建前读取 RuntimeClass，要求 handler=`runsc`，不存在或不同则拒绝。
- `parallelism=1`、`completions=1`、`backoffLimit=0`、`restartPolicy=Never`。这减少重试，但 Kubernetes Job 本身并不保证恰好执行一次，Pod 失联/替代场景仍须运行时和后续调度治理。

### 3. 可信入口的真实系统调用

- 入口拒绝 root 或非 Linux 环境；设置 `PR_SET_NO_NEW_PRIVS`，实际调用 `resource.setrlimit(RLIMIT_NPROC, (n, n))`，同时限制 soft/hard，且不抬高已有更小 hard limit；禁用 core dump。失败时不启动命令。
- 入口用独立 session/process group 启动命令，不传宿主/控制面环境；超时或 SIGTERM/SIGINT 取消以 SIGKILL 清理整个进程组并 wait 回收。即使命令提前成功退出，也清理同组后台进程；处理 spawn 期间的取消竞态。
- `124` 为入口超时、`143` 为取消、`125` 为入口拒绝；其它返回值来自命令。退出码本身不是“项目任务验证通过”的证明。
- `RLIMIT_NPROC` 依赖 Linux/gVisor 对 real UID/线程的实现与计数，监督进程也占配额，不能宣称等同独立 PID cgroup。无 root/提权避免典型绕过，但实际 fork/thread 压力、跨 sandbox UID 计数及 gVisor 支持仍须 live 验收。
- 同 UID 的恶意命令可能攻击监督进程或 `setsid` 脱离原进程组；进程组清理不是完整防逃逸保证。容器 PID namespace、PID 1 退出清理、kubelet/Job 删除和 gVisor 才是补充边界；需真实恶意命令测试。Job 另设命令超时 + 30 秒的 activeDeadlineSeconds 作为外层保险，不替代入口命令期限。

### 4. 网络拒绝不等于真实网络隔离

- 创建顺序固定为 RuntimeClass 检查 → 每 Job 独立 NetworkPolicy → Job。策略包含 Ingress/Egress，规则列表均为空；本切片不开放 DNS、公网、Kubernetes API、云 metadata 或任何“临时依赖下载”出口。
- 回读会核验显式安全字段与资源指纹；NetworkPolicy spec 除 Kubernetes `omitempty` 对空 ingress/egress 的等价省略外必须一致，不能额外收窄选择器而漏选目标 Pod。显式 false 的 hostNetwork/hostPID/hostIPC 在真实 API 响应中也允许等价省略；仍拒绝 true。资源量按数值等价检查，以兼容 API 将 `2000m/4096Mi/10240Mi` 规范化为 `2/4Gi/10Gi`，不允许借单位变化放宽限制。Job 的 sidecar/init container/hostPath/capability/环境注入等明显漂移拒绝。
- **NetworkPolicy 是附加式的允许规则集合**：其它选中同一 Pod 的 allow 策略可以开放流量，本策略没有绝对优先级。必须使用运维专属沙箱 namespace，审计所有策略、CNI、节点路径、Pod admission 和 sidecar 注入，预置 namespace 默认拒绝并验证启动窗口。
- RuntimeClass 名称、handler 回读、资源 YAML、NetworkPolicy HTTP 201 都不是 gVisor/CNI 真正执行的证据。Job admission 与 Pod admission 是两个阶段，Job 回读检查不能保证之后的 Pod 没被注入；集群必须使用受限 Pod Security/验证准入策略防止放宽。
- **CNI live 验收未完成，真实网络隔离未确认。** 必须在目标集群测公网、DNS、跨 Pod/namespace、控制面、metadata/节点出口及重启/策略传播窗口，确认拒绝，并单独设计未来受控出口。

### 5. HTTP 生命周期、对账与清理

- 实际调用 batch/v1 Job GET/POST/DELETE、core/v1 Pod list/log、networking.k8s.io/v1 NetworkPolicy GET/POST/DELETE、node.k8s.io/v1 RuntimeClass GET。
- 请求有 httpx 分阶段超时及 asyncio 总时限，API JSON 最多 256KiB；拒绝压缩响应避免解压放大，错误脱敏。Pod list 有 limit，出现分页 continuation 时拒绝宣称查询/清理完整。
- 状态先保留唯一、已核验 owner 的 Pod 命令终止证据，再结合 Job 条件。`Failed=True` 保留真实 pod_name/exitCode 和 Job 失败原因，不能因控制器的 BackoffLimitExceeded 丢失命令退出码；Job 失败也不能被单个 code=0 覆盖。`Complete=True` 但 Pod 已回收或缺乏终止证据时返回 `unknown/job_complete_exit_code_unavailable`，不返回 pending，更不伪造退出码 0。
- 日志同时指定 API `limitBytes`、`follow=false`，本地流式硬截断，默认 64KiB、最大 1MiB，达到上限保守标 truncated；响应流及时关闭。原始日志是不可信输出，后续展示必须转义，不能作为新指令；轮转/Pod 消失也意味着无法保证完整日志。
- 请求使用可信调用者生成且持久化的稳定 sandbox_id，命令/镜像/namespace/版本参与 SHA-256 指纹。同名不同指纹拒绝；读取 Pod 需核验标签、Job ownerReference 与 UID。
- 创建先 GET 再最多一次 POST；POST 响应丢失/5xx/409 时只 GET 同名对象对账，不盲目重发。既有 NetworkPolicy 也充当创建尝试标记；若策略已存在而 Job 404，返回 `SandboxUncertain`，不能再次 POST Job。控制面重启也遵守此规则。策略创建后崩溃、策略响应丢失或 Job 被拒绝会保守滞留，需要运维确认，而不是以可用性为由假定安全重试。
- Job 删除用 UID precondition、Foreground、5 秒宽限。DELETE 被接受后仍回读，Job/Pod 尚存则 `CleanupResult(complete=False)`。先观察 Job 和全部 Pod 消失，才 UID 条件删除 NetworkPolicy，并再次回读策略；不使用 background/force 删除伪装成已停止。
- 没有可信 Job UID 的不确定创建不能自动撤掉网络拒绝。若取消回读首次确认 Job UID，结果 `CleanupResult.handle` 及后续 `SandboxError.handle` 立即携带已验证 UID；即使 Job 删除后 GET 超时、Pod 尚存或策略删除未完成，调用者都须保存新 handle，用于下一轮/重启后的清理，不能继续使用丢失 UID 的旧 UNKNOWN handle。本切片没有后台 sweeper，未知孤儿策略需运维检查后处理。清理协程被取消时抛出带已核验 handle 的 `SandboxCancelled`（`asyncio.CancelledError` 子类），保留任务取消语义且不吞取消；调用者须接收并持久化其 handle。进程崩溃仍可能在 handle 持久化前中断，需上游持久串行化与独立对账；协程取消不等同 Kubernetes Job 已取消，也不宣称进程崩溃窗口已自动修复。
- 不启用 TTL 自动删除 Job，避免丢失对账证据。完成后仍由可信调用者调用 cancel/cleanup 流程；清理后的 sandbox_id 必须在上游保留终态且永不复用。现阶段没有数据库幂等账本，因此不宣称清理后跨重启的 exactly-once。
- 清理与新建同名实例不得并发，上游必须使用持久化串行化/租约。Job/Pod API 消失只说明控制面观察结果，不代表已证明节点物理临时盘擦除；节点失联、遗留进程和 emptyDir 清理仍须 live 验证。

## 运维前置条件与未交付项

可信 client 的 RBAC 应限制到专属 namespace 内 Job/NetworkPolicy get/create/delete、Pod list/get/log；RuntimeClass 仅允许 get 指定 `gvisor`，不授予 exec、secret 读取、任意角色管理或跨 namespace 写入。本包不创建 namespace、RuntimeClass、RBAC 或集群配置。

当前每 Job 提供空工作盘，可用结构化命令在其中构建最小文件并运行测试；**尚未实现授权仓库版本获取、项目快照注入、补丁/产物导出、文件能力注册、并发三沙箱配额、持久审计、后台孤儿回收与 Harness 接线**。这些属于后续 M2 集成，不以日志冒充产物闭环。

真实资源限额仍待验证：CPU/memory 由 kubelet/cgroups 负责；ephemeral-storage 与 emptyDir sizeLimit 常体现为统计/驱逐，不能保证写入到第 10Gi 字节同步失败。还需压力测试日志/临时盘合计、OOM、进程/线程超额、超时、取消与节点失联清理。

无需数据库迁移，因为只新增内部执行后端与数据类，没有修改持久化模型/表。本独立切片不生成阶段完成声明；主树集成后由统一状态记录追踪，原生产晋级门禁保持不变。

## 验证方法与证据边界

独立运行，不加载 control-plane 根 conftest、数据库或完整应用初始化。已安装的测试依赖可离线复用，显式 PYTHONPATH 必须指向本工作树而非父工作树。

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$WORKTREE/services/control-plane/src" \
  "$PYTHON" -m pytest -q -p no:cacheprovider \
  --confcutdir="$WORKTREE/services/control-plane/tests/sandbox" \
  "$WORKTREE/services/control-plane/tests/sandbox"

"$RUFF" check --no-cache \
  "$WORKTREE/services/control-plane/src/obsion/sandbox" \
  "$WORKTREE/services/control-plane/tests/sandbox"
```

本隔离工作树验证结果：**102 项独立测试通过（2.33 秒，含审查修复回归）**；Ruff check 通过；`mypy --platform linux --follow-imports=skip --no-incremental --cache-dir=/dev/null` 对新增包的 4 个源文件通过。mypy 使用 Linux 目标仅为静态检查，不代表运行了 Linux/gVisor 测试。未运行全仓应用/数据库回归，因本切片完全新增且按要求保持独立。

HTTP 测试覆盖成功生命周期、Pod 退出码/期限失败、固定镜像/资源/安全字段、凭据不入 Job、默认关闭、重定向拒绝、创建响应丢失及重启对账、异步取消后回读、规格漂移、UID 防误删、日志字节/总时间边界、分页不假清理、Job/Pod 消失前不删除网络拒绝。

审查补充回归覆盖正常 `Failed=True/BackoffLimitExceeded` 保留退出证据、Job Complete 后 Pod GC 的 UNKNOWN、JSON 转义/中文/控制字符的共享入口字节预算，以及未知清理身份在 Job/Pod/策略 pending、删除响应丢失、回读失败、协程取消和新 adapter 续办之间的保留。已先用失败测试复现原问题，再修复；不会把保留 UNKNOWN 改成无依据成功。

入口测试覆盖 hard/soft rlimit 调用顺序（通过 monkeypatch，不改变宿主限额）、root/平台拒绝、realpath 越界、最小环境、字面 argv、真实本地受控子进程组的超时 SIGKILL，以及 spawn/取消竞态。macOS 上的本地进程组实验不是 Linux RLIMIT_NPROC 执行或 gVisor 证明。

**尚未执行真实 Kubernetes/gVisor/CNI、镜像构建/拉取、真实项目构建、限额压力或集群取消/临时盘清理测试。不得将本次独立测试通过表述为 M2 完整验收或生产可用。**
