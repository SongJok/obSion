import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, insert, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.sql.elements import ClauseElement

from obsion.common.time import utc_now
from obsion.config import get_settings
from obsion.db.models import (
    Claim,
    ClaimEvidence,
    ClaimVerificationResult,
    Evidence,
    EvidenceConflict,
    EvidenceObservation,
    Organization,
    PolicyDecision,
    Run,
    RunStep,
    Thread,
    Turn,
    User,
    VerificationAssessment,
    VerificationEvidenceLink,
    Workspace,
)


@dataclass(frozen=True)
class _OrganizationContext:
    organization_id: UUID
    user_id: UUID
    workspace_id: UUID


@dataclass(frozen=True)
class _RunContext:
    organization: _OrganizationContext
    run_id: UUID
    step_id: UUID
    policy_decision_id: UUID


@dataclass(frozen=True)
class _EvidenceContext:
    evidence_id: UUID
    observation_id: UUID


@dataclass(frozen=True)
class _VerificationContext:
    run: _RunContext
    first_evidence: _EvidenceContext
    second_evidence: _EvidenceContext
    claim_id: UUID
    assessment_id: UUID
    claim_result_id: UUID
    evidence_link_id: UUID
    conflict_id: UUID


async def _assert_rejected(
    connection: AsyncConnection,
    statement: ClauseElement,
    *,
    defer_constraints: bool = False,
) -> None:
    savepoint = await connection.begin_nested()
    with pytest.raises(DBAPIError):
        await connection.execute(statement)
        if defer_constraints:
            await connection.exec_driver_sql("SET CONSTRAINTS ALL IMMEDIATE")
    await savepoint.rollback()


async def _insert_organization(
    connection: AsyncConnection,
    *,
    label: str,
) -> _OrganizationContext:
    now = utc_now()
    organization_id = uuid4()
    user_id = uuid4()
    workspace_id = uuid4()
    await connection.execute(
        insert(Organization).values(
            id=organization_id,
            slug=f"verification-{label}-{organization_id}",
            name=f"Verification {label}",
            active=True,
            settings={},
            created_at=now,
            updated_at=now,
        )
    )
    await connection.execute(
        insert(User).values(
            id=user_id,
            organization_id=organization_id,
            external_id=f"verification-{label}-owner",
            email=f"{user_id}@example.invalid",
            display_name=f"Verification {label} owner",
            active=True,
            attributes={},
            created_at=now,
            updated_at=now,
        )
    )
    await connection.execute(
        insert(Workspace).values(
            id=workspace_id,
            organization_id=organization_id,
            name=f"Verification {label} workspace",
            description="",
            owner_id=user_id,
            classification="INTERNAL",
            visibility="PRIVATE",
            created_at=now,
            updated_at=now,
        )
    )
    return _OrganizationContext(
        organization_id=organization_id,
        user_id=user_id,
        workspace_id=workspace_id,
    )


