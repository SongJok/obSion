import asyncio
import hashlib
import time
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from obsion.common.time import utc_now
from obsion.config import Environment, Settings
from obsion.db.base import Base
from obsion.db.models import (
    AgentDefinition,
    AgentVersion,
    Approval,
    Artifact,
    Claim,
    ClaimEvidence,
    Event,
    Evidence,
    Organization,
    PolicyDecision,
    Run,
    RunStep,
    Thread,
    Turn,
    User,
    VerificationAssessment,
    Workspace,
)
from obsion.db.session import Database
from obsion.domain.enums import (
    ApprovalStatus,
    Classification,
    DecisionEffect,
    EvidenceType,
    RegistryStatus,
    RiskLevel,
    RunStatus,
    StepKind,
    StepStatus,
)
from obsion.harness.critic import Critic
from obsion.harness.incident import IncidentEvidenceFusion
from obsion.harness.replay import RunReplayService
from obsion.harness.runtime import HarnessRuntime
from obsion.persistence.audit import AuditWriter
from obsion.persistence.events import EventStore


async def _seed_capability_approval(
    settings: Settings,
    *,
    workspace_id: UUID,
    label: str,
) -> tuple[UUID, UUID]:
    database = Database(settings)
    now = utc_now()
    try:
        async with database.sessions() as session, session.begin():
            agent_version_id = await session.scalar(
                select(AgentVersion.id)
                .join(AgentDefinition, AgentDefinition.id == AgentVersion.agent_id)
                .where(
                    AgentDefinition.organization_id == settings.dev_organization_id,
                    AgentDefinition.name == "general-agent",
                )
                .order_by(AgentVersion.version.desc())
                .limit(1)
            )
            assert agent_version_id is not None
            thread = Thread(
                organization_id=settings.dev_organization_id,
                workspace_id=workspace_id,
                title=f"Phase 20 approval {label}",
                created_by=settings.dev_user_id,
            )
            session.add(thread)
            await session.flush()
            turn = Turn(
                organization_id=settings.dev_organization_id,
                thread_id=thread.id,
                ordinal=1,
                created_by=settings.dev_user_id,
                input_text="你好",
                sanitized_input="你好",
                context_refs=[],
                attachment_refs=[],
                created_at=now,
            )
            session.add(turn)
            await session.flush()
            run = Run(
                organization_id=settings.dev_organization_id,
                turn_id=turn.id,
                status=RunStatus.WAITING_APPROVAL,
                agent_version_id=agent_version_id,
                intent={},
                plan={},
                started_at=now,
            )
            session.add(run)
            await session.flush()
            decision = PolicyDecision(
                organization_id=settings.dev_organization_id,
                run_id=run.id,
                principal_id=settings.dev_user_id,
                action="knowledge.read",
                resource={"workspace_id": str(workspace_id)},
                context={"environment": "development"},
                risk_level=RiskLevel.L1,
                effect=DecisionEffect.ASK,
                matched_policy_ids=[],
                obligations=[],
                reason_codes=["phase20_approval_regression"],
                input_fingerprint=hashlib.sha256(label.encode()).hexdigest(),
                created_at=now,
            )
            session.add(decision)
            await session.flush()
            approval = Approval(
                organization_id=settings.dev_organization_id,
                run_id=run.id,
                step_id=None,
                policy_decision_id=decision.id,
                status=ApprovalStatus.PENDING,
                reason=f"Review {label}",
                requested_by=settings.dev_user_id,
                approver_constraints={"permission": "approval.decide"},
                expires_at=now + timedelta(hours=1),
                resume_token_hash=hashlib.sha256(uuid4().bytes).hexdigest(),
            )
            session.add(approval)
            await session.flush()
            return approval.id, run.id
    finally:
        await database.dispose()


