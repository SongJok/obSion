"""fix verification admission trigger field resolution

Revision ID: b88f1c4d5e60
Revises: a79c4d2e8f10
Create Date: 2026-08-31 16:00:00.000000

The deferred verification-admission trigger resolved the candidate assessment
through a single SQL ``CASE`` expression referencing ``NEW.assessment_id``.
plpgsql translates that expression into one SQL query whose field references
are validated against the trigger row type at execution, so every INSERT into
``verification_assessments`` (a table without ``assessment_id``) failed at
COMMIT with ``record "new" has no field "assessment_id"``.  Splitting the
branches into separate plpgsql statements keeps each field reference behind
control flow, so only the branch that actually runs is ever planned.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b88f1c4d5e60"
down_revision: str | None = "a79c4d2e8f10"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # CREATE OR REPLACE preserves the three existing constraint triggers; only
    # the candidate-id resolution changes from one CASE expression to branch
    # statements whose field references are planned lazily per execution.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION obsion_validate_verification_assessment()
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
            IF TG_TABLE_NAME = 'verification_assessments' THEN
                candidate_id := NEW.id;
            ELSE
                candidate_id := NEW.assessment_id;
            END IF;

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


def downgrade() -> None:
    # Restore the original single-CASE resolution.  The downgrade keeps the
    # historical function body byte-identical to e6f9a0123bcd for audit
    # symmetry; production rollback of this revision is unnecessary because
    # the replaced body is strictly more permissive at plan time.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION obsion_validate_verification_assessment()
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
