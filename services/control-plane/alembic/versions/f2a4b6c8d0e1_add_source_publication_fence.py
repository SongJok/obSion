"""Serialize local authorization changes with final publication and delivery.

Revision ID: f2a4b6c8d0e1
Revises: e1f3a6b8c5d7
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f2a4b6c8d0e1"
down_revision: str | None = "e1f3a6b8c5d7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Ignore only operational fields. Future authorization fields are protected by
# default, including direct SQL writes. Every listed table has organization_id.
TABLES: dict[str, tuple[str, ...]] = {
    "users": (),
    "departments": (),
    "roles": (),
    "user_roles": (),
    "policies": (),
    "documents": (),
    "document_versions": (),
    "knowledge_sync_sources": (
        "generation",
        "scan_state",
        "next_poll_at",
        "lease_token",
        "lease_expires_at",
        "last_success_at",
        "last_error_code",
    ),
    "knowledge_sync_items": ("seen_generation", "title", "gaps", "last_error_code"),
    "connectors": ("last_health",),
    "connector_configuration_versions": (),
    "connector_version_revocations": (),
    "im_installations": (),
    "im_principal_bindings": (),
    "im_installation_bindings": (),
    "im_group_audiences": (),
    "im_conversation_audiences": (),
    "workspaces": (),
    "workspace_members": (),
    "capability_definitions": (),
    "capability_versions": (),
    "capability_bindings": (),
    "secret_references": (),
}


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION obsion_source_fence_key(organization uuid) RETURNS bigint
        LANGUAGE sql IMMUTABLE STRICT AS $$
            SELECT hashtextextended('obsion.source-publication:' || organization::text, 0)
        $$
    """)
    op.execute("""
        CREATE FUNCTION obsion_acquire_source_fence(organization uuid) RETURNS boolean
        LANGUAGE plpgsql AS $$
        DECLARE old_timeout text;
        BEGIN
            -- A pre-existing repeatable-read snapshot cannot be made current by
            -- acquiring a lock. Refuse it rather than validate stale permissions.
            IF current_setting('transaction_isolation') <> 'read committed' THEN
                RETURN false;
            END IF;
            old_timeout := current_setting('lock_timeout');
            PERFORM set_config('lock_timeout', '2000ms', true);
            BEGIN
                PERFORM pg_advisory_xact_lock_shared(obsion_source_fence_key(organization));
            EXCEPTION WHEN lock_not_available THEN
                PERFORM set_config('lock_timeout', old_timeout, true);
                RETURN false;
            END;
            PERFORM set_config('lock_timeout', old_timeout, true);
            RETURN true;
        END $$
    """)
    op.execute("""
        CREATE FUNCTION obsion_guard_source_authorization() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE ignored text[]; old_org uuid; new_org uuid; key bigint;
        BEGIN
            ignored := ARRAY['updated_at'] || TG_ARGV;
            IF TG_OP = 'UPDATE' AND
               (to_jsonb(OLD) - ignored) IS NOT DISTINCT FROM (to_jsonb(NEW) - ignored) THEN
                IF TG_TABLE_NAME <> 'connectors' THEN RETURN NEW; END IF;
                -- The source predicate compares these JSON columns as stored
                -- text. JSONB equality would miss an invalidating reorder.
                IF OLD.configuration::text IS NOT DISTINCT FROM NEW.configuration::text
                   AND OLD.declared_grants::text IS NOT DISTINCT FROM NEW.declared_grants::text
                   AND OLD.allowed_egress::text IS NOT DISTINCT FROM NEW.allowed_egress::text THEN
                    RETURN NEW;
                END IF;
            END IF;
            IF TG_OP <> 'INSERT' THEN old_org := OLD.organization_id; END IF;
            IF TG_OP <> 'DELETE' THEN new_org := NEW.organization_id; END IF;
            -- Organization moves are not expected, but fence both sides in
            -- deterministic order instead of leaving the old side unprotected.
            FOR key IN SELECT DISTINCT obsion_source_fence_key(value)
                       FROM unnest(ARRAY[old_org, new_org]) value
                       WHERE value IS NOT NULL ORDER BY 1
            LOOP
                PERFORM pg_advisory_xact_lock(key);
            END LOOP;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END $$
    """)
    for table, ignored in TABLES.items():
        arguments = ", ".join(f"'{name}'" for name in ignored)
        op.execute(
            f"CREATE TRIGGER trg_{table}_source_fence "  # noqa: S608 -- fixed migration allowlist
            f"BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW "
            f"EXECUTE FUNCTION obsion_guard_source_authorization({arguments})"
        )


def downgrade() -> None:
    for table in TABLES:
        op.execute(f"DROP TRIGGER trg_{table}_source_fence ON {table}")  # noqa: S608
    op.execute("DROP FUNCTION obsion_guard_source_authorization()")
    op.execute("DROP FUNCTION obsion_acquire_source_fence(uuid)")
    op.execute("DROP FUNCTION obsion_source_fence_key(uuid)")
