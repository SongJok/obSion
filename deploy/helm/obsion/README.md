# Obsion Helm chart

The chart deploys the control plane, durable run workers, Workbench, and an idempotent
pre-install/pre-upgrade migration job. PostgreSQL with the pgvector extension, Redis,
object storage, OIDC, and an OTLP collector are external production dependencies.
The PostgreSQL service must provide pgvector 0.8 or newer before the migration hook
runs.

The API replicas also host the reliable automation scheduler and worker. PostgreSQL
row locks, unique occurrence keys, and execution leases make this safe with multiple
replicas. Tune `config.automation.workerConcurrency`, `pollIntervalSeconds`, and
`leaseSeconds` with database and Harness capacity; set `enabled: false` only when an
external maintenance window intentionally pauses all background automation.

The same replicas host the governed Action worker. Tune `config.actions` independently
from analysis automation. Disabling the worker pauses approved actions without
weakening policy. The server always rejects production targets and the deferred
config/restart/deploy action types in V1, regardless of Helm values.

Create the required secrets before installation:

```bash
kubectl create secret generic obsion-database \
  --from-literal=url='postgresql+asyncpg://USER:PASSWORD@HOST:5432/obsion'
kubectl create secret generic obsion-object-store \
  --from-literal=access-key='ACCESS_KEY' \
  --from-literal=secret-key='SECRET_KEY'
helm upgrade --install obsion deploy/helm/obsion --namespace obsion --create-namespace
```

The default values deliberately fail closed with production mode and OIDC enabled.
Before installation, a values file must set all OIDC fields, public URLs, TLS ingress,
trusted image digests, external secrets, and network policies matching the deployment's
data sources. The API refuses to boot when production identity configuration is
incomplete. Build the web image with `NEXT_PUBLIC_OBSION_API_URL=/api/v1` when using
the chart ingress.
