"""add the authorized enterprise resource catalog

Revision ID: d6e8f0a2b4c6
Revises: c5d7e9f1a3b5
Create Date: 2026-09-19 18:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d6e8f0a2b4c6"
down_revision: str | None = "c5d7e9f1a3b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(*values: str, name: str, length: int = 32) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, length=length)


def _grant_catalog_discovery_to_system_roles() -> None:
    connection = op.get_bind()
    roles = sa.table(
        "roles",
        sa.column("id", sa.Uuid()),
        sa.column("name", sa.String()),
        sa.column("permissions", sa.JSON()),
        sa.column("system", sa.Boolean()),
    )
    rows = connection.execute(
        sa.select(roles.c.id, roles.c.permissions).where(
            roles.c.system.is_(True),
            roles.c.name.in_(("engineer", "analyst", "operator", "support", "viewer")),
        )
    ).all()
    for role_id, raw_permissions in rows:
        permissions = list(raw_permissions) if isinstance(raw_permissions, list) else []
        if "catalog.discover" in permissions:
            continue
        permissions.append("catalog.discover")
        connection.execute(
            sa.update(roles).where(roles.c.id == role_id).values(permissions=permissions)
        )


def upgrade() -> None:
    resource_kind = _enum(
        "BUSINESS_DOMAIN",
        "SERVICE",
        "REPOSITORY",
        "API",
        "TABLE",
        "METRIC",
        "LOG",
        "DEPLOYMENT",
        "DATA_SOURCE",
        "LOGICAL_DATA_SOURCE",
        "PHYSICAL_DATA_CLUSTER",
        name="catalogresourcekind",
        length=40,
    )
    verification = _enum(
        "DECLARED",
        "STATIC_INFERENCE",
        "RUNTIME_OBSERVED",
        name="catalogverificationlevel",
        length=24,
    )
    op.create_table(
        "catalog_resources",
        sa.Column("kind", resource_kind, nullable=False),
        sa.Column("canonical_key", sa.String(length=500), nullable=False),
        sa.Column("display_name", sa.String(length=240), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("owner", sa.String(length=320), nullable=True),
        sa.Column("version", sa.String(length=200), nullable=True),
        sa.Column(
            "state",
            _enum(
                "ACTIVE",
                "UNCONFIGURED",
                "STALE",
                "UNAVAILABLE",
                name="catalogresourcestate",
                length=24,
            ),
            nullable=False,
        ),
        sa.Column("required_permission", sa.String(length=200), nullable=False),
        sa.Column(
            "classification",
            _enum(
                "PUBLIC",
                "INTERNAL",
                "CONFIDENTIAL",
                "RESTRICTED",
                name="classification",
            ),
            nullable=False,
        ),
        sa.Column("access_policy", sa.JSON(), nullable=False),
        sa.Column("search_terms", sa.JSON(), nullable=False),
        sa.Column("source_type", sa.String(length=80), nullable=False),
        sa.Column("source_ref", sa.String(length=1000), nullable=False),
        sa.Column("source_version", sa.String(length=200), nullable=True),
        sa.Column("verification_level", verification, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(trim(canonical_key)) > 0", name="nonempty_catalog_resource_key"),
        sa.CheckConstraint("length(trim(display_name)) > 0", name="nonempty_catalog_display_name"),
        sa.CheckConstraint("length(trim(source_type)) > 0", name="nonempty_catalog_source_type"),
        sa.CheckConstraint("length(trim(source_ref)) > 0", name="nonempty_catalog_source_ref"),
        sa.CheckConstraint(
            "length(trim(required_permission)) > 0",
            name="nonempty_catalog_required_permission",
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from",
            name="ordered_catalog_resource_validity",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "kind",
            "canonical_key",
            name="uq_catalog_resources_org_kind_key",
        ),
    )
    op.create_index(
        op.f("ix_catalog_resources_organization_id"),
        "catalog_resources",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_catalog_resources_lookup",
        "catalog_resources",
        ["organization_id", "kind", "canonical_key"],
        unique=False,
    )

    op.create_table(
        "catalog_relations",
        sa.Column(
            "relation_type",
            _enum(
                "DOMAIN_CONTAINS_SERVICE",
                "SERVICE_DEPLOYED_AS",
                "DEPLOYMENT_BUILT_FROM",
                "REPOSITORY_EXPOSES_API",
                "DATA_SOURCE_CONTAINS_TABLE",
                "LOGICAL_DATA_SOURCE_MAPS_TO_PHYSICAL_CLUSTER",
                "METRIC_DERIVED_FROM_TABLE",
                "SERVICE_EMITS_LOG",
                name="catalogrelationtype",
                length=56,
            ),
            nullable=False,
        ),
        sa.Column("source_kind", resource_kind, nullable=False),
        sa.Column("source_key", sa.String(length=500), nullable=False),
        sa.Column("source_version", sa.String(length=200), nullable=True),
        sa.Column("target_kind", resource_kind, nullable=False),
        sa.Column("target_key", sa.String(length=500), nullable=False),
        sa.Column("target_version", sa.String(length=200), nullable=True),
        sa.Column("provenance_source", sa.String(length=80), nullable=False),
        sa.Column("provenance_ref", sa.String(length=1000), nullable=False),
        sa.Column("provenance_version", sa.String(length=200), nullable=True),
        sa.Column(
            "evidence_category",
            _enum(
                "DOCUMENT",
                "DATA",
                "SQL",
                "METRIC",
                "LOG",
                "TRACE",
                "CODE",
                "GIT",
                "DEPLOYMENT",
                "CONFIG",
                "TOOL",
                name="evidencetype",
            ),
            nullable=False,
        ),
        sa.Column("verification_level", verification, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("valid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("required_permission", sa.String(length=200), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("length(trim(source_key)) > 0", name="nonempty_catalog_source_key"),
        sa.CheckConstraint("length(trim(target_key)) > 0", name="nonempty_catalog_target_key"),
        sa.CheckConstraint(
            "length(trim(provenance_source)) > 0", name="nonempty_catalog_relation_source"
        ),
        sa.CheckConstraint(
            "length(trim(provenance_ref)) > 0", name="nonempty_catalog_relation_ref"
        ),
        sa.CheckConstraint(
            "length(trim(required_permission)) > 0",
            name="nonempty_catalog_relation_permission",
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from",
            name="ordered_catalog_relation_validity",
        ),
        sa.CheckConstraint(
            "relation_type != 'LOGICAL_DATA_SOURCE_MAPS_TO_PHYSICAL_CLUSTER' OR "
            "(verification_level = 'RUNTIME_OBSERVED' AND evidence_category = 'DEPLOYMENT')",
            name="observed_physical_cluster_mapping",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "relation_type",
            "source_kind",
            "source_key",
            "target_kind",
            "target_key",
            "provenance_ref",
            name="uq_catalog_relations_fact",
        ),
    )
    op.create_index(
        op.f("ix_catalog_relations_organization_id"),
        "catalog_relations",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_catalog_relations_source",
        "catalog_relations",
        ["organization_id", "source_kind", "source_key"],
        unique=False,
    )
    op.create_index(
        "ix_catalog_relations_target",
        "catalog_relations",
        ["organization_id", "target_kind", "target_key"],
        unique=False,
    )
    _grant_catalog_discovery_to_system_roles()


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text("LOCK TABLE catalog_relations, catalog_resources IN ACCESS EXCLUSIVE MODE")
        )
    for name in ("catalog_relations", "catalog_resources"):
        table = sa.table(name, sa.column("id"))
        if connection.scalar(sa.select(table.c.id).limit(1)) is not None:
            raise RuntimeError("Refusing to drop a populated enterprise catalog")
    op.drop_table("catalog_relations")
    op.drop_table("catalog_resources")
