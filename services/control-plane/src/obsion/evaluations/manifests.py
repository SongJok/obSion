import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from obsion.api.schemas import CreateEvaluationCaseRequest, CreateEvaluationDatasetRequest
from obsion.common.errors import ObsionError
from obsion.evaluations.contracts import validate_case_request


class EvaluationManifestError(ValueError):
    pass


def validate_evaluation_root(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        raise EvaluationManifestError(f"evaluation dataset directory does not exist: {root}")
    files = sorted(root.glob("*.json"))
    if not files:
        raise EvaluationManifestError(f"no evaluation datasets found in: {root}")
    target_counts: dict[str, int] = {}
    case_count = 0
    for path in files:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EvaluationManifestError(f"{path}: invalid JSON: {exc}") from exc
        if not isinstance(document, dict):
            raise EvaluationManifestError(f"{path}: dataset must be a JSON object")
        try:
            CreateEvaluationDatasetRequest.model_validate(document)
        except PydanticValidationError as exc:
            raise EvaluationManifestError(f"{path}: invalid dataset metadata: {exc}") from exc
        cases = document.get("cases")
        if not isinstance(cases, list) or not cases:
            raise EvaluationManifestError(f"{path}: dataset must contain at least one case")
        identities: set[tuple[str, int]] = set()
        for ordinal, payload in enumerate(cases, start=1):
            try:
                case = CreateEvaluationCaseRequest.model_validate(payload)
                if case.evaluator is None:
                    raise EvaluationManifestError(
                        f"{path}: case {ordinal} must declare an explicit evaluator"
                    )
                if "actual" in case.fixtures:
                    raise EvaluationManifestError(
                        f"{path}: case {ordinal} cannot self-report fixtures.actual"
                    )
                validate_case_request(case.evaluator, case)
            except PydanticValidationError as exc:
                raise EvaluationManifestError(f"{path}: invalid case {ordinal}: {exc}") from exc
            except ObsionError as exc:
                raise EvaluationManifestError(
                    f"{path}: invalid case {ordinal}: {exc.code}: {exc.message}"
                ) from exc
            identity = (case.external_id, case.version)
            if identity in identities:
                raise EvaluationManifestError(
                    f"{path}: duplicate case revision {case.external_id}@{case.version}"
                )
            identities.add(identity)
            target_counts[case.evaluator.value] = target_counts.get(case.evaluator.value, 0) + 1
            case_count += 1
    return {
        "datasets": len(files),
        "cases": case_count,
        "evaluators": dict(sorted(target_counts.items())),
    }
