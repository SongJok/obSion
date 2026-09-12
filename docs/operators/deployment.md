# Deployment guide

Compose is the local development path. Kubernetes is the production path. Both run a
single Python control plane. PostgreSQL remains the transactional source of truth.

## Local stack

```bash
make bootstrap
make compose-up
make migrate
make dev-api
make dev-web
```

Set a non-production `OBSION_DEV_BEARER_TOKEN`. Production mode refuses development
authentication. Do not reuse the example token outside a laptop.

## Managed DingTalk sources in local development

The source API requires the existing migrations through `a3b5c7d9e1f2`. Normal
bootstrap registers the two `knowledge.dingtalk.*` descriptors but creates no
host connection or allowing policy. Use the administrator APIs to bind a
verified DingTalk installation and sender to the intended local user, create an
explicit `dingtalk-managed-development` connector, and bind both descriptors to
that connector and organization. Register the resulting connection through
`/api/v1/knowledge/sources/dingtalk/managed`.

The operator-owned host process is `python -m obsion.knowledge.sync_host`. It
requires development mode, PostgreSQL, durable MinIO storage, the fixed user's
DWS session, and application credentials injected into its environment for the
connector's environment references. Settings loaded from a dotenv file alone do
not inject credential values into the process environment. Keep that injection
inside the local service manager; never pass secrets as command arguments or
store them in a connector specification. The API container does not gain host
DWS access. The worker refreshes only its configured account and never changes
the global DingTalk profile.

Verify that the host and API address the same database before starting the
worker. A separately installed local PostgreSQL can occupy port 5432 even when
Docker reports a published mapping. The 2026-09-12 local deployment uses host
port 56324 and keeps the container's internal port 5432. Verify a known source
identifier and migration head, not merely a successful database connection.

That deployment installed the user LaunchAgent
`com.obsion.development.knowledge-sync`, with local environment injection and
logs under `.data/host-services`. It restarts the worker and loads at user login;
this is a development service with a user DWS session, not production unattended
credential provisioning. The normal Docker services use the current local
images; previous images and protected database backups were retained. A rollback
must consider the matching database/source-access contract, not only an image
tag. See the [deployment and real-message evidence](../release/evidence/productization/20260912-normal-managed-source-qa.json).

## Validated development source snapshots

The current local source worker loads its control-plane package from the immutable
path in `.data/host-services/runtime-manifest.json`, inserted by its launch wrapper.
Editing the checkout or restarting the supervisor does not promote unvalidated
parser changes. After checks pass, package matching API code, pause the source,
retain a database backup and previous manifest, switch both runtime versions, then
resume and recheck current permissions and readable counts. Parser v2 is deployed
under `obsion-api:jsonml-layout-20260912`, also tagged as the ordinary local latest
API/migration image. Web and database schema are unchanged in this increment.

Pause the source before rolling back both packages; recheck fresh bodies before
resuming older code. The local Stream timing observer is a development process at
`.data/host-services/run-observed-stream.py`; it records hashed event identifiers
and handler timing while keeping the original SDK admission and ACK. It is not a
production supervisor or proof of upstream delivery reliability. See the
[layout validation record](../phases/productization-dingtalk-layout-validation.md).

## Kubernetes

1. Provision PostgreSQL 17 with pgvector, Redis, S3-compatible object storage, OIDC,
   TLS termination, a secret manager, and an OTLP/HTTP collector.
2. Create `obsion-database` and `obsion-object-store` secrets. Optionally create
   `obsion-encryption` and set `encryption.existingSecret` so
   `OBSION_SECRET_ENCRYPTION_KEY` is injected from Kubernetes, not Helm values.
   Never put credentials in values.yaml.
3. Review `deploy/helm/obsion/values.yaml`: `OBSION_ENVIRONMENT=production`,
   `OBSION_AUTH_MODE=oidc`, exact `allowedOrigins`, model egress hosts, and
   NetworkPolicy.
4. `helm upgrade --install obsion deploy/helm/obsion --namespace obsion --create-namespace`
5. Confirm `/health/live` and `/health/ready`, then run Knowledge, Data, Engineering,
   Incident, and Support smoke questions.

The chart includes non-root securityContext, read-only root filesystems, probes,
PodDisruptionBudgets, default-deny NetworkPolicy with scoped ingress, an optional API
HPA, a 60s termination grace period with a preStop drain, and an idempotent
pre-upgrade migration Job. See
[Helm README](../../deploy/helm/obsion/README.md), [upgrade](upgrade.md), and
[backup/restore](backup-restore.md).

Staging from clean infrastructure is operator-owned. Passing CI image builds is not a
staging deploy.

## Alpha.1 promotion gate

The CI candidate bundle contains `artifact-manifest.json` and
`release-candidate-report.json`. Verify both before staging. A normal repository-ready
report may have `promotion_eligible: false`; that is expected while operator evidence
is pending and must never be overridden by editing CI output.

To make a future promotion decision, retain repository-auditable evidence for clean
staging/UAT, timed database and object-store restore, registry HIGH/CRITICAL CVE
policy and image signatures, live OIDC/secret-manager/read-replica behavior, security
and data-owner approval, and maintainer publication authority. Only then may an
authorized operator update the candidate gate and run
`obsion validate-release-candidate --require-promotion-eligible`. Passing that command
does not itself deploy or publish anything. Commit only redacted attestations under
`docs/release/evidence/alpha1/`; raw tenant data, tokens, credentials, private logs,
and secret-manager output must remain outside the repository and model context.

## Vendor IM and Knowledge processes

The chart does not create Feishu, DingTalk, or WeCom applications and does not inject
their credentials through values. Run `obsion-im` as a separately managed Experience
process when vendor callbacks or reply delivery are required. Public callbacks need
TLS, exact Host allowlisting, and channel-specific verification. Vendor Knowledge
connectors run inside the control plane through Capability Gateway and require exact
egress, secret references, grants, rate/sync budgets, and ACL policy. Follow the
[0.75.0-dev release notes](../release/0.75.0-dev.md) before enabling tenant traffic.


知识问答在明确资料不足或有效事实复核拒绝时，可继续搜索/读取文档并再次核验。
`OBSION_RUN_MAX_KNOWLEDGE_INVESTIGATION_ROUNDS` 默认3、范围0–6；0关闭额外调查轮次。
轮次仍受任务步数、时间、Token与费用预算约束。真实资料密级必须包含在端点允许范围内，
否则返回模型不可用；不要通过降低资料密级来恢复回答。参见
[ADR0128](../adr/0128-governed-knowledge-investigation.md)。

ADR0128开发部署应先暂停受管来源，备份数据库，核对部署包与验证源码一致，再通过
`PATCH /api/v1/admin/models/endpoints/{id}/processing-scope`配置经授权的数据范围。
该入口需要`models.write`及对应操作的ALLOW策略，成功与拒绝均有审计；不会修改凭据或地址。
只有在已明确允许外部端点处理敏感资料时，才在该本地部署调整私有模型强制路由开关。
本仓库默认继续要求私有模型。恢复来源必须重新核验当前可用数量和版本标识。
如回退到缺少密级传递修复的旧镜像，须保持来源暂停，不能将其作为已修复版本恢复回答。
