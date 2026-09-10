"""add durable IM delivery outbox

Revision ID: d0e2f5a7b3c4
Revises: c9d1e4f6a2b3
Create Date: 2026-09-09 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d0e2f5a7b3c4"
down_revision: str | None = "c9d1e4f6a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _sha256_hex_check(column: str) -> str:
    remainder = column
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    return f"length({column}) = 64 AND length({remainder}) = 0"


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_policy_decisions_org_id", "policy_decisions", ["organization_id", "id"]
    )
    op.add_column(
        "im_deliveries",
        sa.Column("send_attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column(
        "im_deliveries",
        sa.Column("claim_generation", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.add_column("im_deliveries", sa.Column("lease_owner", sa.String(length=200)))
    op.add_column("im_deliveries", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column("im_deliveries", sa.Column("recipient_snapshot", sa.JSON()))
    op.add_column("im_deliveries", sa.Column("reconciled_at", sa.DateTime(timezone=True)))
    op.add_column("im_deliveries", sa.Column("reconciled_by", sa.Uuid()))
    op.add_column("im_deliveries", sa.Column("reconciliation_outcome", sa.String(length=32)))
    op.add_column("im_deliveries", sa.Column("reconciliation_evidence", sa.String(length=500)))
    op.create_unique_constraint(
        "uq_im_deliveries_organization_id_id", "im_deliveries", ["organization_id", "id"]
    )
    op.create_foreign_key(
        "fk_im_deliveries_org_reconciler",
        "im_deliveries",
        "users",
        ["organization_id", "reconciled_by"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_im_deliveries_lease_expires_at"),
        "im_deliveries",
        ["lease_expires_at"],
    )
    op.create_check_constraint(
        op.f("ck_im_deliveries_nonnegative_im_delivery_sends"),
        "im_deliveries",
        "send_attempt_count >= 0",
    )
    op.create_check_constraint(
        op.f("ck_im_deliveries_nonnegative_im_delivery_generation"),
        "im_deliveries",
        "claim_generation >= 0",
    )
    op.drop_constraint(op.f("ck_im_deliveries_valid_status"), "im_deliveries", type_="check")
    op.create_check_constraint(
        op.f("ck_im_deliveries_valid_status"),
        "im_deliveries",
        "status IN ('PENDING', 'PROCESSING', 'UNKNOWN', 'SENT', 'FAILED')",
    )

    op.create_table(
        "im_delivery_attempts",
        sa.Column("delivery_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("claim_generation", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("claimed_by", sa.String(length=200), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_decision_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_snapshot", sa.JSON(), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("vendor_message_id", sa.String(length=500)),
        sa.Column("failure_code", sa.String(length=100)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("reconciled_at", sa.DateTime(timezone=True)),
        sa.Column("reconciled_by", sa.Uuid()),
        sa.Column("reconciliation_outcome", sa.String(length=32)),
        sa.Column("reconciliation_evidence", sa.String(length=500)),
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
            "ordinal > 0", name=op.f("ck_im_delivery_attempts_positive_im_delivery_attempt_ordinal")
        ),
        sa.CheckConstraint(
            "claim_generation > 0",
            name=op.f("ck_im_delivery_attempts_positive_im_delivery_attempt_generation"),
        ),
        sa.CheckConstraint(
            "status IN ('PROCESSING', 'UNKNOWN', 'SENT', 'FAILED')",
            name=op.f("ck_im_delivery_attempts_valid_im_delivery_attempt_status"),
        ),
        sa.CheckConstraint(
            _sha256_hex_check("content_fingerprint"),
            name=op.f("ck_im_delivery_attempts_attempt_content_fingerprint_sha256"),
        ),
        sa.CheckConstraint(
            "length(trim(idempotency_key)) > 0",
            name=op.f("ck_im_delivery_attempts_attempt_idempotency_key_nonempty"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_im_delivery_attempts_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "delivery_id"],
            ["im_deliveries.organization_id", "im_deliveries.id"],
            name="fk_im_delivery_attempts_org_delivery",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "policy_decision_id"],
            ["policy_decisions.organization_id", "policy_decisions.id"],
            name="fk_im_delivery_attempts_org_policy_decision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "reconciled_by"],
            ["users.organization_id", "users.id"],
            name="fk_im_delivery_attempts_org_reconciler",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_im_delivery_attempts")),
        sa.UniqueConstraint(
            "organization_id", "id", name="uq_im_delivery_attempts_organization_id_id"
        ),
        sa.UniqueConstraint(
            "delivery_id", "ordinal", name="uq_im_delivery_attempts_delivery_ordinal"
        ),
        sa.UniqueConstraint(
            "organization_id",
            "channel",
            "vendor_message_id",
            name="uq_im_delivery_attempts_vendor_receipt",
        ),
    )
    op.create_index(
        op.f("ix_im_delivery_attempts_organization_id"),
        "im_delivery_attempts",
        ["organization_id"],
    )
    op.create_index(
        op.f("ix_im_delivery_attempts_delivery_id"),
        "im_delivery_attempts",
        ["delivery_id"],
    )
    op.create_index(op.f("ix_im_delivery_attempts_channel"), "im_delivery_attempts", ["channel"])
    op.create_index(op.f("ix_im_delivery_attempts_status"), "im_delivery_attempts", ["status"])
    op.create_index(
        op.f("ix_im_delivery_attempts_created_at"),
        "im_delivery_attempts",
        ["created_at"],
    )

    op.execute("""
        CREATE FUNCTION obsion_guard_im_delivery_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'PENDING' OR NEW.send_attempt_count <> 0
                   OR NEW.claim_generation <> 0 OR NEW.lease_owner IS NOT NULL
                   OR NEW.lease_expires_at IS NOT NULL OR NEW.vendor_message_id IS NOT NULL
                   OR NEW.delivered_at IS NOT NULL OR NEW.reconciliation_required_at IS NOT NULL
                   OR NEW.reconciled_at IS NOT NULL OR NEW.reconciled_by IS NOT NULL
                   OR NEW.reconciliation_outcome IS NOT NULL
                   OR NEW.reconciliation_evidence IS NOT NULL THEN
                    RAISE EXCEPTION 'IM delivery must begin pending' USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;

            IF ROW(NEW.id, NEW.organization_id, NEW.run_id, NEW.channel,
                   NEW.conversation_id, NEW.content_fingerprint, NEW.requested_by, NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.id, OLD.organization_id, OLD.run_id, OLD.channel,
                   OLD.conversation_id, OLD.content_fingerprint, OLD.requested_by,
                   OLD.created_at) THEN
                RAISE EXCEPTION 'IM delivery lineage is immutable' USING ERRCODE = '23514';
            END IF;
            IF OLD.recipient_snapshot IS NOT NULL
               AND NEW.recipient_snapshot::jsonb IS DISTINCT FROM OLD.recipient_snapshot::jsonb THEN
                RAISE EXCEPTION 'IM delivery recipient snapshot is immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.status = 'SENT' AND ROW(NEW.status, NEW.vendor_message_id, NEW.delivered_at)
               IS DISTINCT FROM ROW(OLD.status, OLD.vendor_message_id, OLD.delivered_at) THEN
                RAISE EXCEPTION 'IM delivery sent outcome is immutable' USING ERRCODE = '23514';
            END IF;

            IF NEW.status = 'PROCESSING' THEN
                IF OLD.status IN ('PENDING', 'FAILED') THEN
                    IF NEW.claim_generation <> OLD.claim_generation + 1
                       OR NEW.send_attempt_count <> OLD.send_attempt_count + 1
                       OR NEW.lease_owner IS NULL OR NEW.lease_expires_at IS NULL
                       OR NEW.last_attempt_at IS NULL OR NEW.next_attempt_at IS NOT NULL
                       OR NEW.reconciliation_required_at IS NOT NULL THEN
                        RAISE EXCEPTION 'IM delivery claim generation is invalid'
                            USING ERRCODE = '23514';
                    END IF;
                ELSIF OLD.status = 'PROCESSING' THEN
                    IF NEW.claim_generation <> OLD.claim_generation
                       OR NEW.send_attempt_count <> OLD.send_attempt_count
                       OR NEW.lease_owner IS DISTINCT FROM OLD.lease_owner
                       OR OLD.lease_expires_at <= clock_timestamp()
                       OR NEW.lease_expires_at <= OLD.lease_expires_at THEN
                        RAISE EXCEPTION 'IM delivery heartbeat is invalid'
                            USING ERRCODE = '23514';
                    END IF;
                ELSE
                    RAISE EXCEPTION 'IM delivery cannot enter processing'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF OLD.status = 'PROCESSING' THEN
                IF NEW.claim_generation <> OLD.claim_generation
                   OR NEW.send_attempt_count <> OLD.send_attempt_count
                   OR NEW.lease_owner IS NOT NULL OR NEW.lease_expires_at IS NOT NULL THEN
                    RAISE EXCEPTION 'IM delivery completion does not own current claim'
                        USING ERRCODE = '23514';
                END IF;
                IF NEW.status IN ('SENT', 'FAILED')
                   AND OLD.lease_expires_at <= clock_timestamp() THEN
                    RAISE EXCEPTION 'IM delivery claim expired before completion'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.claim_generation <> OLD.claim_generation
               OR NEW.send_attempt_count <> OLD.send_attempt_count
               OR NEW.lease_owner IS NOT NULL OR NEW.lease_expires_at IS NOT NULL THEN
                RAISE EXCEPTION 'IM delivery claim fields changed outside processing'
                    USING ERRCODE = '23514';
            END IF;

            IF OLD.status = 'UNKNOWN' AND NEW.status <> 'UNKNOWN' THEN
                IF NEW.status NOT IN ('SENT', 'FAILED') OR NEW.reconciled_at IS NULL
                   OR NEW.reconciled_by IS NULL
                   OR NEW.reconciliation_outcome NOT IN ('SENT', 'CONFIRMED_UNSENT')
                   OR NEW.reconciliation_evidence IS NULL THEN
                    RAISE EXCEPTION 'IM delivery reconciliation evidence is required'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF OLD.status <> 'UNKNOWN'
               AND NEW.reconciled_at IS DISTINCT FROM OLD.reconciled_at THEN
                RAISE EXCEPTION 'IM delivery reconciliation requires unknown state'
                    USING ERRCODE = '23514';
            END IF;

            IF NEW.status = 'SENT' THEN
                IF NEW.vendor_message_id IS NULL OR length(trim(NEW.vendor_message_id)) = 0
                   OR NEW.delivered_at IS NULL OR NEW.failure_code IS NOT NULL
                   OR NEW.next_attempt_at IS NOT NULL
                   OR NEW.reconciliation_required_at IS NOT NULL THEN
                    RAISE EXCEPTION 'IM delivery sent state is inconsistent'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.status = 'UNKNOWN' THEN
                IF NEW.vendor_message_id IS NOT NULL OR NEW.delivered_at IS NOT NULL
                   OR NEW.reconciliation_required_at IS NULL OR NEW.next_attempt_at IS NOT NULL THEN
                    RAISE EXCEPTION 'IM delivery unknown state is inconsistent'
                        USING ERRCODE = '23514';
                END IF;
            ELSE
                IF NEW.vendor_message_id IS NOT NULL OR NEW.delivered_at IS NOT NULL
                   OR NEW.reconciliation_required_at IS NOT NULL THEN
                    RAISE EXCEPTION 'IM delivery nonterminal state is inconsistent'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER im_delivery_update_guard BEFORE INSERT OR UPDATE ON im_deliveries
        FOR EACH ROW EXECUTE FUNCTION obsion_guard_im_delivery_update();
    """)

    op.execute("""
        CREATE FUNCTION obsion_guard_im_delivery_attempt_update() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'PROCESSING' OR NEW.vendor_message_id IS NOT NULL
                   OR NEW.failure_code IS NOT NULL OR NEW.completed_at IS NOT NULL
                   OR NEW.reconciled_at IS NOT NULL OR NEW.reconciled_by IS NOT NULL
                   OR NEW.reconciliation_outcome IS NOT NULL
                   OR NEW.reconciliation_evidence IS NOT NULL THEN
                    RAISE EXCEPTION 'IM delivery attempt must begin processing'
                        USING ERRCODE = '23514';
                END IF;
                RETURN NEW;
            END IF;
            IF ROW(NEW.id, NEW.organization_id, NEW.delivery_id, NEW.ordinal,
                   NEW.claim_generation, NEW.channel, NEW.claimed_by,
                   NEW.lease_expires_at, NEW.policy_decision_id,
                   NEW.recipient_snapshot::jsonb, NEW.content_fingerprint,
                   NEW.idempotency_key, NEW.created_at)
               IS DISTINCT FROM
               ROW(OLD.id, OLD.organization_id, OLD.delivery_id, OLD.ordinal,
                   OLD.claim_generation, OLD.channel, OLD.claimed_by,
                   OLD.lease_expires_at, OLD.policy_decision_id,
                   OLD.recipient_snapshot::jsonb, OLD.content_fingerprint,
                   OLD.idempotency_key, OLD.created_at) THEN
                RAISE EXCEPTION 'IM delivery attempt claim is immutable' USING ERRCODE = '23514';
            END IF;
            IF OLD.status <> 'PROCESSING' AND NOT (
                OLD.status = 'UNKNOWN' AND NEW.status IN ('SENT', 'FAILED')
            ) THEN
                RAISE EXCEPTION 'IM delivery attempt outcome is immutable'
                    USING ERRCODE = '23514';
            END IF;
            IF OLD.status = 'UNKNOWN' AND NEW.status <> 'UNKNOWN' THEN
                IF NEW.reconciled_at IS NULL OR NEW.reconciled_by IS NULL
                   OR NEW.reconciliation_outcome NOT IN ('SENT', 'CONFIRMED_UNSENT')
                   OR NEW.reconciliation_evidence IS NULL THEN
                    RAISE EXCEPTION 'IM delivery attempt reconciliation evidence is required'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF OLD.status <> 'UNKNOWN'
               AND NEW.reconciled_at IS DISTINCT FROM OLD.reconciled_at THEN
                RAISE EXCEPTION 'IM delivery attempt reconciliation requires unknown state'
                    USING ERRCODE = '23514';
            END IF;
            IF NEW.status = 'SENT' THEN
                IF NEW.vendor_message_id IS NULL OR length(trim(NEW.vendor_message_id)) = 0
                   OR NEW.failure_code IS NOT NULL OR NEW.completed_at IS NULL THEN
                    RAISE EXCEPTION 'IM delivery attempt sent state is inconsistent'
                        USING ERRCODE = '23514';
                END IF;
            ELSIF NEW.status IN ('FAILED', 'UNKNOWN') THEN
                IF NEW.vendor_message_id IS NOT NULL OR NEW.failure_code IS NULL
                   OR NEW.completed_at IS NULL THEN
                    RAISE EXCEPTION 'IM delivery attempt terminal state is inconsistent'
                        USING ERRCODE = '23514';
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER im_delivery_attempt_update_guard
        BEFORE INSERT OR UPDATE ON im_delivery_attempts
        FOR EACH ROW EXECUTE FUNCTION obsion_guard_im_delivery_attempt_update();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER im_delivery_attempt_update_guard ON im_delivery_attempts")
    op.execute("DROP FUNCTION obsion_guard_im_delivery_attempt_update()")
    op.execute("DROP TRIGGER im_delivery_update_guard ON im_deliveries")
    op.execute("DROP FUNCTION obsion_guard_im_delivery_update()")
    op.drop_table("im_delivery_attempts")
    op.drop_constraint("uq_policy_decisions_org_id", "policy_decisions", type_="unique")
    op.drop_constraint(op.f("ck_im_deliveries_valid_status"), "im_deliveries", type_="check")
    op.create_check_constraint(
        op.f("ck_im_deliveries_valid_status"),
        "im_deliveries",
        "status IN ('PENDING', 'UNKNOWN', 'SENT', 'FAILED')",
    )
    op.drop_constraint(
        op.f("ck_im_deliveries_nonnegative_im_delivery_generation"),
        "im_deliveries",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_im_deliveries_nonnegative_im_delivery_sends"),
        "im_deliveries",
        type_="check",
    )
    op.drop_index(op.f("ix_im_deliveries_lease_expires_at"), table_name="im_deliveries")
    op.drop_constraint("fk_im_deliveries_org_reconciler", "im_deliveries", type_="foreignkey")
    op.drop_constraint("uq_im_deliveries_organization_id_id", "im_deliveries", type_="unique")
    op.drop_column("im_deliveries", "reconciliation_evidence")
    op.drop_column("im_deliveries", "reconciliation_outcome")
    op.drop_column("im_deliveries", "reconciled_by")
    op.drop_column("im_deliveries", "reconciled_at")
    op.drop_column("im_deliveries", "recipient_snapshot")
    op.drop_column("im_deliveries", "lease_expires_at")
    op.drop_column("im_deliveries", "lease_owner")
    op.drop_column("im_deliveries", "claim_generation")
    op.drop_column("im_deliveries", "send_attempt_count")
