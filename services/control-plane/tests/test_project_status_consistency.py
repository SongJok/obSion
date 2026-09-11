from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from obsion.release.project_status import (
    ProjectStatusConsistencyError,
    validate_project_status,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_current_repository_phase_status_is_consistent() -> None:
    status = yaml.safe_load(
        (REPOSITORY_ROOT / "docs" / "project-status.yaml").read_text(encoding="utf-8")
    )

    result = validate_project_status(REPOSITORY_ROOT)

    assert result["current_phase"] == status["current_phase"]
    assert result["completed_phases"] == status["completed_phases"]
    assert result["completed_phases"][-1] == result["current_phase"]
    assert result["next_phase"] == status["next_phase"]["id"]
    assert result["formal_report_count"] >= len(result["completed_phases"])
    assert result["architecture_gate_count"] >= len(result["completed_phases"])
    assert result["production_promotion_evaluated"] is False


def test_future_report_and_gate_require_explicit_matching_non_final_status(
    tmp_path: Path,
) -> None:
    root = _repository(
        tmp_path,
        current=1,
        completed=[1],
        next_phase=2,
    )
    _report(root, 1)
    _gate(root, 1)
    future_report = _report(root, 3)
    _gate(root, 3, status="PROVISIONAL")

    with pytest.raises(
        ProjectStatusConsistencyError,
        match=r"PHASE-03-REPORT\.md.*must declare.*PROVISIONAL",
    ):
        validate_project_status(root)

    future_report.write_text("# Phase 3 report\n\n状态：PROVISIONAL\n", encoding="utf-8")
    result = validate_project_status(root)

    assert result["non_completed_reports"] == {"phase-03": "PROVISIONAL"}
    assert result["production_promotion_evaluated"] is False


def test_non_completed_status_is_structural_not_a_historical_text_keyword(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path, current=1, completed=[1], next_phase=2)
    _report(root, 1)
    _gate(root, 1)
    report = _report(root, 3)
    gate = _gate(root, 3, status="PROVISIONAL")
    report.write_text(
        "# Phase 3 report\n\nThe old report says Status: PROVISIONAL in a historical quote.\n",
        encoding="utf-8",
    )

    with pytest.raises(ProjectStatusConsistencyError, match=r"must declare one of"):
        validate_project_status(root)

    report.write_text("# Phase 3 report\n\nStatus: PROVISIONAL\n", encoding="utf-8")
    gate.write_text(
        "# Phase 3 architecture review\n\nStatus: PENDING\n\n"
        "Later text mentions PROVISIONAL as history.\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ProjectStatusConsistencyError,
        match=r"architecture gate.*invalid non-final status 'PENDING'",
    ):
        validate_project_status(root)


def test_orphan_future_gate_requires_explicit_non_final_status(tmp_path: Path) -> None:
    root = _repository(tmp_path, current=1, completed=[1], next_phase=2)
    _report(root, 1)
    _gate(root, 1)
    gate = _gate(root, 3)

    with pytest.raises(
        ProjectStatusConsistencyError,
        match=r"architecture gate without a formal report.*phase-3-fixture-gate\.md.*must declare",
    ):
        validate_project_status(root)

    gate.write_text("# Phase 3 architecture review\n\nStatus: IN_PROGRESS\n", encoding="utf-8")
    result = validate_project_status(root)

    assert result["orphan_non_completed_gates"] == {"phase-03": "IN_PROGRESS"}


def test_orphan_future_gate_rejects_a_complete_declaration(tmp_path: Path) -> None:
    root = _repository(tmp_path, current=1, completed=[1], next_phase=2)
    _report(root, 1)
    _gate(root, 1)
    _gate(root, 101, status="COMPLETE")

    with pytest.raises(
        ProjectStatusConsistencyError,
        match=r"architecture gate without a formal report.*invalid non-final status 'COMPLETE'",
    ):
        validate_project_status(root)


def test_completed_phase_cannot_have_a_non_final_document_status(tmp_path: Path) -> None:
    root = _repository(tmp_path, current=2, completed=[1, 2], next_phase=3)
    _report(root, 1)
    _gate(root, 1)
    _report(root, 2, status="IN_PROGRESS")
    _gate(root, 2)

    with pytest.raises(
        ProjectStatusConsistencyError,
        match=r"completed phase phase-02 has non-final report status IN_PROGRESS",
    ):
        validate_project_status(root)


def test_completed_phase_requires_formal_report(tmp_path: Path) -> None:
    root = _repository(tmp_path, current=2, completed=[1, 2], next_phase=3)
    _report(root, 1)
    _gate(root, 1)
    _gate(root, 2)

    with pytest.raises(
        ProjectStatusConsistencyError,
        match=r"completed phase phase-02 is missing its formal report.*PHASE-02-REPORT\.md",
    ):
        validate_project_status(root)


def test_completed_phase_requires_corresponding_architecture_gate(tmp_path: Path) -> None:
    root = _repository(tmp_path, current=2, completed=[1, 2], next_phase=3)
    _report(root, 1)
    _gate(root, 1)
    _report(root, 2)

    with pytest.raises(
        ProjectStatusConsistencyError,
        match=r"completed phase phase-02 is missing its architecture gate.*phase-2-\*\.md",
    ):
        validate_project_status(root)


@pytest.mark.parametrize("second_status", ["PASS", "PENDING"])
def test_phase_rejects_multiple_architecture_gates(tmp_path: Path, second_status: str) -> None:
    root = _repository(tmp_path, current=98, completed=[98], next_phase=99)
    _report(root, 98)
    architecture = root / "docs" / "architecture"
    first = architecture / "phase-98-local-password-credentials.md"
    second = architecture / "phase-98-yunxiao-readiness-gate.md"
    first.write_text("# Local operator access\n\nStatus: PASS\n", encoding="utf-8")
    second.write_text(f"# Yunxiao readiness\n\nStatus: {second_status}\n", encoding="utf-8")

    with pytest.raises(ProjectStatusConsistencyError) as error:
        validate_project_status(root)

    assert str(error.value) == (
        "phase phase-98 has multiple architecture gates: "
        "docs/architecture/phase-98-local-password-credentials.md, "
        "docs/architecture/phase-98-yunxiao-readiness-gate.md"
    )


def test_current_phase_may_be_completed_but_next_phase_may_not(tmp_path: Path) -> None:
    root = _repository(tmp_path, current=2, completed=[1, 2, 3], next_phase=3)
    _report(root, 1)
    _gate(root, 1)
    _report(root, 2)
    _gate(root, 2)
    _report(root, 3)
    _gate(root, 3)

    with pytest.raises(
        ProjectStatusConsistencyError,
        match=r"completed phases cannot be later than current_phase: phase-03",
    ):
        validate_project_status(root)


def test_registered_root_historical_report_requires_exact_status(tmp_path: Path) -> None:
    root = _repository(tmp_path, current=1, completed=[1], next_phase=2)
    _report(root, 1)
    _gate(root, 1)
    historical = root / "PHASE-102-WEEK1-COMPLETE.md"
    historical.write_text(
        "# Historical report\n\nStatus: IN_PROGRESS\n\nSUPERSEDED is quoted later.\n",
        encoding="utf-8",
    )

    with pytest.raises(
        ProjectStatusConsistencyError,
        match=r"PHASE-102-WEEK1-COMPLETE\.md:3 must be SUPERSEDED, got 'IN_PROGRESS'",
    ):
        validate_project_status(root)

    historical.write_text(
        "# Historical report\n\n状态：SUPERSEDED — not completion evidence\n",
        encoding="utf-8",
    )
    result = validate_project_status(root)

    assert result["registered_historical_reports"] == {"PHASE-102-WEEK1-COMPLETE.md": "SUPERSEDED"}


def test_status_validator_does_not_interpret_pending_as_promotion(tmp_path: Path) -> None:
    root = _repository(tmp_path, current=1, completed=[1], next_phase=2, blocked=True)
    _report(root, 1)
    _gate(root, 1, status="PENDING")

    result = validate_project_status(root)

    assert result["production_promotion_evaluated"] is False
    assert "promotion_eligible" not in result


def _repository(
    root: Path,
    *,
    current: int,
    completed: list[int],
    next_phase: int,
    blocked: bool = True,
) -> Path:
    (root / "docs" / "phases").mkdir(parents=True)
    (root / "docs" / "architecture").mkdir(parents=True)
    document = {
        "project": "fixture",
        "version": "0.0.0-dev",
        "completed_phases": [_phase_id(number) for number in completed],
        "current_phase": _phase_id(current),
        "next_phase": {
            "id": _phase_id(next_phase),
            "name": "fixture-next",
            "blocked": blocked,
            "notes": "Fixture only; operator promotion remains outside this validator.",
        },
    }
    (root / "docs" / "project-status.yaml").write_text(
        yaml.safe_dump(document, sort_keys=False),
        encoding="utf-8",
    )
    return root


def _report(root: Path, number: int, *, status: str | None = None) -> Path:
    path = root / "docs" / "phases" / f"PHASE-{number:02d}-REPORT.md"
    status_line = f"\nStatus: {status}\n" if status is not None else ""
    path.write_text(f"# Phase {number} report\n{status_line}", encoding="utf-8")
    return path


def _gate(root: Path, number: int, *, status: str | None = None) -> Path:
    path = root / "docs" / "architecture" / f"phase-{number}-fixture-gate.md"
    status_line = f"\nStatus: {status}\n" if status is not None else ""
    path.write_text(f"# Phase {number} architecture review\n{status_line}", encoding="utf-8")
    return path


def _phase_id(number: int) -> str:
    return f"phase-{number:02d}" if number < 10 else f"phase-{number}"