async def _insert_run(
    connection: AsyncConnection,
    organization: _OrganizationContext,
    *,
    label: str,
) -> _RunContext:
    now = utc_now()
    thread_id = uuid4()
    turn_id = uuid4()
    run_id = uuid4()
    step_id = uuid4()
    policy_decision_id = uuid4()
    await connection.execute(
        insert(Thread).values(
            id=thread_id,
            organization_id=organization.organization_id,
            workspace_id=organization.workspace_id,
            title=f"Verification {label} thread",
            status="ACTIVE",
            created_by=organization.user_id,
            created_at=now,
            updated_at=now,
        )
    )
    await connection.execute(
        insert(Turn).values(
            id=turn_id,
            organization_id=organization.organization_id,
            thread_id=thread_id,
            ordinal=1,
            created_by=organization.user_id,
            input_text="Verify this answer",
            sanitized_input="Verify this answer",
            context_refs=[],
            attachment_refs=[],
            created_at=now,
        )
    )
    await connection.execute(
        insert(Run).values(
            id=run_id,
            organization_id=organization.organization_id,
            turn_id=turn_id,
            status="RUNNING",
            intent={},
            plan={},
            max_steps=30,
            timeout_seconds=300,
            max_input_tokens=120_000,
            max_output_tokens=16_000,
            max_cost_amount=Decimal("10"),
            step_count=1,
            input_tokens=0,
            output_tokens=0,
            cost_amount=Decimal("0"),
            aggregate_version=0,
            created_at=now,
            updated_at=now,
        )
    )
    await connection.execute(
        insert(RunStep).values(
            id=step_id,
            organization_id=organization.organization_id,
            run_id=run_id,
            ordinal=1,
            name="Independent evidence verification",
            kind="VERIFY",
            status="COMPLETED",
            depends_on=[],
            input_payload={},
            retry_count=0,
            max_retries=0,
            started_at=now,
            completed_at=now,
            created_at=now,
            updated_at=now,
        )
    )
    await connection.execute(
        insert(PolicyDecision).values(
            id=policy_decision_id,
            organization_id=organization.organization_id,
            run_id=run_id,
            principal_id=organization.user_id,
            action="answer.publish",
            resource={"run_id": str(run_id)},
            context={"environment": "production"},
            risk_level="L0",
            effect="ALLOW",
            matched_policy_ids=[],
            obligations=[],
            reason_codes=["verification_passed"],
            input_fingerprint="a" * 64,
            created_at=now,
        )
    )
    return _RunContext(
        organization=organization,
        run_id=run_id,
        step_id=step_id,
        policy_decision_id=policy_decision_id,
    )


def _evidence_values(run: _RunContext, *, evidence_id: UUID, label: str) -> dict[str, Any]:
    now = utc_now()
    return {
        "id": evidence_id,
        "organization_id": run.organization.organization_id,
        "run_id": run.run_id,
        "step_id": run.step_id,
        "evidence_type": "METRIC",
        "source": f"metrics-{label}",
        "resource": f"metric://latency/{label}",
        "observed_at": now,
        "ingested_at": now,
        "content": {"value": label},
        "content_fingerprint": label[0] * 64,
        "confidence": Decimal("0.9500"),
        "classification": "INTERNAL",
        "permissions": ["metrics.read"],
        "lineage": {"adapter": "verification-test"},
    }


def _observation_values(
    run: _RunContext,
    *,
    evidence_id: UUID,
    observation_id: UUID,
    ordinal: int,
    value: str,
) -> dict[str, Any]:
    now = utc_now()
    return {
        "id": observation_id,
        "organization_id": run.organization.organization_id,
        "run_id": run.run_id,
        "evidence_id": evidence_id,
        "ordinal": ordinal,
        "subject": "checkout-api",
        "measure": "p95_latency",
        "value_type": "NUMBER",
        "value": {"number": value},
        "unit": "ms",
        "environment": "production",
        "scope": {"region": "us-east-1"},
        "scope_fingerprint": "b" * 64,
        "valid_from": now,
        "valid_to": None,
        "definition_version": "metric:v3",
        "mapping_version": "mapping:v1",
        "mapping_fingerprint": "c" * 64,
        "observation_fingerprint": ("d" if ordinal == 1 else "e") * 64,
        "confidence": Decimal("0.9500"),
        "classification": "INTERNAL",
        "lineage": {"evidence_id": str(evidence_id)},
        "created_at": now,
    }


