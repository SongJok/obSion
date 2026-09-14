"""Execute Golden Dataset ROUTING and SQL_POLICY cases against production code."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from obsion.config import Settings
from obsion.domain.enums import EvaluationResultStatus, EvaluationTarget
from obsion.evaluations.engine import EvaluationEngine, OfflineEvaluationCase
from obsion.evaluations.manifests import validate_evaluation_root


class OfflineEvaluationError(ValueError):
    pass


def execute_offline_evaluations(root: Path, settings: Settings | None = None) -> dict[str, Any]:
    summary = validate_evaluation_root(root)
    engine = EvaluationEngine(settings or Settings())
    executed = 0
    skipped = 0
    failures: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        cases = document.get("cases")
        if not isinstance(cases, list):
            continue
        for payload in cases:
            if not isinstance(payload, dict):
                continue
            evaluator = EvaluationTarget(str(payload["evaluator"]))
            if evaluator == EvaluationTarget.RUN_OUTPUT:
                skipped += 1
                results.append(
                    {
                        "dataset": path.name,
                        "external_id": str(payload.get("external_id") or ""),
                        "evaluator": evaluator.value,
                        "status": "NOT_RUN",
                        "reason": "requires_normal_task_entry_and_published_run_output",
                    }
                )
                continue
            case = OfflineEvaluationCase(
                evaluator=evaluator,
                input_payload=dict(payload.get("input_payload") or {}),
                expected=dict(payload.get("expected") or {}),
                fixtures=dict(payload.get("fixtures") or {}),
                external_id=str(payload.get("external_id") or ""),
            )
            result = engine.evaluate_offline(case)
            executed += 1
            results.append(
                {
                    "dataset": path.name,
                    "external_id": case.external_id,
                    "evaluator": evaluator.value,
                    "status": "PASS" if result.status == EvaluationResultStatus.PASSED else "FAIL",
                    "checks": result.checks,
                    "error_code": result.error_code,
                }
            )
            if result.status != EvaluationResultStatus.PASSED:
                failures.append(
                    {
                        "external_id": case.external_id,
                        "evaluator": evaluator.value,
                        "status": result.status.value,
                        "checks": result.checks,
                        "error_code": result.error_code,
                    }
                )
    if failures:
        raise OfflineEvaluationError(
            "offline Golden Dataset evaluation failed: "
            + ", ".join(item["external_id"] for item in failures)
        )
    return {
        "datasets": summary["datasets"],
        "cases": summary["cases"],
        "executed": executed,
        "skipped": skipped,
        "failed": 0,
        "status": "PASSED",
        # Preserve the historical offline-subset contract, but never represent
        # unexecuted final answers as a successful acceptance/quality run.
        "scope": "OFFLINE_ROUTING_AND_SQL_POLICY",
        "acceptance_status": "BLOCKED" if skipped else "PASS",
        "answer_quality_status": "NOT_RUN",
        "quality_eligible": False,
        "not_run": skipped,
        "results": results,
        "evaluators": summary["evaluators"],
    }
