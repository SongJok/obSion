"""add app server protocol state

Revision ID: d5e8f9012abc
Revises: c4d7e8f901ab
Create Date: 2026-08-26 12:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5e8f9012abc"
down_revision: str | None = "c4d7e8f901ab"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("events", sa.Column("run_sequence", sa.Integer(), nullable=True))
    # The backfill is the only intentional mutation of the append-only Event table.
    # Transactional DDL keeps the immutable trigger absent only inside this migration.
    op.execute("DROP TRIGGER trg_events_immutable ON events")
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY run_id ORDER BY created_at, id
                   ) AS run_sequence
            FROM events
            WHERE run_id IS NOT NULL
        )
        UPDATE events AS event
        SET run_sequence = ranked.run_sequence
        FROM ranked
        WHERE event.id = ranked.id
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_events_immutable
        BEFORE UPDATE OR DELETE ON events
        FOR EACH ROW EXECUTE FUNCTION obsion_reject_immutable_mutation()
        """
    )
    op.execute(
        """
        UPDATE runs AS run
        SET aggregate_version = COALESCE(stream.maximum_sequence, 0)
        FROM (
            SELECT run_id, max(run_sequence) AS maximum_sequence
            FROM events
            WHERE run_id IS NOT NULL
            GROUP BY run_id
        ) AS stream
        WHERE run.id = stream.run_id
        """
    )
    op.drop_index("ix_events_run_sequence", table_name="events")
    op.create_index(
        "ix_events_run_sequence",
        "events",
        ["run_id", "run_sequence"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_events_run_sequence",
        "events",
        ["run_id", "run_sequence"],
    )
    op.create_check_constraint(
        op.f("ck_events_event_run_sequence_consistent"),
        "events",
        "(run_id IS NULL AND run_sequence IS NULL) OR "
        "(run_id IS NOT NULL AND run_sequence IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_events_positive_event_run_sequence"),
        "events",
        "run_sequence IS NULL OR run_sequence > 0",
    )

    op.create_table(
        "app_server_requests",
        sa.Column("principal_id", sa.Uuid(), nullable=False),
        sa.Column("client_request_id", sa.String(length=200), nullable=False),
        sa.Column("method", sa.String(length=120), nullable=False),
        sa.Column("params_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("response", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "(response IS NULL AND completed_at IS NULL) OR "
            "(response IS NOT NULL AND completed_at IS NOT NULL)",
            name=op.f("ck_app_server_requests_app_server_request_completion_consistent"),
        ),
        sa.CheckConstraint(
            "length(params_fingerprint) = 64",
            name=op.f("ck_app_server_requests_app_server_params_fingerprint_length"),
        ),
        sa.CheckConstraint(
            "length(trim(client_request_id)) > 0",
            name=op.f("ck_app_server_requests_nonempty_app_server_client_request_id"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_app_server_requests_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["principal_id"],
            ["users.id"],
            name=op.f("fk_app_server_requests_principal_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_app_server_requests")),
        sa.UniqueConstraint(
            "organization_id",
            "principal_id",
            "client_request_id",
            name="uq_app_server_request_principal_key",
        ),
    )
    op.create_index(
        op.f("ix_app_server_requests_organization_id"),
        "app_server_requests",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_app_server_requests_principal_id"),
        "app_server_requests",
        ["principal_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_app_server_requests_expires_at"),
        "app_server_requests",
        ["expires_at"],
        unique=False,
    )
    op.create_index(
        "ix_app_server_requests_lookup",
        "app_server_requests",
        ["organization_id", "principal_id", "client_request_id"],
        unique=False,
    )
    op.execute(
        """
        CREATE FUNCTION obsion_guard_app_server_request_mutation()
        RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                IF OLD.expires_at > CURRENT_TIMESTAMP THEN
                    RAISE EXCEPTION 'app server request retention has not expired';
                END IF;
                RETURN OLD;
            END IF;

            IF OLD.id IS DISTINCT FROM NEW.id
               OR OLD.organization_id IS DISTINCT FROM NEW.organization_id
               OR OLD.principal_id IS DISTINCT FROM NEW.principal_id
               OR OLD.client_request_id IS DISTINCT FROM NEW.client_request_id
               OR OLD.method IS DISTINCT FROM NEW.method
               OR OLD.params_fingerprint IS DISTINCT FROM NEW.params_fingerprint
               OR OLD.created_at IS DISTINCT FROM NEW.created_at
               OR OLD.expires_at IS DISTINCT FROM NEW.expires_at
               OR OLD.response IS NOT NULL
               OR OLD.completed_at IS NOT NULL
               OR NEW.response IS NULL
               OR NEW.completed_at IS NULL THEN
                RAISE EXCEPTION 'app server request identity or outcome is immutable';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_app_server_requests_guard
        BEFORE UPDATE OR DELETE ON app_server_requests
        FOR EACH ROW EXECUTE FUNCTION obsion_guard_app_server_request_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_app_server_requests_guard ON app_server_requests")
    op.execute("DROP FUNCTION IF EXISTS obsion_guard_app_server_request_mutation()")
    op.drop_index("ix_app_server_requests_lookup", table_name="app_server_requests")
    op.drop_index(
        op.f("ix_app_server_requests_expires_at"),
        table_name="app_server_requests",
    )
    op.drop_index(
        op.f("ix_app_server_requests_principal_id"),
        table_name="app_server_requests",
    )
    op.drop_index(
        op.f("ix_app_server_requests_organization_id"),
        table_name="app_server_requests",
    )
    op.drop_table("app_server_requests")

    op.drop_constraint(
        op.f("ck_events_positive_event_run_sequence"),
        "events",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_events_event_run_sequence_consistent"),
        "events",
        type_="check",
    )
    op.drop_constraint("uq_events_run_sequence", "events", type_="unique")
    op.drop_index("ix_events_run_sequence", table_name="events")
    op.create_index("ix_events_run_sequence", "events", ["run_id", "sequence"], unique=False)
    op.drop_column("events", "run_sequence")
