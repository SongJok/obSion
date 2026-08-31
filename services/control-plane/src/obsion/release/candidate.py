"""Fail-closed Alpha.1 release-candidate evidence validation.

The candidate contract binds the human requirements matrix to the exact artifact
classes built by CI.  Repository automation may prove build readiness while
operator-owned staging, DR, signing, and human approvals remain explicitly
pending; pending gates can never be reported as production-promotion authority.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from obsion.release.drill import DrillError, validate_drill_evidence
from obsion.release.live_evidence import LiveEvidenceError, validate_live_evidence

_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_REQUIRED_OPERATOR_GATES = frozenset(
    {
        "staging-deploy",
        "backup-restore-drill",
        "high-cve-signed-promotion",
        "live-identity-secrets-replicas",
        "security-data-owner-signoff",
        "signed-publication",
    }
)


class ReleaseCandidateError(ValueError):
    """Raised when release-candidate evidence is incomplete or over-claims readiness."""


def validate_release_candidate(
    contract_path: Path,
    artifact_manifest_path: Path | None,
    repository_root: Path,
    *,
    contract_only: bool = False,
    require_promotion_eligible: bool = False,
) -> dict[str, Any]:
    """Validate the candidate contract and, unless requested otherwise, built artifacts."""

    root = repository_root.resolve()
    contract = _load_mapping(contract_path, "release candidate contract")
    if contract.get("apiVersion") != "obsion.ai/v1":
        raise ReleaseCandidateError("release candidate apiVersion must be obsion.ai/v1")
    if contract.get("kind") != "ReleaseCandidateGate":
        raise ReleaseCandidateError("release candidate kind must be ReleaseCandidateGate")

    metadata = _mapping(contract, "metadata")
    if _string(metadata, "name") != "alpha1":
        raise ReleaseCandidateError("release candidate metadata.name must be alpha1")
    if _string(metadata, "releaseLine") != "alpha.1":
        raise ReleaseCandidateError("release candidate metadata.releaseLine must be alpha.1")

    spec = _mapping(contract, "spec")
    project_status_path = _repository_file(_string(spec, "projectStatus"), root)
    project_status = _load_mapping(project_status_path, "project status")
    version = project_status.get("version")
    if not isinstance(version, str) or not version:
        raise ReleaseCandidateError("project status must declare a release version")

    requirements_path = _repository_file(_string(spec, "requirementsSource"), root)
    requirement_rows = _requirements(requirements_path)
    expected_artifacts = _expected_artifacts(spec)
    surfaces = _coverage_surfaces(spec, root, requirement_rows, expected_artifacts)
    operator_gates, pending_gates = _operator_gates(spec, root)
    required_steps = _unique_strings(spec, "requiredValidationSteps")
    live_evidence = _live_evidence(spec, root)
    drill_evidence = _drill_evidence(spec, root)

    summary: dict[str, Any] = {
        "release_line": "alpha.1",
        "version": version,
        "requirements": len(requirement_rows),
        "coverage_surfaces": len(surfaces),
        "expected_artifacts": len(expected_artifacts),
        "operator_gates": operator_gates,
        "pending_operator_gates": pending_gates,
        "promotion_eligible": False,
        "artifact_manifest_validated": False,
    }
    summary.update(live_evidence)
    summary.update(drill_evidence)

    if contract_only:
        if require_promotion_eligible:
            raise ReleaseCandidateError(
                "promotion eligibility cannot be established without an artifact manifest"
            )
        return summary
    if artifact_manifest_path is None:
        raise ReleaseCandidateError("artifact manifest is required for candidate validation")

    artifact_summary = _validate_artifact_manifest(
        artifact_manifest_path,
        root,
        version,
        expected_artifacts,
        required_steps,
    )
    summary.update(artifact_summary)
    summary["artifact_manifest_validated"] = True
    summary["promotion_eligible"] = not pending_gates
    if require_promotion_eligible and pending_gates:
        raise ReleaseCandidateError(
            "production promotion is blocked by pending operator gates: " + ", ".join(pending_gates)
        )
    return summary


def _requirements(path: Path) -> list[str]:
    rows: list[str] = []
    in_matrix = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() == "## Requirement matrix":
            in_matrix = True
            continue
        if in_matrix and line.startswith("## "):
            break
        if not in_matrix or not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) != 3 or cells[0] in {"Blueprint area", "---"}:
            continue
        if not all(cells):
            raise ReleaseCandidateError("requirements matrix contains an empty cell")
        rows.append(cells[0])
    if not rows:
        raise ReleaseCandidateError("requirements matrix contains no requirement rows")
    duplicates = sorted(name for name, count in Counter(rows).items() if count > 1)
    if duplicates:
        raise ReleaseCandidateError(
            "requirements matrix area names must be unique: " + ", ".join(duplicates)
        )
    return rows


def _expected_artifacts(spec: dict[str, Any]) -> dict[str, tuple[str, str]]:
    raw = spec.get("expectedArtifacts")
    if not isinstance(raw, list) or not raw:
        raise ReleaseCandidateError("release candidate expectedArtifacts must be a list")
    artifacts: dict[str, tuple[str, str]] = {}
    identities: set[tuple[str, str]] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ReleaseCandidateError(f"expected artifact at index {index} must be an object")
        artifact_id = _string(item, "id")
        identity = (_string(item, "name"), _string(item, "type"))
        if artifact_id in artifacts or identity in identities:
            raise ReleaseCandidateError("expected artifacts must have unique ids and identities")
        artifacts[artifact_id] = identity
        identities.add(identity)
    return artifacts


def _coverage_surfaces(
    spec: dict[str, Any],
    root: Path,
    requirement_rows: list[str],
    expected_artifacts: dict[str, tuple[str, str]],
) -> list[str]:
    raw = spec.get("requirementCoverage")
    if not isinstance(raw, list) or not raw:
        raise ReleaseCandidateError("release candidate requirementCoverage must be a list")
    surfaces: list[str] = []
    covered_requirements: list[str] = []
    covered_artifacts: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ReleaseCandidateError(f"coverage surface at index {index} must be an object")
        name = _string(item, "name")
        if name in surfaces:
            raise ReleaseCandidateError("coverage surface names must be unique")
        surfaces.append(name)
        artifact_ids = _unique_strings(item, "artifacts")
        unknown = sorted(set(artifact_ids) - set(expected_artifacts))
        if unknown:
            raise ReleaseCandidateError(
                f"coverage surface {name} references unknown artifacts: {', '.join(unknown)}"
            )
        covered_artifacts.update(artifact_ids)
        covered_requirements.extend(_unique_strings(item, "requirements"))
        evidence_patterns = _unique_strings(item, "evidence")
        for pattern in evidence_patterns:
            _repository_matches(pattern, root)

    if Counter(covered_requirements) != Counter(requirement_rows):
        missing = sorted(set(requirement_rows) - set(covered_requirements))
        extra = sorted(set(covered_requirements) - set(requirement_rows))
        repeated = sorted(
            name for name, count in Counter(covered_requirements).items() if count > 1
        )
        raise ReleaseCandidateError(
            "requirement coverage must map every matrix row exactly once"
            f"; missing={missing}; extra={extra}; repeated={repeated}"
        )
    missing_artifacts = sorted(set(expected_artifacts) - covered_artifacts)
    if missing_artifacts:
        raise ReleaseCandidateError(
            "requirement coverage does not reference expected artifacts: "
            + ", ".join(missing_artifacts)
        )
    return surfaces


def _operator_gates(spec: dict[str, Any], root: Path) -> tuple[int, list[str]]:
    raw = spec.get("operatorGates")
    if not isinstance(raw, list) or not raw:
        raise ReleaseCandidateError("release candidate operatorGates must be a list")
    ids: set[str] = set()
    pending: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ReleaseCandidateError(f"operator gate at index {index} must be an object")
        gate_id = _string(item, "id")
        if gate_id in ids:
            raise ReleaseCandidateError("operator gate ids must be unique")
        ids.add(gate_id)
        _string(item, "owner")
        required_evidence = _unique_strings(item, "requiredEvidence")
        status = _string(item, "status")
        evidence = item.get("evidence")
        if not isinstance(evidence, list) or any(
            not isinstance(value, str) or not value.strip() for value in evidence
        ):
            raise ReleaseCandidateError("operator gate evidence must be a list of paths")
        if status == "PENDING":
            if evidence or "approvedBy" in item or "approvedAt" in item:
                raise ReleaseCandidateError(
                    f"pending operator gate {gate_id} cannot claim evidence or approval"
                )
            pending.append(gate_id)
        elif status == "SATISFIED":
            if len(evidence) < len(required_evidence):
                raise ReleaseCandidateError(
                    f"satisfied operator gate {gate_id} lacks required evidence"
                )
            _string(item, "approvedBy")
            _timestamp(item, "approvedAt")
            for value in evidence:
                if not value.startswith("docs/release/evidence/alpha1/"):
                    raise ReleaseCandidateError(
                        "operator gate evidence must use docs/release/evidence/alpha1/"
                    )
                _repository_file(value, root)
        else:
            raise ReleaseCandidateError("operator gate status must be PENDING or SATISFIED")
    if ids != _REQUIRED_OPERATOR_GATES:
        raise ReleaseCandidateError(
            "operator gates must exactly match the Alpha.1 promotion prerequisites"
        )
    return len(ids), sorted(pending)


def _live_evidence(spec: dict[str, Any], root: Path) -> dict[str, int]:
    raw = spec.get("liveEvidence")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ReleaseCandidateError("release candidate liveEvidence must be an object")
    contract_path = _repository_file(_string(raw, "contract"), root)
    ledger_values = _unique_strings(raw, "ledgers")
    ledger_paths: list[Path] = []
    for value in ledger_values:
        if not value.startswith("docs/release/evidence/alpha1/"):
            raise ReleaseCandidateError(
                "live evidence ledgers must use docs/release/evidence/alpha1/"
            )
        ledger_paths.append(_repository_file(value, root))
    try:
        result = validate_live_evidence(contract_path, ledger_paths, root)
    except LiveEvidenceError as exc:
        raise ReleaseCandidateError(str(exc)) from exc
    return {
        "live_evidence_ledgers": result["ledgers"],
        "live_evidence_probes": result["covered"],
    }


def _drill_evidence(spec: dict[str, Any], root: Path) -> dict[str, int]:
    raw = spec.get("drillEvidence")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ReleaseCandidateError("release candidate drillEvidence must be an object")
    contract_path = _repository_file(_string(raw, "contract"), root)
    ledger_values = _unique_strings(raw, "ledgers")
    ledger_paths: list[Path] = []
    for value in ledger_values:
        if not value.startswith("docs/release/evidence/alpha1/"):
            raise ReleaseCandidateError(
                "drill evidence ledgers must use docs/release/evidence/alpha1/"
            )
        ledger_paths.append(_repository_file(value, root))
    try:
        result = validate_drill_evidence(contract_path, ledger_paths, root)
    except DrillError as exc:
        raise ReleaseCandidateError(str(exc)) from exc
    return {
        "drill_evidence_ledgers": result["ledgers"],
        "drill_evidence_checks": result["checks"],
    }


def _validate_artifact_manifest(
    path: Path,
    root: Path,
    version: str,
    expected_artifacts: dict[str, tuple[str, str]],
    required_steps: list[str],
) -> dict[str, Any]:
    manifest = _load_mapping(path, "artifact manifest", json_only=True)
    if manifest.get("apiVersion") != "obsion.ai/v1" or manifest.get("kind") != "ArtifactManifest":
        raise ReleaseCandidateError("artifact manifest apiVersion/kind is invalid")
    release = _mapping(manifest, "release")
    if release.get("version") != version:
        raise ReleaseCandidateError("artifact manifest version must match project status")
    revision = release.get("revision")
    if not isinstance(revision, str) or not _REVISION_PATTERN.fullmatch(revision):
        raise ReleaseCandidateError("artifact manifest revision must be a full git SHA")
    if release.get("sourceClean") is not True:
        raise ReleaseCandidateError("release candidate artifacts require a clean source tree")
    if release.get("externallyPublished") is not False:
        raise ReleaseCandidateError("repository candidate cannot claim external publication")
    _timestamp(release, "builtAt")
    if revision != _current_revision(root):
        raise ReleaseCandidateError("artifact manifest revision must match the checked-out commit")

    raw_artifacts = manifest.get("artifacts")
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise ReleaseCandidateError("artifact manifest must contain artifacts")
    actual_identities: list[tuple[str, str]] = []
    for index, artifact in enumerate(raw_artifacts):
        if not isinstance(artifact, dict):
            raise ReleaseCandidateError(f"artifact at index {index} must be an object")
        if artifact.get("skipped"):
            raise ReleaseCandidateError("release candidate artifacts cannot be skipped")
        identity = (_string(artifact, "name"), _string(artifact, "type"))
        actual_identities.append(identity)
        if identity[1] == "container-image":
            _string(artifact, "image")
            digest = artifact.get("sha256")
            if not isinstance(digest, str) or not _DIGEST_PATTERN.fullmatch(digest):
                raise ReleaseCandidateError("container artifacts require a sha256 image digest")
            if artifact.get("imageId") != digest:
                raise ReleaseCandidateError("container imageId and sha256 digest must match")
        else:
            artifact_path = _repository_file(_string(artifact, "path"), root)
            size = artifact.get("sizeBytes")
            if type(size) is not int or size != artifact_path.stat().st_size:
                raise ReleaseCandidateError(f"artifact size mismatch for {artifact_path.name}")
            digest = artifact.get("sha256")
            if not isinstance(digest, str) or digest != _sha256(artifact_path):
                raise ReleaseCandidateError(f"artifact hash mismatch for {artifact_path.name}")
    if Counter(actual_identities) != Counter(expected_artifacts.values()):
        raise ReleaseCandidateError("artifact manifest does not match expected Alpha.1 artifacts")

    validation = _mapping(manifest, "validation")
    _timestamp(validation, "validatedAt")
    raw_steps = validation.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ReleaseCandidateError("artifact manifest requires clean-room validation steps")
    completed_steps: set[str] = set()
    for step in raw_steps:
        if not isinstance(step, dict) or step.get("skipped") is not False:
            raise ReleaseCandidateError("artifact validation contains a skipped or invalid step")
        completed_steps.add(_string(step, "name"))
        _string(step, "detail")
    missing_steps = sorted(set(required_steps) - completed_steps)
    if missing_steps:
        raise ReleaseCandidateError(
            "artifact validation is missing required steps: " + ", ".join(missing_steps)
        )
    return {
        "revision": revision,
        "artifacts": len(actual_identities),
        "validation_steps": len(raw_steps),
    }


def _current_revision(root: Path) -> str:
    git = shutil.which("git")
    if git is None:
        raise ReleaseCandidateError("unable to resolve the checked-out git revision")
    try:
        result = subprocess.run(  # noqa: S603 - fixed git invocation
            [git, "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ReleaseCandidateError("unable to resolve the checked-out git revision") from exc
    revision = result.stdout.strip()
    if result.returncode != 0 or not _REVISION_PATTERN.fullmatch(revision):
        raise ReleaseCandidateError("unable to resolve the checked-out git revision")
    return revision


def _load_mapping(path: Path, label: str, *, json_only: bool = False) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        document = json.loads(text) if json_only else yaml.safe_load(text)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ReleaseCandidateError(f"unable to load {label}: {path}") from exc
    if not isinstance(document, dict):
        raise ReleaseCandidateError(f"{label} must be an object")
    return document


def _mapping(parent: dict[str, Any], key: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise ReleaseCandidateError(f"release candidate {key} must be an object")
    return value


def _string(parent: dict[str, Any], key: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ReleaseCandidateError(f"release candidate {key} must be a non-empty string")
    return value.strip()


def _unique_strings(parent: dict[str, Any], key: str) -> list[str]:
    value = parent.get(key)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise ReleaseCandidateError(f"release candidate {key} must be a non-empty string list")
    normalized = [item.strip() for item in value]
    if len(set(normalized)) != len(normalized):
        raise ReleaseCandidateError(f"release candidate {key} must not contain duplicates")
    return normalized


def _timestamp(parent: dict[str, Any], key: str) -> str:
    value = _string(parent, key)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseCandidateError(
            f"release candidate {key} must be an ISO-8601 timestamp"
        ) from exc
    if parsed.tzinfo is None:
        raise ReleaseCandidateError(f"release candidate {key} must include a timezone")
    return value


def _repository_file(value: str, root: Path) -> Path:
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise ReleaseCandidateError("release candidate paths must stay inside the repository")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ReleaseCandidateError(
            "release candidate paths must stay inside the repository"
        ) from exc
    if not resolved.is_file():
        raise ReleaseCandidateError(f"release candidate file does not exist: {value}")
    return resolved


def _repository_matches(pattern: str, root: Path) -> list[Path]:
    relative = Path(pattern)
    if relative.is_absolute() or ".." in relative.parts:
        raise ReleaseCandidateError("release candidate evidence must stay inside the repository")
    matches = [path.resolve() for path in root.glob(pattern) if path.is_file()]
    if not matches:
        raise ReleaseCandidateError(f"release candidate evidence does not match files: {pattern}")
    for match in matches:
        try:
            match.relative_to(root)
        except ValueError as exc:
            raise ReleaseCandidateError(
                "release candidate evidence must stay inside the repository"
            ) from exc
    return matches


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
