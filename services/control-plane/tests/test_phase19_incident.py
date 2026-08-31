from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from obsion.db.models import Evidence
from obsion.domain.enums import Classification, EvidenceType
from obsion.harness.agent_router import AgentRouter
from obsion.harness.critic import Critic
from obsion.harness.incident import IncidentEvidenceFusion
from obsion.harness.planner import Planner
from obsion.harness.understanding import UnderstandingEngine


def _evidence(kind: EvidenceType, content: dict, observed_at: datetime) -> Evidence:
    return Evidence(
        id=uuid4(),
        organization_id=uuid4(),
        run_id=uuid4(),
        evidence_type=kind,
        source=f"{kind.value.casefold()}-provider",
        resource="service://payments",
        observed_at=observed_at,
        ingested_at=observed_at,
        content=content,
        content_fingerprint=uuid4().hex + uuid4().hex,
        confidence=Decimal("1.0"),
        classification=Classification.INTERNAL,
        permissions=[],
        lineage={},
    )


def test_incident_fusion_ranks_metric_deployment_and_requires_two_types() -> None:
    start = datetime(2026, 8, 29, 8, 0, tzinfo=UTC)
    metric = _evidence(
        EvidenceType.METRIC,
        {
            "operation": "metric.anomaly",
            "events": [
                {
                    "timestamp": start.isoformat(),
                    "service": "payments",
                    "environment": "production",
                    "deployment_id": "dep-42",
                    "severity": "critical",
                }
            ],
            "count": 1,
        },
        start,
    )
    deployment = _evidence(
        EvidenceType.DEPLOYMENT,
        {
            "operation": "deployment.list",
            "items": [
                {
                    "timestamp": (start + timedelta(minutes=2)).isoformat(),
                    "service": "payments",
                    "environment": "production",
                    "deployment_id": "dep-42",
                    "commit_id": "abc1234",
                    "status": "SUCCEEDED",
                }
            ],
            "count": 1,
        },
        start + timedelta(minutes=2),
    )
    result = IncidentEvidenceFusion().fuse([metric, deployment])

    assert result.top1 is not None
    assert result.top1.rank == 1
    assert set(result.top1.evidence_types) == {"METRIC", "DEPLOYMENT"}
    assert result.top1.evidence_ids == (str(metric.id), str(deployment.id))
    assert result.top1.score >= 0.9
    assert result.as_dict()["top3"][0]["rank"] == 1


def test_incident_critic_rejects_single_type_claim() -> None:
    now = datetime.now(UTC)
    metric = _evidence(EvidenceType.METRIC, {"value": 1}, now)
    result = Critic().verify(
        [metric],
        required_types=("METRIC",),
        claims=[{"statement": "candidate", "evidence_ids": [str(metric.id)]}],
        route="INCIDENT",
    )

    assert not result.verified
    assert not result.checks["claim_links"]


def test_incident_plan_is_ordered_and_exposes_repository_for_code_diff() -> None:
    plan = Planner().create(
        {
            "route": "INCIDENT",
            "question": "Why did payments latency increase after the release?",
            "service": "payments",
            "repository": "acme/payments",
            "time_range": {
                "start": "2026-08-29T08:00:00Z",
                "end": "2026-08-29T09:00:00Z",
            },
        },
        available_capabilities=frozenset(
            {
                "metric.query",
                "metric.compare",
                "metric.anomaly",
                "metric.dimension",
                "deployment.list",
                "log.aggregate",
                "log.search",
                "git.diff",
            }
        ),
    )

    assert [step.capability for step in plan.steps] == [
        "metric.query",
        "metric.compare",
        "metric.anomaly",
        "metric.dimension",
        "deployment.list",
        "log.aggregate",
        "log.search",
        "git.diff",
    ]
    assert plan.steps[1].depends_on == (1,)
    assert plan.steps[2].depends_on == (2,)
    assert plan.steps[4].depends_on == (4,)
    assert plan.steps[-1].payload["repository"] == "acme/payments"
    assert plan.steps[-1].resource["repository"] == "acme/payments"


def test_incident_fusion_keeps_conflicts_and_limits_candidates_to_top_three() -> None:
    now = datetime(2026, 8, 29, 8, 0, tzinfo=UTC)
    metric = _evidence(
        EvidenceType.METRIC,
        {
            "events": [
                {"timestamp": now.isoformat(), "service": "payments", "status": "critical"},
                {
                    "timestamp": (now + timedelta(minutes=1)).isoformat(),
                    "service": "payments",
                    "status": "healthy",
                },
            ]
        },
        now,
    )
    log = _evidence(
        EvidenceType.LOG,
        {
            "events": [
                {
                    "timestamp": now.isoformat(),
                    "service": "payments",
                    "severity": "error",
                    "error_type": "TimeoutError",
                }
            ]
        },
        now,
    )
    deployment = _evidence(
        EvidenceType.DEPLOYMENT,
        {
            "items": [
                {
                    "timestamp": now.isoformat(),
                    "service": "payments",
                    "deployment_id": "dep-42",
                }
            ]
        },
        now,
    )
    result = IncidentEvidenceFusion().fuse([metric, log, deployment])

    assert 1 <= len(result.candidates) <= 3
    assert result.candidates[0].rank == 1
    assert len({candidate.evidence_ids for candidate in result.candidates}) == len(
        result.candidates
    )
    assert result.conflicts
    assert any(
        {"METRIC", "LOG"}.issubset(set(candidate.evidence_types)) for candidate in result.candidates
    )


def test_understanding_routes_explicit_operational_incident_to_incident_agent() -> None:
    understanding = UnderstandingEngine().route(
        "生产环境 p99 latency 异常的根因是什么？",
        {
            "domain": "KNOWLEDGE",
            "intent": "ANALYTICS_QUERY",
            "metrics": [],
            "dimensions": [],
            "time_range": {},
            "comparison": None,
        },
    )

    assert understanding["route"] == "INCIDENT"
    assert understanding["need_root_cause"] is True
    assert AgentRouter._SPECIALISTS["INCIDENT"] == (  # noqa: SLF001
        "incident-agent",
        "incident-investigation",
    )


def test_incident_fusion_does_not_turn_empty_provider_results_into_signals() -> None:
    now = datetime.now(UTC)
    empty_metric = _evidence(
        EvidenceType.METRIC,
        {"operation": "metric.query", "events": [], "count": 0},
        now,
    )
    empty_deployment = _evidence(
        EvidenceType.DEPLOYMENT,
        {"operation": "deployment.list", "items": [], "count": 0},
        now,
    )

    result = IncidentEvidenceFusion().fuse([empty_metric, empty_deployment])

    assert result.candidates == ()
    assert result.evidence_type_coverage == ()
