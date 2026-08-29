"""add evidence verification aggregates

Revision ID: e6f9a0123bcd
Revises: d5e8f9012abc
Create Date: 2026-08-26 18:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e6f9a0123bcd"
down_revision: str | None = "d5e8f9012abc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IMMUTABLE_TABLES = (
    "evidence_observations",
    "verification_assessments",
    "claim_verification_results",
    "verification_evidence_links",
    "evidence_conflicts",
)


def _enum(*values: str, name: str) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, length=32)


def _sha256_hex_check(column: str) -> str:
    remainder = column
    for character in "0123456789abcdef":
        remainder = f"replace({remainder}, '{character}', '')"
    return f"length({column}) = 64 AND length({remainder}) = 0"


def upgrade() -> None:
    # Composite keys make organization and Run ownership part of every durable
    # verification reference rather than relying on application-side filters.
    op.create_unique_constraint(
        "uq_runs_organization_id_id",
        "runs",
        ["organization_id", "id"],
    )
    op.create_unique_constraint(
        "uq_run_steps_organization_run_id",
        "run_steps",
        ["organization_id", "run_id", "id"],
    )
    op.create_foreign_key(
        "fk_run_steps_organization_run",
        "run_steps",
        "runs",
        ["organization_id", "run_id"],
        ["organization_id", "id"],
        ondelete="CASCADE",
    )

    op.create_unique_constraint(
        "uq_evidence_organization_run_id",
        "evidence",
        ["organization_id", "run_id", "id"],
    )
    op.create_foreign_key(
        "fk_evidence_organization_run",
        "evidence",
        "runs",
        ["organization_id", "run_id"],
        ["organization_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_evidence_organization_run_step",
        "evidence",
        "run_steps",
        ["organization_id", "run_id", "step_id"],
        ["organization_id", "run_id", "id"],
    )
    op.create_check_constraint(
        op.f("ck_evidence_content_fingerprint_sha256"),
        "evidence",
        _sha256_hex_check("content_fingerprint"),
    )

    op.create_unique_constraint(
        "uq_policy_decisions_organization_run_id",
        "policy_decisions",
        ["organization_id", "run_id", "id"],
    )
    op.create_foreign_key(
        "fk_policy_decisions_organization_run",
        "policy_decisions",
        "runs",
        ["organization_id", "run_id"],
        ["organization_id", "id"],
    )

    # Claim records are already immutable. A generation therefore preserves
    # every rejected attempt while allowing a bounded replan to synthesize a
    # new set without rewriting history.
    op.add_column(
        "claims",
        sa.Column("generation", sa.Integer(), nullable=True, server_default="1"),
    )
    op.execute("UPDATE claims SET generation = 1 WHERE generation IS NULL")
    op.alter_column("claims", "generation", nullable=False, server_default=None)
    op.drop_constraint("uq_claims_run_id", "claims", type_="unique")
    op.create_unique_constraint(
        "uq_claims_run_id",
        "claims",
        ["run_id", "generation", "ordinal"],
    )
    op.create_unique_constraint(
        "uq_claims_organization_run_id",
        "claims",
        ["organization_id", "run_id", "id"],
    )
    op.create_unique_constraint(
        "uq_claims_organization_run_generation_id",
        "claims",
        ["organization_id", "run_id", "generation", "id"],
    )
    op.create_foreign_key(
        "fk_claims_organization_run",
        "claims",
        "runs",
        ["organization_id", "run_id"],
        ["organization_id", "id"],
        ondelete="CASCADE",
    )
    op.create_check_constraint(
        op.f("ck_claims_positive_claim_generation"),
        "claims",
        "generation > 0",
    )
    op.create_check_constraint(
        op.f("ck_claims_positive_claim_ordinal"),
        "claims",
        "ordinal > 0",
    )
    op.create_check_constraint(
        op.f("ck_claims_nonempty_claim_statement"),
        "claims",
        "length(trim(statement)) > 0",
    )

    # Existing links are backfilled from immutable Claim and Evidence rows, then
    # constrained so cross-tenant and cross-Run evidence links fail in PostgreSQL.
    op.add_column("claim_evidence", sa.Column("organization_id", sa.Uuid(), nullable=True))
    op.add_column("claim_evidence", sa.Column("run_id", sa.Uuid(), nullable=True))
    # The backfill is the only intentional mutation of this immutable link table.
    # Transactional DDL restores the guard before the migration commits.
    op.execute("DROP TRIGGER trg_claim_evidence_immutable ON claim_evidence")
    op.execute(
        """
        UPDATE claim_evidence AS link
        SET organization_id = claim.organization_id,
            run_id = claim.run_id
        FROM claims AS claim
        WHERE claim.id = link.claim_id
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_claim_evidence_immutable
        BEFORE UPDATE OR DELETE ON claim_evidence
        FOR EACH ROW EXECUTE FUNCTION obsion_reject_immutable_mutation()
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM claim_evidence AS link
                JOIN evidence AS item ON item.id = link.evidence_id
                WHERE item.organization_id <> link.organization_id
                   OR item.run_id <> link.run_id
            ) THEN
                RAISE EXCEPTION 'existing Claim-Evidence link crosses organization or Run';
            END IF;
        END;
        $$
        """
    )
    op.alter_column("claim_evidence", "organization_id", nullable=False)
    op.alter_column("claim_evidence", "run_id", nullable=False)
    op.create_index(
        op.f("ix_claim_evidence_organization_id"),
        "claim_evidence",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_claim_evidence_run_id"),
        "claim_evidence",
        ["run_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_claim_evidence_organization_id_organizations",
        "claim_evidence",
        "organizations",
        ["organization_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_claim_evidence_organization_run",
        "claim_evidence",
        "runs",
        ["organization_id", "run_id"],
        ["organization_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_claim_evidence_claim",
        "claim_evidence",
        "claims",
        ["organization_id", "run_id", "claim_id"],
        ["organization_id", "run_id", "id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_claim_evidence_evidence",
        "claim_evidence",
        "evidence",
        ["organization_id", "run_id", "evidence_id"],
        ["organization_id", "run_id", "id"],
        ondelete="CASCADE",
    )
    op.drop_constraint(
        "fk_claim_evidence_claim_id_claims",
        "claim_evidence",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_claim_evidence_evidence_id_evidence",
        "claim_evidence",
        type_="foreignkey",
    )

    op.create_table(
        "evidence_observations",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("measure", sa.String(length=300), nullable=False),
        sa.Column(
            "value_type",
            _enum("TEXT", "NUMBER", "BOOLEAN", "DATETIME", "JSON", name="observationvaluetype"),
            nullable=False,
        ),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.Column("unit", sa.String(length=120), nullable=False),
        sa.Column("environment", sa.String(length=120), nullable=False),
        sa.Column("scope", sa.JSON(), nullable=False),
        sa.Column("scope_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("definition_version", sa.String(length=200), nullable=False),
        sa.Column("mapping_version", sa.String(length=160), nullable=False),
        sa.Column("mapping_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("observation_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column(
            "classification",
            sa.Enum(
                "PUBLIC",
                "INTERNAL",
                "CONFIDENTIAL",
                "RESTRICTED",
                name="classification",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("lineage", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "value_type IN ('TEXT', 'NUMBER', 'BOOLEAN', 'DATETIME', 'JSON')",
            name=op.f("ck_evidence_observations_valid_value_type"),
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name=op.f("ck_evidence_observations_valid_confidence"),
        ),
        sa.CheckConstraint(
            _sha256_hex_check("mapping_fingerprint"),
            name=op.f("ck_evidence_observations_mapping_fingerprint_sha256"),
        ),
        sa.CheckConstraint(
            _sha256_hex_check("observation_fingerprint"),
            name=op.f("ck_evidence_observations_observation_fingerprint_sha256"),
        ),
        sa.CheckConstraint(
            _sha256_hex_check("scope_fingerprint"),
            name=op.f("ck_evidence_observations_scope_fingerprint_sha256"),
        ),
        sa.CheckConstraint(
            "length(trim(subject)) > 0 AND length(trim(measure)) > 0",
            name=op.f("ck_evidence_observations_nonempty_key"),
        ),
        sa.CheckConstraint(
            "ordinal > 0",
            name=op.f("ck_evidence_observations_positive_ordinal"),
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name=op.f("ck_evidence_observations_valid_interval"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id", "evidence_id"],
            ["evidence.organization_id", "evidence.run_id", "evidence.id"],
            name="fk_evidence_observations_evidence",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name="fk_evidence_observations_organization_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_evidence_observations_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_observations")),
        sa.UniqueConstraint(
            "evidence_id",
            "ordinal",
            name="uq_evidence_observations_evidence_ordinal",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "run_id",
            "evidence_id",
            "id",
            name="uq_evidence_observations_evidence_id",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "run_id",
            "id",
            name="uq_evidence_observations_organization_run_id",
        ),
    )
    op.create_index(
        op.f("ix_evidence_observations_organization_id"),
        "evidence_observations",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_evidence_observations_run_id"),
        "evidence_observations",
        ["run_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_evidence_observations_evidence_id"),
        "evidence_observations",
        ["evidence_id"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_observations_comparable",
        "evidence_observations",
        [
            "organization_id",
            "run_id",
            "subject",
            "measure",
            "unit",
            "environment",
            "definition_version",
        ],
        unique=False,
    )

    op.create_table(
        "verification_assessments",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("verify_step_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("claim_generation", sa.Integer(), nullable=False),
        sa.Column(
            "outcome",
            _enum("VERIFIED", "PARTIAL", "REJECTED", "ERROR", name="verificationoutcome"),
            nullable=False,
        ),
        sa.Column(
            "publication_decision",
            _enum(
                "PUBLISH",
                "PUBLISH_MASKED",
                "AWAIT_APPROVAL",
                "WITHHOLD",
                name="answerpublicationdecision",
            ),
            nullable=False,
        ),
        sa.Column("evaluator", sa.String(length=160), nullable=False),
        sa.Column("evaluator_version", sa.String(length=80), nullable=False),
        sa.Column("route", sa.String(length=80), nullable=False),
        sa.Column("rules", sa.JSON(), nullable=False),
        sa.Column("ruleset_snapshot", sa.JSON(), nullable=False),
        sa.Column("ruleset_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("policy_snapshot", sa.JSON(), nullable=False),
        sa.Column("policy_decision_id", sa.Uuid(), nullable=True),
        sa.Column("minimum_coverage", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("minimum_confidence", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("coverage", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("missing_requirements", sa.JSON(), nullable=False),
        sa.Column("high_conflict_count", sa.Integer(), nullable=False),
        sa.Column(
            "classification",
            sa.Enum(
                "PUBLIC",
                "INTERNAL",
                "CONFIDENTIAL",
                "RESTRICTED",
                name="classification",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("replay_lineage", sa.JSON(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('VERIFIED', 'PARTIAL', 'REJECTED', 'ERROR')",
            name=op.f("ck_verification_assessments_valid_outcome"),
        ),
        sa.CheckConstraint(
            "publication_decision IN ('PUBLISH', 'PUBLISH_MASKED', 'AWAIT_APPROVAL', 'WITHHOLD')",
            name=op.f("ck_verification_assessments_valid_publication_decision"),
        ),
        sa.CheckConstraint(
            "claim_generation > 0",
            name=op.f("ck_verification_assessments_positive_claim_generation"),
        ),
        sa.CheckConstraint(
            "attempt > 0",
            name=op.f("ck_verification_assessments_positive_attempt"),
        ),
        sa.CheckConstraint(
            "coverage >= 0 AND coverage <= 1",
            name=op.f("ck_verification_assessments_coverage_range"),
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name=op.f("ck_verification_assessments_confidence_range"),
        ),
        sa.CheckConstraint(
            "minimum_coverage >= 0 AND minimum_coverage <= 1",
            name=op.f("ck_verification_assessments_minimum_coverage_range"),
        ),
        sa.CheckConstraint(
            "minimum_confidence >= 0 AND minimum_confidence <= 1",
            name=op.f("ck_verification_assessments_minimum_confidence_range"),
        ),
        sa.CheckConstraint(
            _sha256_hex_check("input_fingerprint"),
            name=op.f("ck_verification_assessments_input_fingerprint_sha256"),
        ),
        sa.CheckConstraint(
            _sha256_hex_check("ruleset_fingerprint"),
            name=op.f("ck_verification_assessments_ruleset_fingerprint_sha256"),
        ),
        sa.CheckConstraint(
            "high_conflict_count >= 0",
            name=op.f("ck_verification_assessments_nonnegative_conflicts"),
        ),
        sa.CheckConstraint(
            "duration_ms >= 0",
            name=op.f("ck_verification_assessments_nonnegative_duration"),
        ),
        sa.CheckConstraint(
            "outcome <> 'VERIFIED' OR ("
            "publication_decision IN ('PUBLISH', 'PUBLISH_MASKED') AND "
            "coverage >= minimum_coverage AND confidence >= minimum_confidence AND "
            "high_conflict_count = 0 AND error_code IS NULL AND "
            "json_array_length(missing_requirements) = 0)",
            name=op.f("ck_verification_assessments_verified_assessment_admissible"),
        ),
        sa.CheckConstraint(
            "publication_decision NOT IN ('PUBLISH', 'PUBLISH_MASKED') OR "
            "(outcome = 'VERIFIED' AND policy_decision_id IS NOT NULL)",
            name=op.f("ck_verification_assessments_publication_requires_verified"),
        ),
        sa.CheckConstraint(
            "(outcome = 'ERROR' AND error_code IS NOT NULL) OR "
            "(outcome <> 'ERROR' AND error_code IS NULL)",
            name=op.f("ck_verification_assessments_verification_error_consistent"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name="fk_verification_assessments_organization_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id", "verify_step_id"],
            ["run_steps.organization_id", "run_steps.run_id", "run_steps.id"],
            name="fk_verification_assessments_verify_step",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_verification_assessments_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id", "policy_decision_id"],
            [
                "policy_decisions.organization_id",
                "policy_decisions.run_id",
                "policy_decisions.id",
            ],
            name="fk_verification_assessments_policy_decision",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_assessments")),
        sa.UniqueConstraint(
            "organization_id",
            "run_id",
            "id",
            name="uq_verification_assessments_organization_run_id",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "run_id",
            "claim_generation",
            "id",
            name="uq_verification_assessments_run_generation_id",
        ),
        sa.UniqueConstraint(
            "run_id",
            "attempt",
            name="uq_verification_assessments_run_attempt",
        ),
    )
    for column in ("organization_id", "run_id", "verify_step_id", "outcome"):
        op.create_index(
            op.f(f"ix_verification_assessments_{column}"),
            "verification_assessments",
            [column],
            unique=False,
        )

    op.create_table(
        "claim_verification_results",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("assessment_id", sa.Uuid(), nullable=False),
        sa.Column("claim_id", sa.Uuid(), nullable=False),
        sa.Column("claim_generation", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "outcome",
            _enum("VERIFIED", "PARTIAL", "REJECTED", "ERROR", name="verificationoutcome"),
            nullable=False,
        ),
        sa.Column("coverage", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("confidence", sa.Numeric(precision=5, scale=4), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("material", sa.Boolean(), nullable=False),
        sa.Column(
            "classification",
            sa.Enum(
                "PUBLIC",
                "INTERNAL",
                "CONFIDENTIAL",
                "RESTRICTED",
                name="classification",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "outcome IN ('VERIFIED', 'PARTIAL', 'REJECTED', 'ERROR')",
            name=op.f("ck_claim_verification_results_valid_outcome"),
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name=op.f("ck_claim_verification_results_confidence_range"),
        ),
        sa.CheckConstraint(
            "coverage >= 0 AND coverage <= 1",
            name=op.f("ck_claim_verification_results_coverage_range"),
        ),
        sa.CheckConstraint(
            "ordinal > 0",
            name=op.f("ck_claim_verification_results_positive_ordinal"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id", "claim_generation", "assessment_id"],
            [
                "verification_assessments.organization_id",
                "verification_assessments.run_id",
                "verification_assessments.claim_generation",
                "verification_assessments.id",
            ],
            name="fk_claim_verification_results_assessment",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id", "claim_generation", "claim_id"],
            ["claims.organization_id", "claims.run_id", "claims.generation", "claims.id"],
            name="fk_claim_verification_results_claim",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id"],
            ["runs.organization_id", "runs.id"],
            name="fk_claim_verification_results_organization_run",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_claim_verification_results_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_claim_verification_results")),
        sa.UniqueConstraint(
            "organization_id",
            "run_id",
            "id",
            name="uq_claim_verification_results_organization_run_id",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "run_id",
            "assessment_id",
            "id",
            name="uq_claim_verification_results_assessment_id",
        ),
        sa.UniqueConstraint(
            "assessment_id",
            "claim_id",
            name="uq_claim_verification_results_assessment_claim",
        ),
        sa.UniqueConstraint(
            "assessment_id",
            "ordinal",
            name="uq_claim_verification_results_assessment_ordinal",
        ),
    )
    for column in ("organization_id", "run_id", "assessment_id", "claim_id"):
        op.create_index(
            op.f(f"ix_claim_verification_results_{column}"),
            "claim_verification_results",
            [column],
            unique=False,
        )

    op.create_table(
        "verification_evidence_links",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("assessment_id", sa.Uuid(), nullable=False),
        sa.Column("claim_result_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.Uuid(), nullable=False),
        sa.Column("observation_id", sa.Uuid(), nullable=True),
        sa.Column("rule", sa.String(length=160), nullable=False),
        sa.Column(
            "rule_outcome",
            _enum(
                "PASSED",
                "FAILED",
                "INDETERMINATE",
                "NOT_APPLICABLE",
                name="verificationruleoutcome",
            ),
            nullable=False,
        ),
        sa.Column(
            "relation",
            _enum("SUPPORTS", "CONTRADICTS", "NEUTRAL", name="evidencerelation"),
            nullable=False,
        ),
        sa.Column("reason_codes", sa.JSON(), nullable=False),
        sa.Column("source_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "classification",
            sa.Enum(
                "PUBLIC",
                "INTERNAL",
                "CONFIDENTIAL",
                "RESTRICTED",
                name="classification",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "rule_outcome IN ('PASSED', 'FAILED', 'INDETERMINATE', 'NOT_APPLICABLE')",
            name=op.f("ck_verification_evidence_links_valid_rule_outcome"),
        ),
        sa.CheckConstraint(
            "relation IN ('SUPPORTS', 'CONTRADICTS', 'NEUTRAL')",
            name=op.f("ck_verification_evidence_links_valid_relation"),
        ),
        sa.CheckConstraint(
            "length(trim(rule)) > 0",
            name=op.f("ck_verification_evidence_links_nonempty_rule"),
        ),
        sa.CheckConstraint(
            _sha256_hex_check("source_fingerprint"),
            name=op.f("ck_verification_evidence_links_source_fingerprint_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id", "assessment_id"],
            [
                "verification_assessments.organization_id",
                "verification_assessments.run_id",
                "verification_assessments.id",
            ],
            name="fk_verification_evidence_links_assessment",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id", "assessment_id", "claim_result_id"],
            [
                "claim_verification_results.organization_id",
                "claim_verification_results.run_id",
                "claim_verification_results.assessment_id",
                "claim_verification_results.id",
            ],
            name="fk_verification_evidence_links_claim_result",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id", "evidence_id"],
            ["evidence.organization_id", "evidence.run_id", "evidence.id"],
            name="fk_verification_evidence_links_evidence",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id", "evidence_id", "observation_id"],
            [
                "evidence_observations.organization_id",
                "evidence_observations.run_id",
                "evidence_observations.evidence_id",
                "evidence_observations.id",
            ],
            name="fk_verification_evidence_links_observation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_verification_evidence_links_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_verification_evidence_links")),
        sa.UniqueConstraint(
            "claim_result_id",
            "evidence_id",
            "rule",
            name="uq_verification_evidence_links_result_evidence_rule",
        ),
    )
    for column in (
        "organization_id",
        "run_id",
        "assessment_id",
        "claim_result_id",
        "evidence_id",
        "observation_id",
    ):
        op.create_index(
            op.f(f"ix_verification_evidence_links_{column}"),
            "verification_evidence_links",
            [column],
            unique=False,
        )

    op.create_table(
        "evidence_conflicts",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("assessment_id", sa.Uuid(), nullable=False),
        sa.Column("left_evidence_id", sa.Uuid(), nullable=False),
        sa.Column("right_evidence_id", sa.Uuid(), nullable=False),
        sa.Column("left_observation_id", sa.Uuid(), nullable=True),
        sa.Column("right_observation_id", sa.Uuid(), nullable=True),
        sa.Column(
            "kind",
            _enum("VALUE", "TEMPORAL", "DEFINITION", "SCOPE", name="evidenceconflictkind"),
            nullable=False,
        ),
        sa.Column(
            "severity",
            _enum("LOW", "MEDIUM", "HIGH", "CRITICAL", name="evidenceconflictseverity"),
            nullable=False,
        ),
        sa.Column(
            "disposition",
            _enum("UNRESOLVED", "EXPLAINED", "DUPLICATE", name="evidenceconflictdisposition"),
            nullable=False,
        ),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("measure", sa.String(length=300), nullable=False),
        sa.Column("unit", sa.String(length=120), nullable=False),
        sa.Column("environment", sa.String(length=120), nullable=False),
        sa.Column("definition_version", sa.String(length=200), nullable=False),
        sa.Column("scope_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("conflict_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "classification",
            sa.Enum(
                "PUBLIC",
                "INTERNAL",
                "CONFIDENTIAL",
                "RESTRICTED",
                name="classification",
                native_enum=False,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('VALUE', 'TEMPORAL', 'DEFINITION', 'SCOPE')",
            name=op.f("ck_evidence_conflicts_valid_kind"),
        ),
        sa.CheckConstraint(
            "severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name=op.f("ck_evidence_conflicts_valid_severity"),
        ),
        sa.CheckConstraint(
            "disposition IN ('UNRESOLVED', 'EXPLAINED', 'DUPLICATE')",
            name=op.f("ck_evidence_conflicts_valid_disposition"),
        ),
        sa.CheckConstraint(
            "left_evidence_id <> right_evidence_id",
            name=op.f("ck_evidence_conflicts_distinct_evidence"),
        ),
        sa.CheckConstraint(
            "left_observation_id IS NULL OR right_observation_id IS NULL OR "
            "left_observation_id <> right_observation_id",
            name=op.f("ck_evidence_conflicts_distinct_observations"),
        ),
        sa.CheckConstraint(
            _sha256_hex_check("conflict_fingerprint"),
            name=op.f("ck_evidence_conflicts_conflict_fingerprint_sha256"),
        ),
        sa.CheckConstraint(
            _sha256_hex_check("scope_fingerprint"),
            name=op.f("ck_evidence_conflicts_scope_fingerprint_sha256"),
        ),
        sa.CheckConstraint(
            "length(trim(subject)) > 0 AND length(trim(measure)) > 0",
            name=op.f("ck_evidence_conflicts_nonempty_key"),
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from",
            name=op.f("ck_evidence_conflicts_valid_interval"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id", "assessment_id"],
            [
                "verification_assessments.organization_id",
                "verification_assessments.run_id",
                "verification_assessments.id",
            ],
            name="fk_evidence_conflicts_assessment",
            ondelete="CASCADE",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id", "left_evidence_id"],
            ["evidence.organization_id", "evidence.run_id", "evidence.id"],
            name="fk_evidence_conflicts_left_evidence",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id", "right_evidence_id"],
            ["evidence.organization_id", "evidence.run_id", "evidence.id"],
            name="fk_evidence_conflicts_right_evidence",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id", "left_evidence_id", "left_observation_id"],
            [
                "evidence_observations.organization_id",
                "evidence_observations.run_id",
                "evidence_observations.evidence_id",
                "evidence_observations.id",
            ],
            name="fk_evidence_conflicts_left_observation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "run_id", "right_evidence_id", "right_observation_id"],
            [
                "evidence_observations.organization_id",
                "evidence_observations.run_id",
                "evidence_observations.evidence_id",
                "evidence_observations.id",
            ],
            name="fk_evidence_conflicts_right_observation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            name=op.f("fk_evidence_conflicts_organization_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_evidence_conflicts")),
        sa.UniqueConstraint(
            "assessment_id",
            "conflict_fingerprint",
            name="uq_evidence_conflicts_assessment_fingerprint",
        ),
    )
    for column in (
        "organization_id",
        "run_id",
        "assessment_id",
        "left_evidence_id",
        "right_evidence_id",
        "severity",
    ):
        op.create_index(
            op.f(f"ix_evidence_conflicts_{column}"),
            "evidence_conflicts",
            [column],
            unique=False,
        )

    for table in _IMMUTABLE_TABLES:
        op.execute(
            f"CREATE TRIGGER trg_{table}_immutable "  # noqa: S608 -- fixed table allowlist
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION obsion_reject_immutable_mutation()"
        )

    # The candidate assessment is an immutable snapshot. Deferred validation sees
    # every child row in the transaction before admitting publication. Run row
    # locking serializes Claim generation sealing against concurrent appenders.
    op.execute(
        """
        CREATE FUNCTION obsion_validate_verification_assessment()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            candidate_id uuid;
            candidate_run_id uuid;
            candidate_generation integer;
            candidate_outcome varchar(32);
            candidate_conflicts integer;
        BEGIN
            candidate_id := CASE
                WHEN TG_TABLE_NAME = 'verification_assessments' THEN NEW.id
                ELSE NEW.assessment_id
            END;

            SELECT run_id, claim_generation, outcome
            INTO candidate_run_id, candidate_generation, candidate_outcome
            FROM verification_assessments
            WHERE id = candidate_id;

            IF NOT FOUND THEN
                RETURN NULL;
            END IF;

            PERFORM 1 FROM runs WHERE id = candidate_run_id FOR UPDATE;

            SELECT count(*)
            INTO candidate_conflicts
            FROM evidence_conflicts
            WHERE assessment_id = candidate_id
              AND severity IN ('HIGH', 'CRITICAL')
              AND disposition = 'UNRESOLVED';

            IF candidate_conflicts <> (
                SELECT high_conflict_count
                FROM verification_assessments
                WHERE id = candidate_id
            ) THEN
                RAISE EXCEPTION
                    'assessment % severe conflict count is inconsistent',
                    candidate_id
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;

            IF candidate_outcome = 'VERIFIED' THEN
                IF NOT EXISTS (
                    SELECT 1
                    FROM claims
                    WHERE run_id = candidate_run_id
                      AND generation = candidate_generation
                ) THEN
                    RAISE EXCEPTION
                        'VERIFIED assessment % has no Claims',
                        candidate_id
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM claims AS claim
                    LEFT JOIN claim_verification_results AS result
                      ON result.assessment_id = candidate_id
                     AND result.claim_id = claim.id
                    WHERE claim.run_id = candidate_run_id
                      AND claim.generation = candidate_generation
                      AND result.id IS NULL
                ) THEN
                    RAISE EXCEPTION
                        'VERIFIED assessment % does not cover every Claim',
                        candidate_id
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM claim_verification_results AS result
                    JOIN claims AS claim ON claim.id = result.claim_id
                    WHERE result.assessment_id = candidate_id
                      AND (
                          result.claim_generation <> candidate_generation
                          OR result.ordinal <> claim.ordinal
                          OR result.outcome <> 'VERIFIED'
                      )
                ) THEN
                    RAISE EXCEPTION
                        'VERIFIED assessment % has an inconsistent Claim result',
                        candidate_id
                        USING ERRCODE = 'integrity_constraint_violation';
                END IF;
            END IF;

            RETURN NULL;
        END;
        $$
        """
    )
    for table in (
        "verification_assessments",
        "claim_verification_results",
        "evidence_conflicts",
    ):
        op.execute(
            f"CREATE CONSTRAINT TRIGGER "  # noqa: S608 -- fixed table allowlist
            f"trg_{table}_verification_admission "
            f"AFTER INSERT ON {table} "
            "DEFERRABLE INITIALLY DEFERRED "
            "FOR EACH ROW EXECUTE FUNCTION obsion_validate_verification_assessment()"
        )

    op.execute(
        """
        CREATE FUNCTION obsion_guard_claim_generation_append()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            PERFORM 1 FROM runs WHERE id = NEW.run_id FOR UPDATE;
            IF EXISTS (
                SELECT 1
                FROM verification_assessments
                WHERE organization_id = NEW.organization_id
                  AND run_id = NEW.run_id
                  AND claim_generation = NEW.generation
                  AND outcome = 'VERIFIED'
            ) THEN
                RAISE EXCEPTION
                    'Claim generation is sealed by a VERIFIED assessment: Run %, generation %',
                    NEW.run_id,
                    NEW.generation
                    USING ERRCODE = 'integrity_constraint_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_claims_verified_generation_seal
        BEFORE INSERT ON claims
        FOR EACH ROW EXECUTE FUNCTION obsion_guard_claim_generation_append()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_claims_verified_generation_seal ON claims")
    op.execute("DROP FUNCTION IF EXISTS obsion_guard_claim_generation_append()")
    for table in (
        "evidence_conflicts",
        "claim_verification_results",
        "verification_assessments",
    ):
        op.execute(
            f"DROP TRIGGER IF EXISTS "  # noqa: S608 -- fixed table allowlist
            f"trg_{table}_verification_admission ON {table}"
        )
    op.execute("DROP FUNCTION IF EXISTS obsion_validate_verification_assessment()")

    for table in reversed(_IMMUTABLE_TABLES):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}"  # noqa: S608
        )

    for table in (
        "evidence_conflicts",
        "verification_evidence_links",
        "claim_verification_results",
        "verification_assessments",
        "evidence_observations",
    ):
        op.drop_table(table)

    op.create_foreign_key(
        "fk_claim_evidence_evidence_id_evidence",
        "claim_evidence",
        "evidence",
        ["evidence_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_claim_evidence_claim_id_claims",
        "claim_evidence",
        "claims",
        ["claim_id"],
        ["id"],
        ondelete="CASCADE",
    )
    for constraint in (
        "fk_claim_evidence_evidence",
        "fk_claim_evidence_claim",
        "fk_claim_evidence_organization_run",
        "fk_claim_evidence_organization_id_organizations",
    ):
        op.drop_constraint(constraint, "claim_evidence", type_="foreignkey")
    op.drop_index(op.f("ix_claim_evidence_run_id"), table_name="claim_evidence")
    op.drop_index(op.f("ix_claim_evidence_organization_id"), table_name="claim_evidence")
    op.drop_column("claim_evidence", "run_id")
    op.drop_column("claim_evidence", "organization_id")

    op.drop_constraint(
        op.f("ck_claims_nonempty_claim_statement"),
        "claims",
        type_="check",
    )
    op.drop_constraint(op.f("ck_claims_positive_claim_ordinal"), "claims", type_="check")
    op.drop_constraint(op.f("ck_claims_positive_claim_generation"), "claims", type_="check")
    op.drop_constraint("fk_claims_organization_run", "claims", type_="foreignkey")
    op.drop_constraint(
        "uq_claims_organization_run_generation_id",
        "claims",
        type_="unique",
    )
    op.drop_constraint("uq_claims_organization_run_id", "claims", type_="unique")
    op.drop_constraint("uq_claims_run_id", "claims", type_="unique")
    op.create_unique_constraint("uq_claims_run_id", "claims", ["run_id", "ordinal"])
    op.drop_column("claims", "generation")

    op.drop_constraint(
        op.f("ck_evidence_content_fingerprint_sha256"),
        "evidence",
        type_="check",
    )
    op.execute(
        "ALTER TABLE policy_decisions DROP CONSTRAINT IF EXISTS "
        "fk_policy_decisions_organization_run"
    )
    op.execute(
        "ALTER TABLE policy_decisions DROP CONSTRAINT IF EXISTS "
        "uq_policy_decisions_organization_run_id"
    )
    op.drop_constraint(
        "fk_evidence_organization_run_step",
        "evidence",
        type_="foreignkey",
    )
    op.drop_constraint("fk_evidence_organization_run", "evidence", type_="foreignkey")
    op.drop_constraint("uq_evidence_organization_run_id", "evidence", type_="unique")

    op.drop_constraint(
        "fk_run_steps_organization_run",
        "run_steps",
        type_="foreignkey",
    )
    op.drop_constraint(
        "uq_run_steps_organization_run_id",
        "run_steps",
        type_="unique",
    )
    op.drop_constraint("uq_runs_organization_id_id", "runs", type_="unique")
