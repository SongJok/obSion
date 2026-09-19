from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import pytest
import yaml

from obsion.evaluations.acceptance import _CONFIG_PATHS_V2
from obsion.evaluations.diagnostics import (
    classify_acceptance_result,
    summarize_failure_categories,
)

ROOT = Path(__file__).parents[3]
LEGACY_BASELINE = (
    ROOT / "docs/release/evidence/productization/20260915-frozen-knowledge-baseline.json"
)
H01_DIAGNOSTICS = ROOT / "docs/release/evidence/harness/20260919-h01-legacy-diagnostics.json"
H01_VERTICAL_SLICES = ROOT / "evaluations/productization/h01-vertical-slices-v1.json"
H01_CANDIDATE = ROOT / "docs/release/evidence/harness/20260919-h01-candidate-baseline.json"
H01_BACKLOG = ROOT / "docs/product/autonomous-harness-backlog-20260919.json"
H01_PLAN = ROOT / "docs/product/autonomous-harness-plan-20260919.md"
H01_PROVENANCE = ROOT / "docs/product/autonomous-harness-requirements-provenance.yaml"


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"status": "PASS", "reason": "independent_rules_satisfied"}, None),
        (
            {
                "status": "BLOCKED",
                "reason": "independent_judgment_invalid",
                "score": {"evidence": [{"judgment_diagnostic": "quote_not_substantive"}]},
            },
            "CITATION",
        ),
        (
            {"status": "BLOCKED", "reason": "independent_judgment_invalid"},
            "MODEL_PROTOCOL",
        ),
        ({"status": "FAIL", "reason": "independent_answer_failed"}, "SEMANTIC"),
        ({"status": "BLOCKED", "reason": "independent_model_unavailable"}, "ENDPOINT"),
        ({"status": "FAIL", "reason": "task_timeout"}, "RESOURCE"),
        ({"status": "BLOCKED", "reason": "frozen_source_drift"}, "SOURCE"),
        ({"status": "BLOCKED", "reason": "fresh_source_generation_missing"}, "FRESHNESS"),
        ({"status": "BLOCKED", "reason": "new_unmapped_failure"}, "UNCLASSIFIED"),
    ],
)
def test_acceptance_failures_use_h01_diagnostic_taxonomy(result, expected) -> None:
    assert classify_acceptance_result(result) == expected


def test_failure_summary_preserves_unknowns_instead_of_guessing() -> None:
    summary = summarize_failure_categories(
        [
            {"status": "PASS", "reason": "independent_rules_satisfied"},
            {"status": "BLOCKED", "reason": "independent_model_unavailable"},
            {"status": "FAIL", "reason": "new_unmapped_failure"},
        ]
    )

    assert summary == {"ENDPOINT": 1, "UNCLASSIFIED": 1}


def test_historical_24_case_baseline_is_projected_without_rewriting_or_denominator_loss() -> None:
    source_bytes = LEGACY_BASELINE.read_bytes()
    source = json.loads(source_bytes)
    ledger = json.loads(H01_DIAGNOSTICS.read_bytes())

    assert hashlib.sha256(source_bytes).hexdigest() == ledger["source"]["sha256"]
    assert len(source["cases"]) == ledger["denominator"]["required_cases"] == 24
    status_counts = Counter(item["status"] for item in source["cases"])
    assert (
        ledger["denominator"]["counts"]
        == {
            "PASS": status_counts["PASS"],
            "FAIL": status_counts["FAIL"],
            "BLOCKED": status_counts["BLOCKED"],
            "NOT_RUN": status_counts["NOT_RUN"],
        }
        == {"PASS": 5, "FAIL": 1, "BLOCKED": 18, "NOT_RUN": 0}
    )

    reason_counts = Counter(item["reason"] for item in source["cases"] if item["status"] != "PASS")
    projected = {
        reason: details["count"] for reason, details in ledger["reason_classification"].items()
    }
    assert projected == reason_counts
    assert sum(ledger["category_counts"].values()) == 19
    assert set(ledger["taxonomy"]) == {
        "SOURCE",
        "SEMANTIC",
        "CITATION",
        "MODEL_PROTOCOL",
        "ENDPOINT",
        "RESOURCE",
        "FRESHNESS",
    }


def test_protected_vertical_slice_contract_forbids_fake_real_world_evidence() -> None:
    contract = json.loads(H01_VERTICAL_SLICES.read_bytes())

    assert contract["status"] == "CONTRACT_ONLY"
    assert contract["holdout"]["repository_contains_case_content"] is False
    assert contract["holdout"]["minimum_reviewers"] == 2
    assert contract["absence_policy"] == {
        "missing_enterprise_source_or_credential": "NOT_RUN",
        "missing_required_runtime_receipt": "BLOCKED",
        "mock_or_synthetic_substitution": "FORBIDDEN",
    }
    assert {item["family"] for item in contract["cases"]} == {
        "BUSINESS_LOCALIZATION",
        "EXACT_STATISTICS",
        "ISOLATED_CODE_EXECUTION",
    }
    assert all(item["execution_status"] == "NOT_RUN" for item in contract["cases"])
    assert all(item["required_receipts"] for item in contract["cases"])
    assert len({item["id"] for item in contract["cases"]}) == 3
    for item in contract["cases"]:
        assert not {"question", "gold", "expected_answer", "credential"} & set(item)


