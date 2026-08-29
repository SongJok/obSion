"""rename audit records to audit logs

Revision ID: f7a1b2c3d4e5
Revises: e6f9a0123bcd
Create Date: 2026-08-26 22:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "f7a1b2c3d4e5"
down_revision: str | None = "e6f9a0123bcd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_TABLE = "audit_records"
_NEW_TABLE = "audit_logs"

_OLD_TO_NEW_CONSTRAINT_NAMES = (
    ("pk_audit_records", "pk_audit_logs"),
    (
        "fk_audit_records_approval_id_approvals",
        "fk_audit_logs_approval_id_approvals",
    ),
    (
        "fk_audit_records_organization_id_organizations",
        "fk_audit_logs_organization_id_organizations",
    ),
    (
        "fk_audit_records_policy_decision_id_policy_decisions",
        "fk_audit_logs_policy_decision_id_policy_decisions",
    ),
)

_OLD_TO_NEW_INDEX_NAMES = (
    ("ix_audit_records_action", "ix_audit_logs_action"),
    ("ix_audit_records_correlation_id", "ix_audit_logs_correlation_id"),
    ("ix_audit_records_organization_id", "ix_audit_logs_organization_id"),
)


def upgrade() -> None:
    op.rename_table(_OLD_TABLE, _NEW_TABLE)
    for old_name, new_name in _OLD_TO_NEW_CONSTRAINT_NAMES:
        _rename_constraint(_NEW_TABLE, old_name, new_name)
    for old_name, new_name in _OLD_TO_NEW_INDEX_NAMES:
        _rename_index(old_name, new_name)
    op.execute(
        "ALTER TRIGGER trg_audit_records_immutable ON audit_logs RENAME TO trg_audit_logs_immutable"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TRIGGER trg_audit_logs_immutable ON audit_logs RENAME TO trg_audit_records_immutable"
    )
    for old_name, new_name in reversed(_OLD_TO_NEW_INDEX_NAMES):
        _rename_index(new_name, old_name)
    for old_name, new_name in reversed(_OLD_TO_NEW_CONSTRAINT_NAMES):
        _rename_constraint(_NEW_TABLE, new_name, old_name)
    op.rename_table(_NEW_TABLE, _OLD_TABLE)


def _rename_constraint(table_name: str, old_name: str, new_name: str) -> None:
    # 显式重命名保留原对象、数据、OID 以及外键语义，而不是 drop/recreate。
    op.execute(  # noqa: S608 -- names come exclusively from fixed constants above
        f'ALTER TABLE "{table_name}" RENAME CONSTRAINT "{old_name}" TO "{new_name}"'
    )


def _rename_index(old_name: str, new_name: str) -> None:
    op.execute(  # noqa: S608 -- names come exclusively from fixed constants above
        f'ALTER INDEX "{old_name}" RENAME TO "{new_name}"'
    )