def _assessment_values(
    run: _RunContext,
    *,
    assessment_id: UUID,
    attempt: int,
    claim_generation: int = 1,
) -> dict[str, Any]:
    now = utc_now()
    return {
        "id": assessment_id,
        "organization_id": run.organization.organization_id,
        "run_id": run.run_id,
        "verify_step_id": run.step_id,
        "attempt": attempt,
        "claim_generation": claim_generation,
        "outcome": "VERIFIED",
        "publication_decision": "PUBLISH",
        "evaluator": "independent-evidence-critic",
        "evaluator_version": "1.0.0",
        "route": "DATA",
        "rules": ["metric_definition", "sql_validated", "result_cited"],
        "ruleset_snapshot": {"version": "data:v1"},
        "ruleset_fingerprint": "f" * 64,
        "input_fingerprint": "1" * 64,
        "policy_snapshot": {"effect": "ALLOW"},
        "policy_decision_id": run.policy_decision_id,
        "minimum_coverage": Decimal("0.8000"),
        "minimum_confidence": Decimal("0.8000"),
        "coverage": Decimal("1.0000"),
        "confidence": Decimal("0.9500"),
        "checks": {"mandatory_rules": True},
        "missing_requirements": [],
        "high_conflict_count": 0,
        "classification": "INTERNAL",
        "error_code": None,
        "duration_ms": 10,
        "replay_lineage": {},
        "completed_at": now,
        "created_at": now,
    }


def _claim_result_values(
    run: _RunContext,
    *,
    result_id: UUID,
    assessment_id: UUID,
    claim_id: UUID,
    claim_generation: int = 1,
) -> dict[str, Any]:
    return {
        "id": result_id,
        "organization_id": run.organization.organization_id,
        "run_id": run.run_id,
        "assessment_id": assessment_id,
        "claim_id": claim_id,
        "claim_generation": claim_generation,
        "ordinal": 1,
        "outcome": "VERIFIED",
        "coverage": Decimal("1.0000"),
        "confidence": Decimal("0.9500"),
        "checks": {"result_cited": "PASSED"},
        "reason_codes": [],
        "material": True,
        "classification": "INTERNAL",
        "created_at": utc_now(),
    }


async def _insert_verification_graph(
    connection: AsyncConnection,
    run: _RunContext,
) -> _VerificationContext:
    now = utc_now()
    first_evidence = _EvidenceContext(uuid4(), uuid4())
    second_evidence = _EvidenceContext(uuid4(), uuid4())
    claim_id = uuid4()
    assessment_id = uuid4()
    result_id = uuid4()
    link_id = uuid4()
    conflict_id = uuid4()

    await connection.execute(
        insert(Evidence),
        [
            _evidence_values(run, evidence_id=first_evidence.evidence_id, label="alpha"),
            _evidence_values(run, evidence_id=second_evidence.evidence_id, label="beta"),
        ],
    )
    await connection.execute(
        insert(EvidenceObservation),
        [
            _observation_values(
                run,
                evidence_id=first_evidence.evidence_id,
                observation_id=first_evidence.observation_id,
                ordinal=1,
                value="120",
            ),
            _observation_values(
                run,
                evidence_id=second_evidence.evidence_id,
                observation_id=second_evidence.observation_id,
                ordinal=1,
                value="140",
            ),
        ],
    )
    await connection.execute(
        insert(Claim).values(
            id=claim_id,
            organization_id=run.organization.organization_id,
            run_id=run.run_id,
            generation=1,
            ordinal=1,
            statement="Checkout p95 latency is elevated",
            confidence=Decimal("0.9500"),
            verification_status="VERIFIED",
            critic_notes={},
            created_at=now,
        )
    )
    await connection.execute(
        insert(ClaimEvidence),
        [
            {
                "organization_id": run.organization.organization_id,
                "run_id": run.run_id,
                "claim_id": claim_id,
                "evidence_id": first_evidence.evidence_id,
            },
            {
                "organization_id": run.organization.organization_id,
                "run_id": run.run_id,
                "claim_id": claim_id,
                "evidence_id": second_evidence.evidence_id,
            },
        ],
    )
    await connection.execute(
        insert(VerificationAssessment).values(
            **_assessment_values(
                run,
                assessment_id=assessment_id,
                attempt=1,
            )
        )
    )
    await connection.execute(
        insert(ClaimVerificationResult).values(
            **_claim_result_values(
                run,
                result_id=result_id,
                assessment_id=assessment_id,
                claim_id=claim_id,
            )
        )
    )
    await connection.execute(
        insert(VerificationEvidenceLink).values(
            id=link_id,
            organization_id=run.organization.organization_id,
            run_id=run.run_id,
            assessment_id=assessment_id,
            claim_result_id=result_id,
            evidence_id=first_evidence.evidence_id,
            observation_id=first_evidence.observation_id,
            rule="result_cited",
            rule_outcome="PASSED",
            relation="SUPPORTS",
            reason_codes=[],
            source_fingerprint="2" * 64,
            classification="INTERNAL",
            created_at=now,
        )
    )
    await connection.execute(
        insert(EvidenceConflict).values(
            id=conflict_id,
            organization_id=run.organization.organization_id,
            run_id=run.run_id,
            assessment_id=assessment_id,
            left_evidence_id=first_evidence.evidence_id,
            right_evidence_id=second_evidence.evidence_id,
            left_observation_id=first_evidence.observation_id,
            right_observation_id=second_evidence.observation_id,
            kind="VALUE",
            severity="LOW",
            disposition="EXPLAINED",
            subject="checkout-api",
            measure="p95_latency",
            unit="ms",
            environment="production",
            definition_version="metric:v3",
            scope_fingerprint="b" * 64,
            valid_from=now,
            valid_to=None,
            details={"reason_code": "sampling_window"},
            conflict_fingerprint="3" * 64,
            classification="INTERNAL",
            created_at=now,
        )
    )
    return _VerificationContext(
        run=run,
        first_evidence=first_evidence,
        second_evidence=second_evidence,
        claim_id=claim_id,
        assessment_id=assessment_id,
        claim_result_id=result_id,
        evidence_link_id=link_id,
        conflict_id=conflict_id,
    )


