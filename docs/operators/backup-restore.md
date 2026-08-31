# Backup, restore, and rollback

PostgreSQL is the transactional source of truth. Redis and object storage are
rebuildable from PostgreSQL plus artifact bytes.

## Backup

1. Take a consistent PostgreSQL backup (volume snapshot or `pg_dump` with `--format=custom`)
   while the control plane is at a known Alembic revision.
2. Snapshot the artifact bucket (`obsion-artifacts`) with the same timestamp.
3. Record the Helm revision, image digests, and Alembic head in the change ticket.
4. Do not back up Redis as authority; session rows live in PostgreSQL.

## Restore

1. Restore PostgreSQL first. Confirm `alembic current` matches the backup revision.
2. Restore the object-store bucket to the same timestamp.
3. Roll the API/Web deployment to the recorded image digest.
4. Run `/health/ready` and a Knowledge + Data smoke Run before returning traffic.

## Migration rollback

Alembic downgrade is allowed only for revisions that are explicitly reversible.
Identity and audit-log breaking migrations have dedicated opt-in tests and must not
be downgraded in production without a restore from backup. Prefer rolling forward
with a new revision.

## Disaster recovery

RPO is the PostgreSQL backup interval. RTO is restore time plus image rollout.
If the secret manager is unavailable, connectors fail closed; they must not fall
back to inline credentials.
