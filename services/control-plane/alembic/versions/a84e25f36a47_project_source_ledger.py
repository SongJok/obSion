"""新增不可变项目来源/配置版本与独立撤销账本，不回填可信来源。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.schema import SchemaItem

revision = "a84e25f36a47"
down_revision = "a83d14e25f36"
branch_labels = None
depends_on = None

_TABLES = (
    "connector_configuration_versions",
    "project_sources",
    "connector_version_revocations",
    "project_source_revocations",
)


def _tenant_fk(table: str, column: str, target: str, name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["organization_id", column],
        [f"{target}.organization_id", f"{target}.id"],
        name=name,
        ondelete="RESTRICT",
    )


def _create(table: str, *items: SchemaItem, primary_key: str = "id") -> None:
    op.create_table(
        table,
        *items,
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint(primary_key),
    )
    op.create_index(f"ix_{table}_organization_id", table, ["organization_id"])


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_connectors_organization_id_id", "connectors", ["organization_id", "id"]
    )
    op.create_unique_constraint(
        "uq_code_repositories_organization_id_id", "code_repositories", ["organization_id", "id"]
    )
    _create(
        "connector_configuration_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("connector_type", sa.String(160), nullable=False),
        sa.Column("environment", sa.String(80), nullable=False),
        sa.Column("endpoint", sa.String(1024)),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.Column("credential_ref", sa.String(500)),
        sa.Column("declared_grants", sa.JSON(), nullable=False),
        sa.Column("allowed_egress", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_connector_config_versions_org_id"),
        _tenant_fk(
            "connector_configuration_versions",
            "connector_id",
            "connectors",
            "fk_connector_config_versions_connector",
        ),
        _tenant_fk(
            "connector_configuration_versions",
            "created_by",
            "users",
            "fk_connector_config_versions_creator",
        ),
        sa.CheckConstraint(
            "length(trim(connector_type)) > 0 AND length(trim(environment)) > 0",
            name="version_identity_nonempty",
        ),
    )
    op.create_index(
        "ix_connector_configuration_versions_connector_id",
        "connector_configuration_versions",
        ["connector_id"],
    )
    _create(
        "project_sources",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("repository_id", sa.Uuid(), nullable=False),
        sa.Column("connector_version_id", sa.Uuid(), nullable=False),
        sa.Column("provider_repository_id", sa.String(500), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("organization_id", "id", name="uq_project_sources_org_id"),
        sa.UniqueConstraint(
            "organization_id",
            "workspace_id",
            "repository_id",
            "connector_version_id",
            name="uq_project_sources_fixed_binding",
        ),
        _tenant_fk("project_sources", "workspace_id", "workspaces", "fk_project_sources_workspace"),
        _tenant_fk(
            "project_sources", "repository_id", "code_repositories", "fk_project_sources_repository"
        ),
        _tenant_fk(
            "project_sources",
            "connector_version_id",
            "connector_configuration_versions",
            "fk_project_sources_version",
        ),
        _tenant_fk("project_sources", "created_by", "users", "fk_project_sources_creator"),
        sa.CheckConstraint("length(trim(provider_repository_id)) > 0", name="provider_id_nonempty"),
    )
    for table, key, target, target_fk, actor_fk in (
        (
            "connector_version_revocations",
            "connector_version_id",
            "connector_configuration_versions",
            "fk_connector_version_revocations_version",
            "fk_connector_version_revocations_actor",
        ),
        (
            "project_source_revocations",
            "source_id",
            "project_sources",
            "fk_project_source_revocations_source",
            "fk_project_source_revocations_actor",
        ),
    ):
        _create(
            table,
            sa.Column(key, sa.Uuid(), nullable=False),
            sa.Column("revoked_by", sa.Uuid(), nullable=False),
            sa.Column("reason_code", sa.String(32), nullable=False),
            sa.Column(
                "revoked_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            _tenant_fk(table, key, target, target_fk),
            _tenant_fk(table, "revoked_by", "users", actor_fk),
            sa.CheckConstraint(
                "reason_code IN ('OPERATOR_REVOKED', 'CONFIGURATION_REPLACED', 'SECURITY_REVOKED')",
                name="revocation_reason",
            ),
            primary_key=key,
        )
    for table in _TABLES:
        # 固定表名；复用已存在的不可变触发器函数，TRUNCATE 也不能抹掉撤销事实。
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable "  # noqa: S608
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION obsion_reject_immutable_mutation()"
        )
        op.execute(
            f"CREATE TRIGGER trg_{table}_no_truncate "  # noqa: S608
            f"BEFORE TRUNCATE ON {table} "
            "FOR EACH STATEMENT EXECUTE FUNCTION obsion_reject_immutable_mutation()"
        )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(sa.text("LOCK TABLE " + ", ".join(_TABLES) + " IN ACCESS EXCLUSIVE MODE"))
    for name in _TABLES:
        table = sa.table(name, sa.column("organization_id"))
        if connection.scalar(sa.select(table.c.organization_id).limit(1)) is not None:
            raise RuntimeError("Refusing to drop a populated project source ledger")
    for name in reversed(_TABLES):
        op.drop_table(name)
    op.drop_constraint(
        "uq_code_repositories_organization_id_id", "code_repositories", type_="unique"
    )
    op.drop_constraint("uq_connectors_organization_id_id", "connectors", type_="unique")