def _postgres_enabled() -> bool:
    return os.getenv("OBSION_RUN_POSTGRES_TESTS") == "1"


@pytest.mark.asyncio
async def test_verification_aggregates_are_append_only_and_admission_guarded() -> None:
    if not _postgres_enabled():
        pytest.skip("PostgreSQL invariant tests are opt-in")

    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            organization = await _insert_organization(connection, label="immutable")
            run = await _insert_run(connection, organization, label="immutable")
            graph = await _insert_verification_graph(connection, run)

            immutable_mutations = (
                update(EvidenceObservation)
                .where(EvidenceObservation.id == graph.first_evidence.observation_id)
                .values(confidence=Decimal("0.5000")),
                delete(EvidenceObservation).where(
                    EvidenceObservation.id == graph.first_evidence.observation_id
                ),
                update(VerificationAssessment)
                .where(VerificationAssessment.id == graph.assessment_id)
                .values(checks={}),
                delete(VerificationAssessment).where(
                    VerificationAssessment.id == graph.assessment_id
                ),
                update(ClaimVerificationResult)
                .where(ClaimVerificationResult.id == graph.claim_result_id)
                .values(checks={}),
                delete(ClaimVerificationResult).where(
                    ClaimVerificationResult.id == graph.claim_result_id
                ),
                update(VerificationEvidenceLink)
                .where(VerificationEvidenceLink.id == graph.evidence_link_id)
                .values(reason_codes=["rewritten"]),
                delete(VerificationEvidenceLink).where(
                    VerificationEvidenceLink.id == graph.evidence_link_id
                ),
                update(EvidenceConflict)
                .where(EvidenceConflict.id == graph.conflict_id)
                .values(details={}),
                delete(EvidenceConflict).where(EvidenceConflict.id == graph.conflict_id),
            )
            for statement in immutable_mutations:
                await _assert_rejected(connection, statement)

            invalid_admissions = (
                {"publication_decision": "WITHHOLD"},
                {"coverage": Decimal("0.7000")},
                {"confidence": Decimal("0.7000")},
                {"high_conflict_count": 1},
                {"error_code": "internal_error"},
                {"missing_requirements": ["result_cited"]},
                {"policy_decision_id": None},
            )
            next_attempt = 2
            for changes in invalid_admissions:
                values = _assessment_values(
                    run,
                    assessment_id=uuid4(),
                    attempt=next_attempt,
                )
                next_attempt += 1
                values.update(changes)
                await _assert_rejected(
                    connection,
                    insert(VerificationAssessment).values(**values),
                )

            partial_with_error = _assessment_values(
                run,
                assessment_id=uuid4(),
                attempt=next_attempt,
            )
            next_attempt += 1
            partial_with_error.update(
                outcome="PARTIAL",
                publication_decision="WITHHOLD",
                error_code="internal_error",
            )
            await _assert_rejected(
                connection,
                insert(VerificationAssessment).values(**partial_with_error),
            )

            partial_publish = _assessment_values(
                run,
                assessment_id=uuid4(),
                attempt=next_attempt,
            )
            next_attempt += 1
            partial_publish.update(outcome="PARTIAL", publication_decision="PUBLISH")
            await _assert_rejected(
                connection,
                insert(VerificationAssessment).values(**partial_publish),
            )

            error_without_code = _assessment_values(
                run,
                assessment_id=uuid4(),
                attempt=next_attempt,
            )
            next_attempt += 1
            error_without_code.update(
                outcome="ERROR",
                publication_decision="WITHHOLD",
                error_code=None,
            )
            await _assert_rejected(
                connection,
                insert(VerificationAssessment).values(**error_without_code),
            )

            empty_assessment = _assessment_values(
                run,
                assessment_id=uuid4(),
                attempt=next_attempt,
            )
            next_attempt += 1
            await _assert_rejected(
                connection,
                insert(VerificationAssessment).values(**empty_assessment),
                defer_constraints=True,
            )

            partial_result_assessment = _assessment_values(
                run,
                assessment_id=uuid4(),
                attempt=next_attempt,
            )
            next_attempt += 1
            await connection.execute(
                insert(VerificationAssessment).values(**partial_result_assessment)
            )
            partial_result = _claim_result_values(
                run,
                result_id=uuid4(),
                assessment_id=partial_result_assessment["id"],
                claim_id=graph.claim_id,
            )
            partial_result["outcome"] = "PARTIAL"
            await connection.execute(insert(ClaimVerificationResult).values(**partial_result))
            await _assert_rejected(
                connection,
                insert(VerificationEvidenceLink).values(
                    id=uuid4(),
                    organization_id=organization.organization_id,
                    run_id=run.run_id,
                    assessment_id=partial_result_assessment["id"],
                    claim_result_id=partial_result["id"],
                    evidence_id=graph.first_evidence.evidence_id,
                    observation_id=graph.first_evidence.observation_id,
                    rule="result_cited",
                    rule_outcome="PASSED",
                    relation="SUPPORTS",
                    reason_codes=[],
                    source_fingerprint="8" * 64,
                    classification="INTERNAL",
                    created_at=utc_now(),
                ),
                defer_constraints=True,
            )

            severe_conflict_assessment = _assessment_values(
                run,
                assessment_id=uuid4(),
                attempt=next_attempt,
            )
            next_attempt += 1
            severe_result = _claim_result_values(
                run,
                result_id=uuid4(),
                assessment_id=severe_conflict_assessment["id"],
                claim_id=graph.claim_id,
            )
            await connection.execute(
                insert(VerificationAssessment).values(**severe_conflict_assessment)
            )
            await connection.execute(insert(ClaimVerificationResult).values(**severe_result))
            await _assert_rejected(
                connection,
                insert(EvidenceConflict).values(
                    id=uuid4(),
                    organization_id=organization.organization_id,
                    run_id=run.run_id,
                    assessment_id=severe_conflict_assessment["id"],
                    left_evidence_id=graph.first_evidence.evidence_id,
                    right_evidence_id=graph.second_evidence.evidence_id,
                    left_observation_id=graph.first_evidence.observation_id,
                    right_observation_id=graph.second_evidence.observation_id,
                    kind="VALUE",
                    severity="HIGH",
                    disposition="UNRESOLVED",
                    subject="checkout-api",
                    measure="p95_latency",
                    unit="ms",
                    environment="production",
                    definition_version="metric:v3",
                    scope_fingerprint="b" * 64,
                    valid_from=utc_now(),
                    valid_to=None,
                    details={},
                    conflict_fingerprint="9" * 64,
                    classification="INTERNAL",
                    created_at=utc_now(),
                ),
                defer_constraints=True,
            )
        finally:
            await transaction.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_verification_ranges_fingerprints_and_ordinals_are_database_guarded() -> None:
    if not _postgres_enabled():
        pytest.skip("PostgreSQL invariant tests are opt-in")

    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            organization = await _insert_organization(connection, label="checks")
            run = await _insert_run(connection, organization, label="checks")
            graph = await _insert_verification_graph(connection, run)

            bad_evidence = _evidence_values(run, evidence_id=uuid4(), label="gamma")
            bad_evidence["content_fingerprint"] = "short"
            await _assert_rejected(connection, insert(Evidence).values(**bad_evidence))

            observation_base = _observation_values(
                run,
                evidence_id=graph.first_evidence.evidence_id,
                observation_id=uuid4(),
                ordinal=2,
                value="121",
            )
            for changes in (
                {"value_type": "INSTRUCTION"},
                {"ordinal": 0},
                {"confidence": Decimal("1.1000")},
                {"scope_fingerprint": "short"},
                {"mapping_fingerprint": "short"},
                {"observation_fingerprint": "short"},
                {"subject": ""},
                {"valid_to": utc_now().replace(year=2000)},
            ):
                values = dict(observation_base)
                values.update(changes)
                await _assert_rejected(
                    connection,
                    insert(EvidenceObservation).values(**values),
                )

            for changes in (
                {"outcome": "UNTRUSTED"},
                {"publication_decision": "BYPASS"},
                {"attempt": 0},
                {"claim_generation": 0},
                {"minimum_coverage": Decimal("-0.1000")},
                {"minimum_confidence": Decimal("1.1000")},
                {"coverage": Decimal("1.1000")},
                {"confidence": Decimal("-0.1000")},
                {"ruleset_fingerprint": "short"},
                {"input_fingerprint": "short"},
                {"high_conflict_count": -1},
                {"duration_ms": -1},
            ):
                values = _assessment_values(run, assessment_id=uuid4(), attempt=2)
                values.update(changes)
                await _assert_rejected(
                    connection,
                    insert(VerificationAssessment).values(**values),
                )

            for changes in (
                {"ordinal": 0},
                {"coverage": Decimal("1.1000")},
                {"confidence": Decimal("-0.1000")},
            ):
                values = _claim_result_values(
                    run,
                    result_id=uuid4(),
                    assessment_id=graph.assessment_id,
                    claim_id=graph.claim_id,
                )
                values.update(changes)
                await _assert_rejected(
                    connection,
                    insert(ClaimVerificationResult).values(**values),
                )

            invalid_link_base = {
                "id": uuid4(),
                "organization_id": organization.organization_id,
                "run_id": run.run_id,
                "assessment_id": graph.assessment_id,
                "claim_result_id": graph.claim_result_id,
                "evidence_id": graph.second_evidence.evidence_id,
                "observation_id": graph.second_evidence.observation_id,
                "rule": "metric_definition",
                "rule_outcome": "PASSED",
                "relation": "SUPPORTS",
                "reason_codes": [],
                "source_fingerprint": "5" * 64,
                "classification": "INTERNAL",
                "created_at": utc_now(),
            }
            for changes in (
                {"rule": " "},
                {"source_fingerprint": "short"},
                {"rule_outcome": "BYPASS"},
                {"relation": "INSTRUCTS"},
            ):
                values = dict(invalid_link_base)
                values.update(changes)
                await _assert_rejected(
                    connection,
                    insert(VerificationEvidenceLink).values(**values),
                )

            conflict_base = {
                "id": uuid4(),
                "organization_id": organization.organization_id,
                "run_id": run.run_id,
                "assessment_id": graph.assessment_id,
                "left_evidence_id": graph.first_evidence.evidence_id,
                "right_evidence_id": graph.second_evidence.evidence_id,
                "left_observation_id": graph.first_evidence.observation_id,
                "right_observation_id": graph.second_evidence.observation_id,
                "kind": "VALUE",
                "severity": "LOW",
                "disposition": "EXPLAINED",
                "subject": "checkout-api",
                "measure": "p95_latency",
                "unit": "ms",
                "environment": "production",
                "definition_version": "metric:v3",
                "scope_fingerprint": "b" * 64,
                "valid_from": utc_now(),
                "valid_to": None,
                "details": {},
                "conflict_fingerprint": "4" * 64,
                "classification": "INTERNAL",
                "created_at": utc_now(),
            }
            for changes in (
                {"kind": "INSTRUCTION"},
                {"severity": "BLOCKER"},
                {"disposition": "IGNORED"},
                {"right_evidence_id": graph.first_evidence.evidence_id},
                {"right_observation_id": graph.first_evidence.observation_id},
                {"scope_fingerprint": "short"},
                {"conflict_fingerprint": "short"},
                {"subject": ""},
                {"valid_to": utc_now().replace(year=2000)},
            ):
                values = dict(conflict_base)
                values.update(changes)
                await _assert_rejected(
                    connection,
                    insert(EvidenceConflict).values(**values),
                )

            duplicate_claim_id = uuid4()
            await _assert_rejected(
                connection,
                insert(Claim).values(
                    id=duplicate_claim_id,
                    organization_id=organization.organization_id,
                    run_id=run.run_id,
                    generation=1,
                    ordinal=1,
                    statement="Duplicate ordinal",
                    confidence=Decimal("0.9000"),
                    verification_status="PENDING",
                    critic_notes={},
                    created_at=utc_now(),
                ),
            )
            await _assert_rejected(
                connection,
                insert(VerificationAssessment).values(
                    **_assessment_values(run, assessment_id=uuid4(), attempt=1)
                ),
            )
        finally:
            await transaction.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_verification_references_cannot_cross_tenants_runs_or_aggregates() -> None:
    if not _postgres_enabled():
        pytest.skip("PostgreSQL invariant tests are opt-in")

    engine = create_async_engine(get_settings().database_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        try:
            first_organization = await _insert_organization(connection, label="first")
            second_organization = await _insert_organization(connection, label="second")
            first_run = await _insert_run(connection, first_organization, label="first")
            sibling_run = await _insert_run(connection, first_organization, label="sibling")
            foreign_run = await _insert_run(connection, second_organization, label="foreign")
            first_graph = await _insert_verification_graph(connection, first_run)
            sibling_graph = await _insert_verification_graph(connection, sibling_run)
            foreign_graph = await _insert_verification_graph(connection, foreign_run)

            for evidence_id in (
                sibling_graph.first_evidence.evidence_id,
                foreign_graph.first_evidence.evidence_id,
            ):
                await _assert_rejected(
                    connection,
                    insert(ClaimEvidence).values(
                        organization_id=first_organization.organization_id,
                        run_id=first_run.run_id,
                        claim_id=first_graph.claim_id,
                        evidence_id=evidence_id,
                    ),
                )

            wrong_generation = _claim_result_values(
                first_run,
                result_id=uuid4(),
                assessment_id=first_graph.assessment_id,
                claim_id=first_graph.claim_id,
                claim_generation=2,
            )
            await _assert_rejected(
                connection,
                insert(ClaimVerificationResult).values(**wrong_generation),
            )

            mismatched_assessment_generation = _assessment_values(
                first_run,
                assessment_id=uuid4(),
                attempt=2,
                claim_generation=2,
            )
            await connection.execute(
                insert(VerificationAssessment).values(**mismatched_assessment_generation)
            )
            await _assert_rejected(
                connection,
                insert(ClaimVerificationResult).values(
                    **_claim_result_values(
                        first_run,
                        result_id=uuid4(),
                        assessment_id=mismatched_assessment_generation["id"],
                        claim_id=first_graph.claim_id,
                        claim_generation=1,
                    )
                ),
                defer_constraints=True,
            )

            await _assert_rejected(
                connection,
                insert(VerificationEvidenceLink).values(
                    id=uuid4(),
                    organization_id=first_organization.organization_id,
                    run_id=first_run.run_id,
                    assessment_id=first_graph.assessment_id,
                    claim_result_id=first_graph.claim_result_id,
                    evidence_id=first_graph.first_evidence.evidence_id,
                    observation_id=first_graph.second_evidence.observation_id,
                    rule="metric_definition",
                    rule_outcome="PASSED",
                    relation="SUPPORTS",
                    reason_codes=[],
                    source_fingerprint="5" * 64,
                    classification="INTERNAL",
                    created_at=utc_now(),
                ),
            )

            second_assessment_id = uuid4()
            await connection.execute(
                insert(VerificationAssessment).values(
                    **_assessment_values(
                        first_run,
                        assessment_id=second_assessment_id,
                        attempt=3,
                    )
                )
            )
            await _assert_rejected(
                connection,
                insert(VerificationEvidenceLink).values(
                    id=uuid4(),
                    organization_id=first_organization.organization_id,
                    run_id=first_run.run_id,
                    assessment_id=second_assessment_id,
                    claim_result_id=first_graph.claim_result_id,
                    evidence_id=first_graph.second_evidence.evidence_id,
                    observation_id=first_graph.second_evidence.observation_id,
                    rule="metric_definition",
                    rule_outcome="PASSED",
                    relation="SUPPORTS",
                    reason_codes=[],
                    source_fingerprint="6" * 64,
                    classification="INTERNAL",
                    created_at=utc_now(),
                ),
                defer_constraints=True,
            )

            conflict_values = {
                "id": uuid4(),
                "organization_id": first_organization.organization_id,
                "run_id": first_run.run_id,
                "assessment_id": first_graph.assessment_id,
                "left_evidence_id": first_graph.first_evidence.evidence_id,
                "right_evidence_id": first_graph.second_evidence.evidence_id,
                "left_observation_id": first_graph.second_evidence.observation_id,
                "right_observation_id": first_graph.first_evidence.observation_id,
                "kind": "VALUE",
                "severity": "HIGH",
                "disposition": "UNRESOLVED",
                "subject": "checkout-api",
                "measure": "p95_latency",
                "unit": "ms",
                "environment": "production",
                "definition_version": "metric:v3",
                "scope_fingerprint": "b" * 64,
                "valid_from": utc_now(),
                "valid_to": None,
                "details": {},
                "conflict_fingerprint": "7" * 64,
                "classification": "INTERNAL",
                "created_at": utc_now(),
            }
            await _assert_rejected(
                connection,
                insert(EvidenceConflict).values(**conflict_values),
            )

            cross_run_policy = _assessment_values(
                first_run,
                assessment_id=uuid4(),
                attempt=4,
            )
            cross_run_policy["policy_decision_id"] = sibling_run.policy_decision_id
            await _assert_rejected(
                connection,
                insert(VerificationAssessment).values(**cross_run_policy),
                defer_constraints=True,
            )
        finally:
            await transaction.rollback()
    await engine.dispose()
