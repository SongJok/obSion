"""Release-hardening scanners and evaluation gates.

These helpers are deterministic control-plane tools. They never phone home,
never require cloud credentials, and never weaken Policy or Gateway checks.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key_block", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("postgres_dsn", re.compile(r"postgres(?:ql)?://[^:\s/]+:[^@\s/]+@")),
    ("mysql_dsn", re.compile(r"mysql://[^:\s/]+:[^@\s/]+@")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("pem_literal", re.compile(r"(?i)api[_-]?key\s*[:=]\s*['\"]?(?:sk|rk)-live")),
)
_SKIP_PARTS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    "dist",
    "coverage",
    ".mypy_cache",
    ".ruff_cache",
}
_SCAN_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".yml", ".yaml", ".json", ".env", ".toml", ".md"}


@dataclass(frozen=True, slots=True)
class SecretFinding:
    path: str
    line: int
    kind: str


def scan_secrets(root: Path) -> list[SecretFinding]:
    findings: list[SecretFinding] = []
    resolved = root.resolve()
    for path in sorted(resolved.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _SCAN_SUFFIXES:
            continue
        if any(part in _SKIP_PARTS for part in path.parts):
            continue
        relative = path.relative_to(resolved).as_posix()
        if path.name == ".env" or relative.endswith(".cdx.json"):
            continue
        if "/tests/" in f"/{relative}/" or relative.startswith("tests/"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for index, line in enumerate(text.splitlines(), start=1):
            for kind, pattern in _SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(SecretFinding(path=relative, line=index, kind=kind))
                    break
    return findings


def cyclonedx_sbom(
    lockfile: Path,
    *,
    component_name: str = "obsion",
    component_version: str = "0.1.0",
) -> dict[str, Any]:
    packages = _packages_from_uv_lock(lockfile.read_text(encoding="utf-8"))
    components = [
        {
            "type": "library",
            "name": name,
            "version": version,
            "purl": f"pkg:pypi/{name}@{version}",
        }
        for name, version in packages
    ]
    serialized = json.dumps({"components": components}, sort_keys=True, separators=(",", ":"))
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "version": 1,
        "serialNumber": "urn:uuid:" + hashlib.sha256(serialized.encode()).hexdigest()[:32],
        "metadata": {
            "component": {
                "type": "application",
                "name": component_name,
                "version": component_version,
            }
        },
        "components": components,
    }


def _packages_from_uv_lock(text: str) -> list[tuple[str, str]]:
    packages: list[tuple[str, str]] = []
    name = ""
    for line in text.splitlines():
        if line == "[[package]]":
            name = ""
            continue
        if line.startswith("name = "):
            name = line.split("=", 1)[1].strip().strip('"')
        elif line.startswith("version = ") and name:
            version = line.split("=", 1)[1].strip().strip('"')
            packages.append((name, version))
            name = ""
    return packages


class EvaluationGateError(ValueError):
    pass


def validate_evaluation_gate(path: Path, dataset_summary: dict[str, Any]) -> dict[str, Any]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise EvaluationGateError(f"unable to load evaluation gate {path}") from exc
    if not isinstance(document, dict):
        raise EvaluationGateError("evaluation gate must be an object")
    spec = document.get("spec")
    if not isinstance(spec, dict):
        raise EvaluationGateError("evaluation gate requires spec")
    pass_rate = spec.get("minimumPassRate")
    error_rate = spec.get("maximumErrorRate")
    regression_rate = spec.get("maximumRegressionRate")
    if not isinstance(pass_rate, (int, float)) or not 0 <= float(pass_rate) <= 1:
        raise EvaluationGateError("minimumPassRate must be a ratio in [0, 1]")
    if not isinstance(error_rate, (int, float)) or not 0 <= float(error_rate) <= 1:
        raise EvaluationGateError("maximumErrorRate must be a ratio in [0, 1]")
    if not isinstance(regression_rate, (int, float)) or not 0 <= float(regression_rate) <= 1:
        raise EvaluationGateError("maximumRegressionRate must be a ratio in [0, 1]")
    floors = spec.get("scoreFloors", {})
    if not isinstance(floors, dict) or not floors:
        raise EvaluationGateError("scoreFloors is required")
    for key, value in floors.items():
        if not isinstance(key, str) or not isinstance(value, (int, float)):
            raise EvaluationGateError(f"score floor {key!r} is invalid")
        if not 0 <= float(value) <= 1:
            raise EvaluationGateError(f"score floor {key!r} is invalid")
    required_evaluators = spec.get("requiredEvaluators", [])
    required_routes = spec.get("requiredRoutes", [])
    if not isinstance(required_evaluators, list) or not required_evaluators:
        raise EvaluationGateError("requiredEvaluators is required")
    if not isinstance(required_routes, list) or not required_routes:
        raise EvaluationGateError("requiredRoutes is required")
    present_evaluators = set(dataset_summary.get("evaluators", {}))
    missing_evaluators = sorted(set(map(str, required_evaluators)) - present_evaluators)
    if missing_evaluators:
        raise EvaluationGateError(
            "evaluation datasets are missing required evaluators: " + ", ".join(missing_evaluators)
        )
    present_routes = set(dataset_summary.get("routes", []))
    missing_routes = sorted(set(map(str, required_routes)) - present_routes)
    if missing_routes:
        raise EvaluationGateError(
            "evaluation datasets are missing required routes: " + ", ".join(missing_routes)
        )
    return {
        "gate": document.get("metadata", {}).get("name"),
        "minimum_pass_rate": float(pass_rate),
        "maximum_error_rate": float(error_rate),
        "maximum_regression_rate": float(regression_rate),
        "score_floors": {str(key): float(value) for key, value in floors.items()},
        "required_evaluators": list(required_evaluators),
        "required_routes": list(required_routes),
        "cases": int(dataset_summary.get("cases", 0)),
    }
