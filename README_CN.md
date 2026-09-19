# Obsion

Obsion 是开源的企业 Agent 运行时与智能工作台。它以统一 Python 控制面运行受治理的
企业知识、数据、代码和运行时任务，并保留每次结果背后的证据、策略决定、审批与可回放
轨迹。

当前仓库版本为 **`0.98.0-dev`**。自主 Harness 改造和 P1 真实任务质量验证仍在进行，
P1 与 Phase 99 均未完成。历史 **`0.80.0-alpha.1`** 仅是仓库内候选记录，不代表当前
版本、外部发布或生产批准。权威状态见
[项目状态](docs/project-status.yaml)；版本号和“已完成阶段”数量不能替代发布证据。

## 当前边界

仓库已经包含以下受治理基础能力，但能否使用取决于部署配置、显式授权和对应验收：

- `Workspace → Thread → Turn → Run → Step → Event` 持久 Harness 模型；
- 统一 Capability Gateway、Policy Engine、审批、审计、速率限制和凭据代理；
- 企业文档检索、静态代码图谱、受限只读查询及证据化结果；
- 模型 Profile、端点路由、调用成本记录和失败关闭；
- Web、CLI、IDE、桌面端、SDK 与 IM 适配入口，共用同一 App Server；
- 持久任务恢复、取消、回放、Artifact、Evidence 和评测框架；
- 开发/预发布环境中的受控 PR 与工单动作合同；
- 沙箱合同和 Kubernetes/gVisor 适配器基础实现。

这些条目是源码能力清单，不是所有连接器、真实租户、模型、沙箱集群或业务场景都已验收
的声明。生产写入、部署、重启、配置修改、非幂等写入和 L4–L5 操作继续由服务端拒绝。
缺少真实数据、凭据或回执时，结果必须是 `NOT_RUN`、`BLOCKED` 或明确拒绝，不能用
Mock 冒充真实通过。

## 架构

```text
Web / IM / CLI / IDE / SDK / API
                │
          统一 App Server
                │
 Workspace → Thread → Turn → Run → Step → Event
                │
 Observe → Understand → Plan → Execute → Verify → Reflect → Respond
                │
       Capability Gateway + Policy Engine
                │
 Knowledge / Data / Code / Runtime / Sandbox
                │
          Evidence + Artifact + Audit
```

PostgreSQL 是事务事实源；Redis 用于分布式协调；S3 兼容存储保存大 Artifact。Agent 不接收
连接器凭据，所有外部能力必须经过 Gateway，所有授权决定必须经过 Policy Engine。

## 本地开发

需要 Python 3.12、uv、Node.js 22、npm、Docker 与 Docker Compose：

```bash
cp .env.example .env
make bootstrap
make compose-up
make migrate
make dev-api
```

另一个终端运行 `make dev-web`。按 `.env.example` 的开发端口，Web 地址为
<http://localhost:53001>，API 文档为 <http://localhost:58081/api/docs>。端口可由 `.env`
覆盖。

创建或轮换本地管理员时，不要把密码写进命令参数：

```bash
uv run obsion provision-user --email admin@example.com --role admin
```

命令从标准输入、`OBSION_PROVISION_PASSWORD` 或隐藏输入读取密码。模型与连接器密钥只放在
未纳入版本控制的环境或 Secret Manager 中；Agent、配置文档和源码不得包含明文凭据。

## 验证

```bash
make check
```

完整工程门只有在同一候选的 lint、类型检查、测试和构建全部完成后才算通过。真实企业
验收与本地合成测试分开记账；上游失败导致的跳过不能记作成功。当前质量事实、未执行项和
阻断项见 [项目状态](docs/project-status.yaml) 及 [阶段报告](docs/phases/)。

## 文档

- [系统设计](docs/architecture/system-design.md)
- [产品路线](docs/product/roadmap.md)
- [安全模型](docs/security/security-model.md)
- [ADR](docs/adr/)
- [阶段报告](docs/phases/)
- [贡献指南](CONTRIBUTING.md)

## 许可证

[MIT License](LICENSE)
