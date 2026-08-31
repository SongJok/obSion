from fastapi.testclient import TestClient

from obsion.harness.planner import Planner

_PHASE8_PLACEHOLDERS = {
    "knowledge.search": "DOCUMENT",
    "data.query": "DATA",
    "metric.query": "METRIC",
    "log.search": "LOG",
    "git.diff": "GIT",
}


def test_capability_descriptor_contract_exposes_phase8_placeholders(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/capabilities")
    assert response.status_code == 200, response.text

    descriptors = {item["name"]: item for item in response.json()}
    assert _PHASE8_PLACEHOLDERS.keys() <= descriptors.keys()
    for name, evidence_type in _PHASE8_PLACEHOLDERS.items():
        descriptor = descriptors[name]
        assert descriptor["version"] >= 1
        assert descriptor["input_schema"]["type"] == "object"
        assert descriptor["output_schema"]["type"] == "object"
        assert descriptor["output"]["kind"] == "Evidence"
        assert descriptor["output"]["mapping"]["type"] == evidence_type
        assert descriptor["risk"] in {"L0", "L1", "L2"}
        assert descriptor["side_effect"] == "NONE"
        assert descriptor["permission"]
        assert descriptor["timeout_seconds"] > 0

        detail = client.get(f"/api/v1/capabilities/{descriptor['id']}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["version_id"] == descriptor["version_id"]


def test_planner_selects_only_registered_capabilities() -> None:
    understanding = {
        "route": "INCIDENT",
        "question": "为什么 production p99 latency 上升",
        "time_range": {"start": "2026-08-28T00:00:00Z", "end": "2026-08-28T01:00:00Z"},
    }

    plan = Planner().create(
        understanding,
        available_capabilities=frozenset({"metric.query", "log.search", "git.diff"}),
    )

    selected = {step.capability for step in plan.steps}
    assert selected == {"metric.query", "log.search", "git.diff"}
    assert "metric.anomaly" not in selected
    assert "deployment.list" not in selected
    assert "trace.search" not in selected

    unavailable = Planner().create(
        {"route": "KNOWLEDGE", "question": "release policy"},
        available_capabilities=frozenset(),
    )
    assert unavailable.steps == ()
    assert unavailable.required_evidence == ("DOCUMENT",)
