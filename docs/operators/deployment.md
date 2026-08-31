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