async def _assessment_snapshot(settings: Settings, run_id: UUID) -> dict:
    database = Database(settings)
    try:
        async with database.sessions() as session:
            assessment = await session.scalar(
                select(VerificationAssessment).where(
                    VerificationAssessment.organization_id == settings.dev_organization_id,
                    VerificationAssessment.run_id == run_id,
                )
            )
            assert assessment is not None
            return {
                "id": str(assessment.id),
                "outcome": str(assessment.outcome),
                "publication_decision": str(assessment.publication_decision),
                "ruleset_fingerprint": assessment.ruleset_fingerprint,
                "input_fingerprint": assessment.input_fingerprint,
                "replay_lineage": assessment.replay_lineage,
            }
    finally:
        await database.dispose()


def _workspace(client: TestClient) -> dict:
    response = client.post(
        "/api/v1/workspaces",
        json={"name": "Phase 20 approvals", "description": "Capability approval gate"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _wait_terminal(client: TestClient, run_id: UUID) -> dict:
    run: dict = {}
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return run
        time.sleep(0.05)
    raise AssertionError(f"Run did not reach a terminal state: {run}")


def test_capability_approval_api_approves_rejects_emits_events_and_audits(
    client: TestClient,
    app_settings: Settings,
) -> None:
    workspace = _workspace(client)
    rejected_id, rejected_run_id = asyncio.run(
        _seed_capability_approval(
            app_settings,
            workspace_id=UUID(workspace["id"]),
            label="reject",
        )
    )
    approved_id, approved_run_id = asyncio.run(
        _seed_capability_approval(
            app_settings,
            workspace_id=UUID(workspace["id"]),
            label="approve",
        )
    )

    pending = client.get("/api/v1/approvals", params={"status": "PENDING"})
    assert pending.status_code == 200, pending.text
    assert {item["id"] for item in pending.json()} >= {str(rejected_id), str(approved_id)}

    rejected = client.post(
        f"/api/v1/approvals/{rejected_id}/reject",
        json={"reason": "Evidence scope is insufficient"},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["status"] == "REJECTED"
    rejected_run = client.get(f"/api/v1/runs/{rejected_run_id}").json()
    assert rejected_run["status"] == "FAILED"
    assert rejected_run["error_code"] == "approval_rejected"

    approved = client.post(
        f"/api/v1/approvals/{approved_id}/approve",
        json={"reason": "Evidence scope and policy constraints reviewed"},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "APPROVED"
    approved_run = _wait_terminal(client, approved_run_id)
    assert approved_run["status"] == "COMPLETED", (
        approved_run["error_code"],
        approved_run["error_message"],
        approved_run["plan"],
    )

    source_assessment = asyncio.run(_assessment_snapshot(app_settings, approved_run_id))
    replay = client.post(f"/api/v1/runs/{approved_run_id}/replay")
    assert replay.status_code == 202, replay.text
    replay_run_id = UUID(replay.json()["id"])
    assert _wait_terminal(client, replay_run_id)["status"] == "COMPLETED"
    replay_assessment = asyncio.run(_assessment_snapshot(app_settings, replay_run_id))
    assert replay_assessment["id"] != source_assessment["id"]
    assert replay_assessment["ruleset_fingerprint"] == source_assessment["ruleset_fingerprint"]
    assert replay_assessment["input_fingerprint"] == source_assessment["input_fingerprint"]
    assert replay_assessment["outcome"] == source_assessment["outcome"]
    assert replay_assessment["publication_decision"] == source_assessment["publication_decision"]
    assert (
        replay_assessment["replay_lineage"]["replay_source_assessment_id"]
        == (source_assessment["id"])
    )

    rejected_events = client.get(f"/api/v1/runs/{rejected_run_id}/events").json()
    approved_events = client.get(f"/api/v1/runs/{approved_run_id}/events").json()
    assert "approval.rejected" in {item["name"] for item in rejected_events}
    assert "approval.approved" in {item["name"] for item in approved_events}

    audit = client.get("/api/v1/admin/audit?limit=100").json()
    approval_audits = {
        item["approval_id"]: item["outcome"]
        for item in audit
        if item["action"] == "approval.decide"
    }
    assert approval_audits[str(rejected_id)] == "REJECTED"
    assert approval_audits[str(approved_id)] == "APPROVED"


def test_admin_browser_projections_hide_gateway_configuration_and_secret_references(
    client: TestClient,
) -> None:
    connector = client.post(
        "/api/v1/admin/connectors",
        json={
            "name": "phase20-browser-safe-connector",
            "connector_type": "observability-http",
            "environment": "development",
            "endpoint": "http://observability.internal/query",
            "configuration": {"protocol": "observability.v1"},
            "credential_ref": "env://OBSION_PHASE20_CONNECTOR_TOKEN",
            "declared_grants": ["metrics.read"],
            "allowed_egress": ["observability.internal:80"],
            "status": "DRAFT",
        },
    )
    assert connector.status_code == 201, connector.text
    connector_view = next(
        item
        for item in client.get("/api/v1/admin/connectors").json()
        if item["name"] == "phase20-browser-safe-connector"
    )
    assert connector_view["has_credential"] is True
    assert "credential_ref" not in connector_view
    assert "endpoint" not in connector_view
    assert "configuration" not in connector_view

    endpoint = client.post(
        "/api/v1/admin/models/endpoints",
        json={
            "name": "phase20-browser-safe-model",
            "provider": "openai-compatible",
            "base_url": "http://localhost:9999/v1",
            "model_id": "governed-model-id",
            "credential_ref": "env://OBSION_PHASE20_MODEL_TOKEN",
            "classifications": ["PUBLIC", "INTERNAL"],
            "capabilities": ["chat"],
            "limits": {"context_window": 8192, "max_output_tokens": 1024},
            "enabled": False,
        },
    )
    assert endpoint.status_code == 201, endpoint.text
    endpoint_view = next(
        item
        for item in client.get("/api/v1/admin/models/endpoints").json()
        if item["name"] == "phase20-browser-safe-model"
    )
    assert endpoint_view["has_credential"] is True
    assert "credential_ref" not in endpoint_view
    assert "base_url" not in endpoint_view


@pytest.mark.asyncio
async def test_incident_golden_response_persists_verified_claims_and_replays_graph(
    tmp_path: Path,
) -> None:
    settings = Settings(
        environment=Environment.TEST,
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'phase20-incident.db'}",
    )
    database = Database(settings)
    organization_id = uuid4()
    now = utc_now()
    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with database.sessions() as session, session.begin():
            organization = Organization(
                id=organization_id,
                slug="phase20-incident-golden",
                name="Phase 20 Incident Golden",
                active=True,
                settings={},
            )
            user = User(
                organization_id=organization_id,
                external_id="phase20-incident-owner",
                email="phase20-incident@example.invalid",
                display_name="Phase 20 Incident Owner",
                active=True,
                attributes={},
            )
            session.add_all([organization, user])
            await session.flush()
            workspace = Workspace(
                organization_id=organization_id,
                name="Incident Golden",
                owner_id=user.id,
            )
            agent = AgentDefinition(
                organization_id=organization_id,
                name="incident-agent",
                display_name="Incident Agent",
                description="Read-only incident investigator",
                status=RegistryStatus.ACTIVE,
            )
            session.add_all([workspace, agent])
            await session.flush()
            agent_version = AgentVersion(
                organization_id=organization_id,
                agent_id=agent.id,
                version=1,
                spec={
                    "name": "incident-agent",
                    "modelProfile": "reasoning-high",
                    "capabilities": [],
                    "skills": ["incident-investigation"],
                    "budgets": {"risk": "L2"},
                },
                checksum_sha256="a" * 64,
                created_by=user.id,
                created_at=now,
            )
            session.add(agent_version)
            await session.flush()
            thread = Thread(
                organization_id=organization_id,
                workspace_id=workspace.id,
                title="Payments incident",
                created_by=user.id,
            )
            session.add(thread)
            await session.flush()
            turn = Turn(
                organization_id=organization_id,
                thread_id=thread.id,
                ordinal=1,
                created_by=user.id,
                input_text="支付服务延迟异常的根因是什么？",
                sanitized_input="支付服务延迟异常的根因是什么？",
                context_refs=[],
                attachment_refs=[],
                created_at=now,
            )
            session.add(turn)
            await session.flush()
            run = Run(
                organization_id=organization_id,
                turn_id=turn.id,
                status=RunStatus.RUNNING,
                agent_version_id=agent_version.id,
                intent={
                    "route": "INCIDENT",
                    "time_range": {
                        "start": (now - timedelta(minutes=30)).isoformat(),
                        "end": (now + timedelta(minutes=30)).isoformat(),
                    },
                },
                plan={
                    "route": "INCIDENT",
                    "steps": [],
                    "required_evidence": ["METRIC", "DEPLOYMENT", "LOG"],
                    "verification": ["cross_type_claims", "temporal_consistency"],
                },
                started_at=now,
                step_count=1,
            )
            session.add(run)
            await session.flush()
            verify_step = RunStep(
                organization_id=organization_id,
                run_id=run.id,
                ordinal=1,
                name="Independent incident verification",
                kind=StepKind.VERIFY,
                status=StepStatus.PENDING,
                depends_on=[],
                input_payload={"required_evidence": ["METRIC", "DEPLOYMENT", "LOG"]},
            )
            respond_step = RunStep(
                organization_id=organization_id,
                run_id=run.id,
                ordinal=2,
                name="Publish incident response",
                kind=StepKind.RESPOND,
                status=StepStatus.PENDING,
                depends_on=[1],
                input_payload={},
            )
            session.add_all([verify_step, respond_step])
            evidence_rows = [
                Evidence(
                    organization_id=organization_id,
                    run_id=run.id,
                    evidence_type=EvidenceType.METRIC,
                    source="metrics-golden",
                    resource="metric://payments/p99",
                    observed_at=now,
                    ingested_at=now,
                    content={
                        "events": [
                            {
                                "timestamp": now.isoformat(),
                                "service": "payments",
                                "environment": "production",
                                "deployment_id": "deploy-42",
                                "metric": "p99_latency",
                                "severity": "critical",
                            }
                        ],
                        "count": 1,
                    },
                    content_fingerprint="1" * 64,
                    confidence=Decimal("1"),
                    classification=Classification.INTERNAL,
                    permissions=["metrics.read"],
                    lineage={"environment": "production"},
                ),
                Evidence(
                    organization_id=organization_id,
                    run_id=run.id,
                    evidence_type=EvidenceType.DEPLOYMENT,
                    source="deployments-golden",
                    resource="deployment://payments/deploy-42",
                    observed_at=now + timedelta(minutes=1),
                    ingested_at=now + timedelta(minutes=1),
                    content={
                        "items": [
                            {
                                "timestamp": (now + timedelta(minutes=1)).isoformat(),
                                "service": "payments",
                                "environment": "production",
                                "deployment_id": "deploy-42",
                                "commit_id": "abc1234",
                            }
                        ],
                        "count": 1,
                    },
                    content_fingerprint="2" * 64,
                    confidence=Decimal("1"),
                    classification=Classification.INTERNAL,
                    permissions=["deployment.read"],
                    lineage={"environment": "production"},
                ),
                Evidence(
                    organization_id=organization_id,
                    run_id=run.id,
                    evidence_type=EvidenceType.LOG,
                    source="logs-golden",
                    resource="log://payments/timeouts",
                    observed_at=now + timedelta(minutes=2),
                    ingested_at=now + timedelta(minutes=2),
                    content={
                        "events": [
                            {
                                "timestamp": (now + timedelta(minutes=2)).isoformat(),
                                "service": "payments",
                                "environment": "production",
                                "deployment_id": "deploy-42",
                                "error_type": "TimeoutError",
                                "severity": "error",
                            }
                        ],
                        "count": 1,
                    },
                    content_fingerprint="3" * 64,
                    confidence=Decimal("1"),
                    classification=Classification.INTERNAL,
                    permissions=["logs.read"],
                    lineage={"environment": "production"},
                ),
            ]
            session.add_all(evidence_rows)
            policy_decision = PolicyDecision(
                organization_id=organization_id,
                run_id=run.id,
                principal_id=user.id,
                agent_version_id=agent_version.id,
                action="answer.publish",
                resource={"run_id": str(run.id)},
                context={"environment": "production"},
                risk_level=RiskLevel.L2,
                effect=DecisionEffect.ALLOW,
                matched_policy_ids=[],
                obligations=[],
                reason_codes=["incident_golden_verified"],
                input_fingerprint="4" * 64,
                created_at=now,
            )
            session.add(policy_decision)
            source_run_id = run.id

        runtime = object.__new__(HarnessRuntime)
        runtime.database = database
        runtime.events = EventStore()
        runtime.critic = Critic()
        runtime.incident_fusion = IncidentEvidenceFusion()
        runtime.audit = AuditWriter()
        await runtime._respond(organization_id, source_run_id)

        async with database.sessions() as session:
            source_run = await session.get(Run, source_run_id)
            assert source_run is not None and source_run.status == RunStatus.COMPLETED
            artifact = await session.scalar(
                select(Artifact).where(Artifact.run_id == source_run_id)
            )
            assert artifact is not None and artifact.inline_content is not None
            fusion = artifact.inline_content["incident_fusion"]
            assert fusion["candidate_count"] == 3
            assert set(fusion["top1"]["evidence_types"]) == {"METRIC", "DEPLOYMENT"}
            assert artifact.inline_content["verification"]["verified"] is True
            claims = list(
                await session.scalars(
                    select(Claim).where(Claim.run_id == source_run_id).order_by(Claim.ordinal)
                )
            )
            assert len(claims) == 3
            for claim in claims:
                linked_types = set(
                    await session.scalars(
                        select(Evidence.evidence_type)
                        .join(ClaimEvidence, ClaimEvidence.evidence_id == Evidence.id)
                        .where(ClaimEvidence.claim_id == claim.id)
                    )
                )
                assert len(linked_types) >= 2
            assessment = await session.scalar(
                select(VerificationAssessment).where(VerificationAssessment.run_id == source_run_id)
            )
            assert assessment is not None
            assert str(assessment.outcome) == "VERIFIED"
            assert str(assessment.publication_decision) == "PUBLISH"
            assert assessment.policy_decision_id == policy_decision.id
            assert (
                await session.scalar(
                    select(func.count()).select_from(Event).where(Event.run_id == source_run_id)
                )
                >= 4
            )

        async with database.sessions() as session, session.begin():
            replay = Run(
                organization_id=organization_id,
                turn_id=turn.id,
                status=RunStatus.RUNNING,
                replay_of_run_id=source_run_id,
            )
            session.add(replay)
            await session.flush()
            replay_run_id = replay.id
        async with database.sessions() as session, session.begin():
            await RunReplayService().materialize(session, organization_id, replay_run_id)
        async with database.sessions() as session:
            replay_assessment = await session.scalar(
                select(VerificationAssessment).where(VerificationAssessment.run_id == replay_run_id)
            )
            assert replay_assessment is not None
            assert replay_assessment.id != assessment.id
            assert replay_assessment.input_fingerprint == assessment.input_fingerprint
            assert replay_assessment.replay_lineage["replay_source_assessment_id"] == str(
                assessment.id
            )
            assert (
                await session.scalar(
                    select(func.count()).select_from(Claim).where(Claim.run_id == replay_run_id)
                )
                == 3
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Evidence)
                    .where(Evidence.run_id == replay_run_id)
                )
                == 3
            )
    finally:
        await database.dispose()
