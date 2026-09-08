"""仓库本地阶段状态声明的一致性静态检查。

本模块只验证文档账本，不检查、满足或转换生产操作门禁，也不产生晋级授权。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_PHASE_ID_PATTERN = re.compile(r"^phase-(0[1-9]|[1-9][0-9]*)$")
_REPORT_NAME_PATTERN = re.compile(r"^PHASE-([0-9]+)-REPORT\.md$")
_GATE_NAME_PATTERN = re.compile(r"^phase-([0-9]+)-.+\.md$")
_STATUS_LINE_PATTERN = re.compile(
    r"^\s*(?:>\s*)?(?:[-+*]\s+)?(?:\*\*)?(?:status|状态)(?:\*\*)?\s*[:：]\s*(.+?)\s*$",
    re.IGNORECASE,
)
_STATUS_TOKEN_PATTERN = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")
_NON_FINAL_STATUSES = frozenset({"PROVISIONAL", "IN_PROGRESS", "SUPERSEDED"})
_STATUS_PREAMBLE_LINES = 32

# 根目录报告不属于 docs/phases 正式命名空间。历史声明必须显式登记，不能根据
# 任意 Markdown 文件名或正文里偶然出现的阶段引用进行猜测。
_REGISTERED_ROOT_HISTORICAL_REPORTS: dict[str, str] = {
    "PHASE-102-WEEK1-COMPLETE.md": "SUPERSEDED",
}


class ProjectStatusError(ValueError):
    """仓库阶段状态声明无效时的基础错误。"""


class ProjectStatusConsistencyError(ProjectStatusError):
    """项目状态与本地阶段文档不一致时抛出。"""


def validate_project_status(root: Path) -> dict[str, Any]:
    """验证 *root* 下的仓库本地阶段声明。

    检查范围包括 ``docs/project-status.yaml``、正式的
    ``docs/phases/PHASE-*-REPORT.md`` 报告、按阶段数字对应的
    ``docs/architecture/phase-*.md`` 评审，以及显式登记的根目录历史报告。
    成功结果不是生产晋级证据；本函数有意不评估操作门禁状态。
    """

    repository_root = root.resolve()
    if not repository_root.is_dir():
        raise ProjectStatusConsistencyError(
            f"repository root does not exist or is not a directory: {root}"
        )

    status_path = repository_root / "docs" / "project-status.yaml"
    status = _load_status(status_path)
    current_phase, current_number = _required_phase(status, "current_phase")
    completed_phases, completed_numbers = _completed_phases(status, current_number)
    next_phase = _next_phase(status, current_number, set(completed_numbers))

    reports = _formal_reports(repository_root)
    gates = _architecture_gates(repository_root)
    completed_number_set = set(completed_numbers)

    for phase, number in zip(completed_phases, completed_numbers, strict=True):
        report_path = reports.get(number)
        if report_path is None:
            raise ProjectStatusConsistencyError(
                f"completed phase {phase} is missing its formal report: "
                f"docs/phases/{_report_name(number)}"
            )
        gate_path = gates.get(number)
        if gate_path is None:
            raise ProjectStatusConsistencyError(
                f"completed phase {phase} is missing its architecture gate: "
                f"docs/architecture/phase-{number}-*.md"
            )
        _reject_non_final_completed_marker(report_path, phase, repository_root, "report")
        _reject_non_final_completed_marker(gate_path, phase, repository_root, "architecture gate")

    non_completed_reports: dict[str, str] = {}
    for number, report_path in sorted(reports.items()):
        phase = _canonical_phase_id(number)
        gate_path = gates.get(number)
        if gate_path is None:
            raise ProjectStatusConsistencyError(
                f"formal report {_relative(report_path, repository_root)} has no corresponding "
                f"architecture gate: docs/architecture/phase-{number}-*.md"
            )
        if number in completed_number_set:
            continue

        report_status = _required_non_final_status(
            report_path, phase, repository_root, "formal report"
        )
        gate_status = _required_non_final_status(
            gate_path, phase, repository_root, "architecture gate"
        )
        if gate_status != report_status:
            raise ProjectStatusConsistencyError(
                f"{phase} has inconsistent non-final status: report is {report_status}, "
                f"architecture gate {_relative(gate_path, repository_root)} is {gate_status}"
            )
        non_completed_reports[phase] = report_status

    orphan_non_completed_gates: dict[str, str] = {}
    for number, gate_path in sorted(gates.items()):
        if number in completed_number_set or number in reports:
            continue
        phase = _canonical_phase_id(number)
        orphan_non_completed_gates[phase] = _required_non_final_status(
            gate_path, phase, repository_root, "architecture gate without a formal report"
        )

    historical_reports = _registered_historical_reports(repository_root)

    return {
        "scope": "repository-local-phase-declarations",
        "current_phase": current_phase,
        "completed_phases": completed_phases,
        "next_phase": next_phase,
        "formal_report_count": len(reports),
        "architecture_gate_count": len(gates),
        "non_completed_reports": non_completed_reports,
        "orphan_non_completed_gates": orphan_non_completed_gates,
        "registered_historical_reports": historical_reports,
        "production_promotion_evaluated": False,
    }


def _load_status(path: Path) -> dict[str, Any]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProjectStatusConsistencyError(f"unable to load project status {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ProjectStatusConsistencyError(f"project status must be a YAML object: {path}")
    return document


def _required_phase(document: dict[str, Any], key: str) -> tuple[str, int]:
    value = document.get(key)
    if not isinstance(value, str):
        raise ProjectStatusConsistencyError(
            f"project status {key} must be a canonical phase id such as phase-01"
        )
    number = _phase_number(value, f"project status {key}")
    return value, number


def _completed_phases(document: dict[str, Any], current_number: int) -> tuple[list[str], list[int]]:
    value = document.get("completed_phases")
    if not isinstance(value, list) or not value:
        raise ProjectStatusConsistencyError(
            "project status completed_phases must be a non-empty list"
        )

    phases: list[str] = []
    numbers: list[int] = []
    for index, item in enumerate(value):
        if not isinstance(item, str):
            raise ProjectStatusConsistencyError(
                f"project status completed_phases[{index}] must be a canonical phase id"
            )
        phases.append(item)
        numbers.append(_phase_number(item, f"project status completed_phases[{index}]"))

    if len(set(numbers)) != len(numbers):
        duplicates = sorted(
            _canonical_phase_id(number) for number in set(numbers) if numbers.count(number) > 1
        )
        raise ProjectStatusConsistencyError(
            "project status completed_phases contains duplicates: " + ", ".join(duplicates)
        )
    if numbers != sorted(numbers):
        raise ProjectStatusConsistencyError(
            "project status completed_phases must be in ascending numeric order"
        )
    if any(number > current_number for number in numbers):
        invalid = [_canonical_phase_id(number) for number in numbers if number > current_number]
        raise ProjectStatusConsistencyError(
            "completed phases cannot be later than current_phase: " + ", ".join(invalid)
        )
    if current_number not in numbers:
        raise ProjectStatusConsistencyError(
            f"current_phase {_canonical_phase_id(current_number)} must also be listed in "
            "completed_phases (the repository's current-phase convention)"
        )
    return phases, numbers


def _next_phase(document: dict[str, Any], current_number: int, completed_numbers: set[int]) -> str:
    value = document.get("next_phase")
    if not isinstance(value, dict):
        raise ProjectStatusConsistencyError("project status next_phase must be an object")

    phase, number = _required_phase(value, "id")
    expected_number = current_number + 1
    if number != expected_number:
        raise ProjectStatusConsistencyError(
            f"project status next_phase.id must be {_canonical_phase_id(expected_number)}, "
            f"got {phase}"
        )
    if number in completed_numbers:
        raise ProjectStatusConsistencyError(
            f"project status next_phase.id {phase} cannot already be completed"
        )
    name = value.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ProjectStatusConsistencyError(
            "project status next_phase.name must be a non-empty string"
        )
    if type(value.get("blocked")) is not bool:
        raise ProjectStatusConsistencyError("project status next_phase.blocked must be a boolean")
    notes = value.get("notes")
    if not isinstance(notes, str) or not notes.strip():
        raise ProjectStatusConsistencyError(
            "project status next_phase.notes must be a non-empty string"
        )
    return phase


def _phase_number(value: str, label: str) -> int:
    match = _PHASE_ID_PATTERN.fullmatch(value)
    if match is None:
        raise ProjectStatusConsistencyError(
            f"{label} must be a canonical phase id such as phase-01; got {value!r}"
        )
    number = int(match.group(1))
    if value != _canonical_phase_id(number):
        raise ProjectStatusConsistencyError(
            f"{label} must use canonical spelling {_canonical_phase_id(number)}; got {value!r}"
        )
    return number


def _formal_reports(root: Path) -> dict[int, Path]:
    directory = root / "docs" / "phases"
    if not directory.is_dir():
        raise ProjectStatusConsistencyError(
            f"formal phase report directory does not exist: {directory}"
        )
    reports: dict[int, Path] = {}
    for path in sorted(directory.glob("PHASE-*-REPORT.md")):
        match = _REPORT_NAME_PATTERN.fullmatch(path.name)
        if match is None:
            raise ProjectStatusConsistencyError(
                f"formal phase report has an invalid filename: {_relative(path, root)}"
            )
        number = _positive_document_phase(match.group(1), path, root)
        previous = reports.get(number)
        if previous is not None:
            raise ProjectStatusConsistencyError(
                f"phase {_canonical_phase_id(number)} has multiple formal reports: "
                f"{_relative(previous, root)}, {_relative(path, root)}"
            )
        reports[number] = path
    return reports


def _architecture_gates(root: Path) -> dict[int, Path]:
    directory = root / "docs" / "architecture"
    if not directory.is_dir():
        raise ProjectStatusConsistencyError(f"architecture directory does not exist: {directory}")
    gates: dict[int, Path] = {}
    for path in sorted(directory.glob("phase-*.md")):
        match = _GATE_NAME_PATTERN.fullmatch(path.name)
        if match is None:
            continue
        number = _positive_document_phase(match.group(1), path, root)
        previous = gates.get(number)
        if previous is not None:
            raise ProjectStatusConsistencyError(
                f"phase {_canonical_phase_id(number)} has multiple architecture gates: "
                f"{_relative(previous, root)}, {_relative(path, root)}"
            )
        gates[number] = path
    return gates


def _positive_document_phase(raw_number: str, path: Path, root: Path) -> int:
    number = int(raw_number)
    if number < 1:
        raise ProjectStatusConsistencyError(
            f"phase document must use a positive phase number: {_relative(path, root)}"
        )
    return number


def _reject_non_final_completed_marker(
    path: Path, phase: str, root: Path, document_kind: str
) -> None:
    declaration = _declared_status(path, root)
    if declaration is not None and declaration[0] in _NON_FINAL_STATUSES:
        status, line = declaration
        raise ProjectStatusConsistencyError(
            f"completed phase {phase} has non-final {document_kind} status {status} at "
            f"{_relative(path, root)}:{line}"
        )


def _required_non_final_status(path: Path, phase: str, root: Path, document_kind: str) -> str:
    declaration = _declared_status(path, root)
    allowed = ", ".join(sorted(_NON_FINAL_STATUSES))
    if declaration is None:
        raise ProjectStatusConsistencyError(
            f"{document_kind} {_relative(path, root)} represents non-completed {phase} and must "
            f"declare one of {allowed} in a preamble Status/状态 field"
        )
    status, line = declaration
    if status not in _NON_FINAL_STATUSES:
        raise ProjectStatusConsistencyError(
            f"{document_kind} {_relative(path, root)}:{line} has invalid non-final status "
            f"{status!r}; expected one of {allowed}"
        )
    return status


def _declared_status(path: Path, root: Path) -> tuple[str, int] | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ProjectStatusConsistencyError(
            f"unable to read phase document {_relative(path, root)}: {exc}"
        ) from exc

    in_fence = False
    for line_number, line in enumerate(lines[:_STATUS_PREAMBLE_LINES], start=1):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _STATUS_LINE_PATTERN.fullmatch(line)
        if match is None:
            continue
        raw_value = match.group(1).lstrip(" \t*_`")
        token_match = _STATUS_TOKEN_PATTERN.match(raw_value)
        if token_match is None:
            invalid = raw_value.split(maxsplit=1)[0] if raw_value else "<empty>"
            return invalid, line_number
        return token_match.group(0), line_number
    return None


def _registered_historical_reports(root: Path) -> dict[str, str]:
    validated: dict[str, str] = {}
    for relative_path, expected_status in _REGISTERED_ROOT_HISTORICAL_REPORTS.items():
        path = root / relative_path
        if not path.exists():
            continue
        if not path.is_file():
            raise ProjectStatusConsistencyError(
                f"registered historical report is not a file: {relative_path}"
            )
        declaration = _declared_status(path, root)
        if declaration is None:
            raise ProjectStatusConsistencyError(
                f"registered historical report {relative_path} must declare "
                f"Status/状态: {expected_status} in its preamble"
            )
        status, line = declaration
        if status != expected_status:
            raise ProjectStatusConsistencyError(
                f"registered historical report {relative_path}:{line} must be "
                f"{expected_status}, got {status!r}"
            )
        validated[relative_path] = status
    return validated


def _canonical_phase_id(number: int) -> str:
    return f"phase-{number:02d}" if number < 10 else f"phase-{number}"


def _report_name(number: int) -> str:
    suffix = f"{number:02d}" if number < 10 else str(number)
    return f"PHASE-{suffix}-REPORT.md"


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)
