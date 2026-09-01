"""Phase 90: per-stage investigation narrative in the Runtime panel.

Static boundary tests pinning the step-to-evidence-to-claim correlation
contract: every timeline step shows its persisted duration, the evidence
it produced (via persisted step_id), and the claims that evidence
supports — a pure projection with no invented data and no new API.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from obsion.release.notes import validate_release_notes

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps" / "web" / "src"


def _read(relative: str) -> str:
    return (WEB / relative).read_text(encoding="utf-8")


def test_timeline_correlates_steps_with_persisted_evidence() -> None:
    inspector = _read("components/runtime-inspector.tsx")
    # Correlation uses the persisted step_id foreign key only.
    assert "item.step_id === step.id" in inspector
    assert "step-evidence-chip" in inspector
    assert "onSelectEvidence(item)" in inspector


def test_step_duration_comes_from_persisted_timestamps() -> None:
    inspector = _read("components/runtime-inspector.tsx")
    assert "stepDurationLabel" in inspector
    assert "step.started_at" in inspector and "step.completed_at" in inspector
    # No duration is fabricated when timestamps are missing.
    assert "return undefined;" in inspector


def test_claims_are_linked_through_evidence_ids() -> None:
    inspector = _read("components/runtime-inspector.tsx")
    assert "claimIndexByEvidenceId" in inspector
    assert "claim.evidence_ids" in inspector
    assert "结论 C" in inspector
    assert "onOpenClaims" in inspector


def test_step_evidence_display_is_bounded() -> None:
    inspector = _read("components/runtime-inspector.tsx")
    assert "MAX_STEP_EVIDENCE_CHIPS" in inspector
    assert "slice(0, MAX_STEP_EVIDENCE_CHIPS)" in inspector


def test_unattributed_evidence_stays_visible() -> None:
    inspector = _read("components/runtime-inspector.tsx")
    # Attachment-style evidence has no step_id; it must not disappear.
    assert "unattributed-evidence" in inspector
    assert "未关联步骤的证据" in inspector
    assert "!item.step_id" in inspector


def test_narrative_styles_exist() -> None:
    styles = _read("app/globals.css")
    for selector in (
        ".step-evidence",
        ".step-evidence-chip",
        ".step-claims",
        ".unattributed-evidence",
    ):
        assert selector in styles


def test_release_notes_and_project_status_track_phase90() -> None:
    result = validate_release_notes(ROOT / "docs" / "release" / "0.90.0-dev.yaml", ROOT)
    assert result["version"] == "0.90.0-dev"
    status = yaml.safe_load((ROOT / "docs" / "project-status.yaml").read_text(encoding="utf-8"))
    assert status["version"] == "0.96.0-dev"
    assert status["current_phase"] == "phase-96"
    assert "phase-90" in status["completed_phases"]
