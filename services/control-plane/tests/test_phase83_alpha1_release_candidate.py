from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from obsion.cli import build_parser
from obsion.release import candidate
from obsion.release.candidate import ReleaseCandidateError, validate_release_candidate
from obsion.release.notes import validate_release_notes

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPOSITORY_ROOT / "docs/release/alpha1-candidate-gates.yaml"
MANIFEST_PATH = REPOSITORY_ROOT / "docs/release/0.83.0-dev.yaml"

_OPERATOR_GATES = (
    "staging-deploy",
    "backup-restore-drill",
    "high-cve-signed-promotion",
    "live-identity-secrets-replicas",
    "security-data-owner-signoff",
    "signed-publication",
)


def test_real_alpha1_candidate_contract_maps_every_requirement_and_artifact() -> None:
    result = validate_release_candidate(
        CONTRACT_PATH,
        None,
        REPOSITORY_ROOT,
        contract_only=True,
    )

    assert result == {
        "release_line": "alpha.1",
        "version": "0.97.0-dev",
        "requirements": 37,
        "coverage_surfaces": 4,
        "expected_artifacts": 12,
        "operator_gates": 6,
        "pending_operator_gates": sorted(_OPERATOR_GATES),
        "promotion_eligible": False,
        "artifact_manifest_validated": False,
        "live_evidence_ledgers": 2,
        "live_evidence_probes": 6,
        "drill_evidence_ledgers": 2,
        "drill_evidence_checks": 16,
    }


def test_candidate_validator_accepts_complete_clean_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract_path, artifact_manifest = _fake_repository(tmp_path)
    monkeypatch.setattr(candidate, "_current_revision", lambda _root: "a" * 40)

    result = validate_release_candidate(contract_path, artifact_manifest, tmp_path)

    assert result["artifact_manifest_validated"] is True
    assert result["artifacts"] == 1
    assert result["validation_steps"] == 1
    assert result["promotion_eligible"] is False


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (lambda manifest: manifest["release"].__setitem__("sourceClean", False), "clean source"),
        (
            lambda manifest: manifest["artifacts"][0].__setitem__("sha256", "0" * 64),
            "hash mismatch",
        ),
        (
            lambda manifest: manifest["artifacts"][0].__setitem__("skipped", True),
            "cannot be skipped",
        ),
        (
            lambda manifest: manifest["validation"]["steps"][0].__setitem__("skipped", True),
            "skipped or invalid",
        ),
    ],
)
def test_candidate_artifact_evidence_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: Any,
    expected: str,
) -> None:
    contract_path, artifact_manifest = _fake_repository(tmp_path)
    document = json.loads(artifact_manifest.read_text(encoding="utf-8"))
    mutation(document)
    artifact_manifest.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setattr(candidate, "_current_revision", lambda _root: "a" * 40)

    with pytest.raises(ReleaseCandidateError, match=expected):
        validate_release_candidate(contract_path, artifact_manifest, tmp_path)


def test_pending_operator_gates_block_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract_path, artifact_manifest = _fake_repository(tmp_path)
    monkeypatch.setattr(candidate, "_current_revision", lambda _root: "a" * 40)

    with pytest.raises(ReleaseCandidateError, match="pending operator gates"):
        validate_release_candidate(
            contract_path,
            artifact_manifest,
            tmp_path,
            require_promotion_eligible=True,
        )


def test_satisfied_gates_still_require_full_artifact_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    contract_path, artifact_manifest = _fake_repository(tmp_path)
    document = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    for gate in document["spec"]["operatorGates"]:
        gate.update(
            {
                "status": "SATISFIED",
                "evidence": ["docs/release/evidence/alpha1/test.md"],
                "approvedBy": "accountable-operator",
                "approvedAt": "2026-08-31T10:00:00Z",
            }
        )
    contract_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(candidate, "_current_revision", lambda _root: "a" * 40)

    contract_result = validate_release_candidate(
        contract_path,
        None,
        tmp_path,
        contract_only=True,
    )
    full_result = validate_release_candidate(contract_path, artifact_manifest, tmp_path)

    assert contract_result["promotion_eligible"] is False
    assert contract_result["artifact_manifest_validated"] is False
    assert full_result["promotion_eligible"] is True


def test_pending_operator_gate_cannot_claim_placeholder_evidence(tmp_path: Path) -> None:
    contract_path, _ = _fake_repository(tmp_path)
    document = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    document["spec"]["operatorGates"][0]["evidence"] = ["evidence/test.txt"]
    contract_path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")

    with pytest.raises(ReleaseCandidateError, match="cannot claim evidence"):
        validate_release_candidate(contract_path, None, tmp_path, contract_only=True)


