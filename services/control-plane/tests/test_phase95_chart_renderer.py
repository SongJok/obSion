"""Phase 95: schema-driven chart renderer.

The Harness emits a Vega-Lite v5 subset for CHART artifacts; until now the
Workbench rendered every mark as horizontal bars, so temporal line charts
and single-number text marks lost their shape. Producer tests pin the
_chart_contract marks and encodings the renderer now honours; static tests
pin the Web wiring: parseChartSpec, the line geometry helper, the big-number
mark, and the SVG renderer in the artifact preview.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from obsion.harness.runtime import HarnessRuntime
from obsion.release.notes import validate_release_notes

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps" / "web"


def _read(relative: str) -> str:
    return (WEB / relative).read_text(encoding="utf-8")


def test_chart_contract_marks_temporal_series_as_line() -> None:
    contract = HarnessRuntime._chart_contract(
        ["day", "success_rate"],
        [
            {"day": "2026-08-30", "success_rate": "97.2"},
            {"day": "2026-08-31", "success_rate": "96.8"},
        ],
    )
    assert contract is not None
    assert contract["mark"]["type"] == "line"
    assert contract["mark"]["point"] is True
    assert contract["encoding"]["x"] == {"field": "day", "type": "temporal", "sort": None}
    assert contract["encoding"]["y"] == {"field": "success_rate", "type": "quantitative"}
    values = contract["data"]["values"]
    assert values[0]["success_rate"] == 97.2  # normalized to float


def test_chart_contract_marks_nominal_series_as_bar() -> None:
    contract = HarnessRuntime._chart_contract(
        ["channel", "total"],
        [
            {"channel": "web", "total": 12},
            {"channel": "app", "total": 30},
        ],
    )
    assert contract is not None
    assert contract["mark"]["type"] == "bar"
    assert contract["encoding"]["x"]["type"] == "nominal"
    assert contract["encoding"]["y"]["field"] == "total"


def test_chart_contract_marks_single_number_as_text() -> None:
    contract = HarnessRuntime._chart_contract(["total"], [{"total": 12345}])
    assert contract is not None
    assert contract["mark"]["type"] == "text"
    assert "x" not in contract["encoding"]
    assert contract["encoding"]["text"]["field"] == "total"


def test_chart_contract_fails_closed_without_numeric_values() -> None:
    assert HarnessRuntime._chart_contract(["name"], [{"name": "alice"}]) is None
    assert HarnessRuntime._chart_contract([], []) is None


def test_chart_spec_helper_covers_the_emitted_subset() -> None:
    helpers = _read("src/lib/chart-spec.ts")
    for marker in (
        "parseChartSpec",
        "buildLineGeometry",
        "formatTick",
        "MAX_BAR_POINTS",
        "MAX_LINE_POINTS",
        'SUPPORTED_MARKS = new Set<ChartMark>(["bar", "line", "text"])',
        'temporal = xType === "temporal"',
    ):
        assert marker in helpers
    types = _read("src/lib/types.ts")
    assert "mark?: string | { type?: string; point?: boolean; tooltip?: boolean };" in types


def test_artifact_preview_renders_each_mark() -> None:
    preview = _read("src/components/artifact-preview.tsx")
    for marker in (
        "parseChartSpec(artifact.inline_content)",
        'chart.mark === "text"',
        'chart.mark === "line"',
        "ChartText",
        "ChartLine",
        "ChartBars",
        "buildLineGeometry(chart.points",
        "chart-line-path",
        "chart-big-number",
        "formatTick(tick)",
    ):
        assert marker in preview
    styles = _read("src/app/globals.css")
    for marker in (".chart-line-path", ".chart-line-dot", ".chart-big-number", ".chart-tick"):
        assert marker in styles


def test_web_behaviour_suite_covers_chart_parsing() -> None:
    suite = _read("tests/chart-spec.test.ts")
    for marker in (
        "parseChartSpec",
        "buildLineGeometry",
        "formatTick",
        "MAX_LINE_POINTS",
        "temporal",
    ):
        assert marker in suite


def test_release_notes_and_project_status_track_phase95() -> None:
    result = validate_release_notes(ROOT / "docs" / "release" / "0.95.0-dev.yaml", ROOT)
    assert result["version"] == "0.95.0-dev"
    status = yaml.safe_load((ROOT / "docs" / "project-status.yaml").read_text(encoding="utf-8"))
    assert status["version"] == "0.96.0-dev"
    assert status["current_phase"] == "phase-96"
    assert "phase-94" in status["completed_phases"]
