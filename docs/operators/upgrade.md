# Upgrade guide

1. Read the phase report and Alembic revision notes for the target version.
2. Back up PostgreSQL and the artifact bucket.
3. `alembic upgrade head` against staging first. `alembic check` must report no drift.
4. Run `make check`, `uv run obsion validate-eval-gates`, and `uv run obsion scan-secrets`.
5. Roll Helm with the new image tags. Keep `OBSION_ENVIRONMENT=production` and
   `OBSION_AUTH_MODE=oidc`.
6. Confirm `/health/ready`, then execute the Knowledge, Data, Engineering, Incident,
   and Support smoke questions.
7. If verification fails, restore PostgreSQL and the previous Helm revision. Do not
   skip Policy, Gateway, or production read-only defaults to recover traffic.

For an Alpha.1 candidate, obtain the clean CI artifact bundle rather than rebuilding
from an uncommitted operator checkout. Confirm its source revision, hashes, unskipped
clean-room steps, and candidate report. `promotion_eligible: false` blocks promotion
but does not invalidate the repository build; complete the separately owned staging,
restore, security, identity/secrets, signature, and publication gates first.

For `0.75.0-dev`, Phases 68-75 add no Alembic revision. Upgrade the control plane,
Workbench, and separately managed `obsion-im` process as one tested image set; then
reconcile vendor connector manifests and run the explicit vendor smoke matrix. The
complete secret-name, origin, rollout, and no-schema-downgrade contract is in the
[0.75.0-dev release notes](../release/0.75.0-dev.md).
