from __future__ import annotations

import json
import re

import pytest

from obsion.evaluations.diagnostics import (
    classify_acceptance_result,
    summarize_failure_categories,
)


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
