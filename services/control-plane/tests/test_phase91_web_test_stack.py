"""Phase 91: JavaScript component-test stack for apps/web.

Static boundary tests pinning the ADR 0067-deferred decision: apps/web
now has a real vitest + Testing Library suite covering the pure logic
(typed Evidence classifier, citation helpers, API normalization) and the
typed Evidence renderers, wired into the root npm test fan-out that both
`make test` and CI already run.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from obsion.release.notes import validate_release_notes

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps" / "web"


def _read(relative: str) -> str:
    return (WEB / relative).read_text(encoding="utf-8")


def test_vitest_stack_is_pinned_and_scoped() -> None:
    package = _read("package.json")
    assert '"test": "vitest run"' in package
    assert '"vitest"' in package
    assert '"@testing-library/react"' in package
    assert '"jsdom"' in package
    config = _read("vitest.config.mts")
    assert "tests/**/*.test.{ts,tsx}" in config
    assert '"@":' in config
    assert "globals: false" in config


def test_classifier_suite_covers_every_envelope_and_fallback() -> None:
    suite = _read("tests/typed-evidence.test.ts")
    for kind in (
        "events",
        "items",
        "code-items",
        "data-table",
        "explain-plan",
        "knowledge-hits",
        "document-text",
        "generic",
    ):
        assert f'"{kind}"' in suite
    assert "observabilityEventView" in suite
    assert "changeItemView" in suite
    assert "codeItemView" in suite
    assert "formatAttributeValue" in suite


def test_citation_suite_pins_no_invention_behavior() -> None:
    suite = _read("tests/knowledge-citation.test.ts")
    assert "hitsFromEvidenceContent" in suite
    assert "provenanceEntries" in suite
    assert "citationLabel" in suite
    assert "knowledge · 授权文档" in suite


def test_api_suite_pins_error_normalization_and_session_transport() -> None:
    suite = _read("tests/api.test.ts")
    for code in (
        "request_timeout",
        "request_cancelled",
        "network_error",
        "invalid_response",
        "capability_denied",
    ):
        assert f'"{code}"' in suite
    assert '"include"' in suite
    assert '"no-store"' in suite


def test_component_suite_renders_every_typed_view() -> None:
    suite = _read("tests/evidence-content.test.tsx")
    for marker in (
        "diff-view",
        "变更前",
        "getByRole",
        "src/payments/service.py:42-87",
        "引用溯源",
        "evidence-raw-json",
        "EvidenceMeta",
        "产生步骤",
    ):
        assert marker in suite


def test_root_npm_test_fans_out_to_web() -> None:
    root = (ROOT / "package.json").read_text(encoding="utf-8")
    assert "npm run test --workspaces --if-present" in root
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "npm test" in workflow


def test_release_notes_and_project_status_track_phase91() -> None:
    result = validate_release_notes(ROOT / "docs" / "release" / "0.91.0-dev.yaml", ROOT)
    assert result["version"] == "0.91.0-dev"
    status = yaml.safe_load((ROOT / "docs" / "project-status.yaml").read_text(encoding="utf-8"))
    assert status["version"] == "0.94.0-dev"
    assert status["current_phase"] == "phase-94"
    assert "phase-91" in status["completed_phases"]
