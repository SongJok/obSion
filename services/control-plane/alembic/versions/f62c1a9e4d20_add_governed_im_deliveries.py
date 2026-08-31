"""add governed IM delivery ledger

Revision ID: f62c1a9e4d20
Revises: e82d1b3c4a56
Create Date: 2026-08-30 10:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f62c1a9e4d20"
down_revision: str | None = "e82d1b3c4a56"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _sha256_hex_check(column: str) -> str:
    remainder = column
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    return f"length({column}) = 64 AND length({remainder}) = 0"


def upgrade() -> None:
    op.create_table(
        "im_deliveries",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=255), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("policy_decision_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by", sa.Uuid(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("vendor_message_id", sa.String(length=500), nullable=True),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('PENDING', 'SENT', 'FAILED')",
            name=op.f("ck_im_deliveries_valid_status"),
        ),
        sa.CheckConstraint(
            "length(trim(channel)) > 0",
            name=op.f("ck_im_deliveries_nonempty_im_delivery_channel"),
        ),
        sa.CheckConstraint(
            "length(trim(conversation_id)) > 0",
            name=op.f("ck_im_deliveries_nonempty_im_delivery_conversation"),
        ),
        sa.CheckConstraint(
            "attempt_count > 0",
            name=op.f("ck_im_deliveries_positive_im_delivery_attempts"),
        ),
        sa.CheckConstraint(
            _sha256_hex_check("content_fingerprint"),
            name=op.f("ck_im_deliveries_im_delivery_content_fingerprint_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_im_deliveries_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "requested_by"],
            ["users.organization_id", "users.id"],
            name="fk_im_deliveries_org_requester",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name="fk_im_deliveries_org_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["policy_decision_id"],
            ["policy_decisions.id"],
            name=op.f("fk_im_deliveries_policy_decision_id_policy_decisions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_im_deliveries")),
        sa.UniqueConstraint(
            "organization_id",
            "run_id",
            name="uq_im_deliveries_org_run",
        ),
    )
    op.create_index(
        op.f("ix_im_deliveries_organization_id"),
        "im_deliveries",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_im_deliveries_run_id"),
        "im_deliveries",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_im_deliveries_status"),
        "im_deliveries",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_im_deliveries_status"), table_name="im_deliveries")
    op.drop_index(op.f("ix_im_deliveries_run_id"), table_name="im_deliveries")
    op.drop_index(op.f("ix_im_deliveries_organization_id"), table_name="im_deliveries")
    op.drop_table("im_deliveries")