def test_h01_candidate_manifest_records_observed_package_and_freezes_all_configuration() -> None:
    manifest = json.loads(H01_CANDIDATE.read_bytes())

    assert re.fullmatch(r"[0-9a-f]{40}", manifest["repository"]["candidate_commit"])
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["repository"]["candidate_tree"])
    assert manifest["deployment"]["api_package"] == {
        "sha256": "cec96b89215e0c5d6ded03d5231551c3d27aa733a73d8488deb856d96412f863",
        "files": 372,
        "observation": "installed_package_at_api_initialization",
    }
    worker = manifest["deployment"]["expected_worker_package"]
    assert (worker["sha256"], worker["files"]) == (
        manifest["deployment"]["api_package"]["sha256"],
        manifest["deployment"]["api_package"]["files"],
    )
    assert worker["actual_run_observation_required"] is True
    assert set(manifest["configuration"]) == _CONFIG_PATHS_V2
    assert all(
        re.fullmatch(r"[0-9a-f]{64}", value["sha256"])
        for value in manifest["configuration"].values()
    )
    assert manifest["evaluation"]["selected_model_profile"] is None
    assert manifest["evaluation"]["selected_model_profile_status"] == "NOT_RUN"
    assert manifest["deployment"]["signature_verified"] is False
    assert manifest["promotion_eligible"] is False
    assert (
        manifest["continuous_integration"]["candidate_commit"]
        == manifest["repository"]["candidate_commit"]
    )
    assert manifest["continuous_integration"] == {
        "run_id": 35439987679,
        "url": "https://github.com/SongJok/obSion/actions/runs/35439987679",
        "candidate_commit": manifest["repository"]["candidate_commit"],
        "status_at_capture": "COMPLETED",
        "conclusion": "SUCCESS",
        "quality_gate": "PASS",
        "quality_job": "SUCCESS",
        "container_job": "SUCCESS",
        "job_count": 15,
        "started_at": "2026-09-19T11:23:40Z",
        "completed_at": "2026-09-19T11:49:54Z",
    }
    security = manifest["container_security"]
    assert security["gate"] == (
        "HIGH,CRITICAL; ignore_unfixed=false; vulnerability and secret scanners enabled"
    )
    assert security["after"] == {
        "control_plane": {"high_or_critical_vulnerabilities": 0, "secrets": 0},
        "web": {"high_or_critical_vulnerabilities": 0, "secrets": 0},
    }
    assert security["remediation"]["web_runtime_build_tools_removed"] == [
        "npm",
        "corepack",
        "yarn",
    ]


def test_autonomous_harness_requirement_packet_is_versioned_and_complete() -> None:
    backlog_bytes = H01_BACKLOG.read_bytes()
    plan_bytes = H01_PLAN.read_bytes()
    backlog = json.loads(backlog_bytes)
    provenance = yaml.safe_load(H01_PROVENANCE.read_text(encoding="utf-8"))

    assert len(backlog["phases"]) == 20
    assert sum(len(phase["tasks"]) for phase in backlog["phases"]) == 100
    assert sum(len(phase["acceptance"]) for phase in backlog["phases"]) == 80
    assert [phase["id"] for phase in backlog["phases"]] == [
        f"H{number:02d}" for number in range(1, 21)
    ]
    assert (
        hashlib.sha256(backlog_bytes).hexdigest() == provenance["sources"][1]["repository_sha256"]
    )
    assert hashlib.sha256(plan_bytes).hexdigest() == provenance["sources"][0]["repository_sha256"]
    assert provenance["sources"][1]["semantic_json_equal"] is True
    assert provenance["sources"][2]["repository_path"] is None


def test_connector_configuration_snapshot_is_stable_and_content_free(client) -> None:
    before = client.get("/api/v1/admin/connectors/configuration-snapshot")
    assert before.status_code == 200

    created = client.post(
        "/api/v1/admin/connectors",
        json={
            "name": "h01-baseline-connector",
            "connector_type": "h01-test",
            "status": "DRAFT",
            "environment": "test",
            "endpoint": "https://connector.example.test",
            "credential_ref": "env://OBSION_H01_TEST_SECRET",
            "configuration": {"protocol": "h01.v1", "tenant": "private-tenant"},
            "declared_grants": ["knowledge.read"],
            "allowed_egress": ["https://connector.example.test"],
        },
    )
    assert created.status_code == 201, created.text

    after = client.get("/api/v1/admin/connectors/configuration-snapshot")
    assert after.status_code == 200
    assert before.json() != after.json()
    repeated = client.get("/api/v1/admin/connectors/configuration-snapshot")
    assert repeated.status_code == 200
    assert repeated.json() == after.json()
    item = next(entry for entry in after.json() if entry["id"] == created.json()["id"])
    assert set(item) == {"id", "name", "status", "configuration_sha256"}
    assert re.fullmatch(r"[0-9a-f]{64}", item["configuration_sha256"])
    serialized = json.dumps(after.json(), sort_keys=True)
    for forbidden in (
        "connector.example.test",
        "OBSION_H01_TEST_SECRET",
        "private-tenant",
        "credential_ref",
        "allowed_egress",
    ):
        assert forbidden not in serialized