def test_phase83_release_contract_cli_ci_and_status() -> None:
    result = validate_release_notes(MANIFEST_PATH, REPOSITORY_ROOT)
    assert result["name"] == "alpha1-release-candidate-hardening"
    assert result["version"] == "0.83.0-dev"
    assert result["phase"] == 83
    assert result["consolidates"] == list(range(75, 83))
    assert result["database_migration"] == "none"
    assert result["vendors"] == []

    args = build_parser().parse_args(["validate-release-notes"])
    assert args.manifest == "docs/release/0.97.0-dev.yaml"
    candidate_args = build_parser().parse_args(["validate-release-candidate"])
    assert candidate_args.contract == "docs/release/alpha1-candidate-gates.yaml"
    assert candidate_args.contract_only is False

    workflow = (REPOSITORY_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    containers = workflow.split("  containers:", 1)[1].split("\n  java-sdk:", 1)[0]
    assert "make release-artifacts" in containers
    assert "make validate-release-artifacts" in containers
    assert "make validate-release-candidate" in containers
    assert "actions/upload-artifact@v6" in containers
    assert "docker/build-push-action" not in containers
    for forbidden in ("docker push", "npm publish", "uv publish", "git push"):
        assert forbidden not in containers

    status = yaml.safe_load(
        (REPOSITORY_ROOT / "docs/project-status.yaml").read_text(encoding="utf-8")
    )
    assert status["current_phase"] == "phase-97"
    assert status["completed_phases"][-1] == "phase-97"
    assert status["next_phase"] == {
        "id": "phase-98",
        "name": "alpha1-operator-promotion",
        "blocked": True,
        "notes": status["next_phase"]["notes"],
    }


def test_release_builder_requires_clean_source_and_removes_stale_java_outputs() -> None:
    source = (REPOSITORY_ROOT / "scripts/release_artifacts.py").read_text(encoding="utf-8")
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    assert '"status", "--porcelain=v1"' in source
    assert "sourceClean" in source
    assert "--allow-dirty" in source
    assert "--require-clean" in source
    assert '"clean",\n            "package"' in source
    assert '"sha256": inspect.stdout.strip()' in source
    assert "clean-room locked dependency install" in source
    assert "--require-hashes" in source
    assert "--no-deps" in source
    assert "clean-room dependency check" in source
    assert "with_pip=False" in source
    java_target = makefile.split("test-java:", 1)[1].split("\n\n", 1)[0]
    assert "eclipse-temurin:21-jdk" in java_target
    assert "./mvnw -B clean test" in java_target


def _fake_repository(root: Path) -> tuple[Path, Path]:
    (root / "docs").mkdir()
    (root / "docs/release/evidence/alpha1").mkdir(parents=True)
    (root / "evidence").mkdir()
    (root / "dist").mkdir()
    (root / "docs/status.yaml").write_text("version: 0.83.0-dev\n", encoding="utf-8")
    (root / "docs/requirements.md").write_text(
        "# Requirements\n\n"
        "## Requirement matrix\n\n"
        "| Blueprint area | V1 implementation commitment | Primary verification |\n"
        "| --- | --- | --- |\n"
        "| Harness | Durable runtime | Runtime tests |\n\n"
        "## Non-negotiable acceptance gates\n",
        encoding="utf-8",
    )
    (root / "evidence/test.txt").write_text("verified\n", encoding="utf-8")
    (root / "docs/release/evidence/alpha1/test.md").write_text(
        "redacted operator attestation\n",
        encoding="utf-8",
    )
    artifact = root / "dist/package.whl"
    artifact.write_bytes(b"artifact")
    digest = hashlib.sha256(b"artifact").hexdigest()

    contract = {
        "apiVersion": "obsion.ai/v1",
        "kind": "ReleaseCandidateGate",
        "metadata": {"name": "alpha1", "releaseLine": "alpha.1"},
        "spec": {
            "projectStatus": "docs/status.yaml",
            "requirementsSource": "docs/requirements.md",
            "expectedArtifacts": [{"id": "runtime", "name": "runtime", "type": "python-wheel"}],
            "requiredValidationSteps": ["smoke"],
            "requirementCoverage": [
                {
                    "name": "runtime",
                    "artifacts": ["runtime"],
                    "requirements": ["Harness"],
                    "evidence": ["evidence/*.txt"],
                }
            ],
            "operatorGates": [
                {
                    "id": gate_id,
                    "owner": "operator",
                    "status": "PENDING",
                    "requiredEvidence": ["real evidence"],
                    "evidence": [],
                }
                for gate_id in _OPERATOR_GATES
            ],
        },
    }
    contract_path = root / "docs/contract.yaml"
    contract_path.write_text(yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")

    manifest = {
        "apiVersion": "obsion.ai/v1",
        "kind": "ArtifactManifest",
        "release": {
            "version": "0.83.0-dev",
            "revision": "a" * 40,
            "sourceClean": True,
            "builtAt": "2026-08-31T09:00:00Z",
            "externallyPublished": False,
        },
        "artifacts": [
            {
                "name": "runtime",
                "type": "python-wheel",
                "path": "dist/package.whl",
                "sha256": digest,
                "sizeBytes": len(b"artifact"),
            }
        ],
        "validation": {
            "validatedAt": "2026-08-31T09:30:00Z",
            "steps": [{"name": "smoke", "skipped": False, "detail": "ok"}],
        },
    }
    artifact_manifest = root / "dist/artifact-manifest.json"
    artifact_manifest.write_text(json.dumps(manifest), encoding="utf-8")
    return contract_path, artifact_manifest
