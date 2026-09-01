"""Phase 89: typed Evidence views.

Static boundary tests pinning the typed Evidence rendering contract:
dispatch on the persisted envelope shape (events[] / items[] / columns+rows /
plan / hits / document text), never inventing fields, keeping the generic
JSON fallback, and surfacing the full persisted metadata ledger.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from obsion.release.notes import validate_release_notes

ROOT = Path(__file__).resolve().parents[3]
WEB = ROOT / "apps" / "web" / "src"


def _read(relative: str) -> str:
    return (WEB / relative).read_text(encoding="utf-8")


def test_classifier_dispatches_on_persisted_envelopes() -> None:
    lib = _read("lib/typed-evidence.ts")
    # Dispatch keys mirror the control-plane normalized envelopes exactly.
    assert "content.events" in lib
    assert "content.items" in lib
    assert "content.columns" in lib and "content.rows" in lib
    assert "content.plan" in lib and "content.validation" in lib
    assert "content.hits" in lib
    assert "content.text" in lib
    # CODE items get a dedicated symbol view; everything else stays generic.
    assert 'evidenceType === "CODE"' in lib
    assert '"generic"' in lib


def test_classifier_never_invents_fields() -> None:
    lib = _read("lib/typed-evidence.ts")
    # Every accessor is a type guard; no defaults fabricate payload content.
    assert "function isRecord(" in lib
    assert "function asString(" in lib
    assert "function asNumber(" in lib
    assert "MAX_TABLE_ROWS" in lib
    assert "MAX_LIST_ENTRIES" in lib


def test_typed_renderers_cover_goal_evidence_panel_types() -> None:
    view = _read("components/evidence-content.tsx")
    # goal.txt section 57: Metric / Log / Deployment / Git Diff / Config Diff.
    assert "ObservabilityEvents" in view
    assert "ChangeItems" in view
    assert '"git.diff"' in view and '"config.diff"' in view
    assert "diff-view" in view
    assert "config-diff" in view
    assert "CodeItems" in view
    assert "DataTable" in view
    assert "ExplainPlan" in view
    assert "KnowledgeHits" in view
    assert "DocumentText" in view
    assert "RawJson" in view


def test_generic_fallback_is_preserved_for_unknown_payloads() -> None:
    view = _read("components/evidence-content.tsx")
    assert "default:" in view
    assert "JSON.stringify(content, null, 2)" in view


def test_evidence_meta_surfaces_full_persisted_ledger() -> None:
    view = _read("components/evidence-content.tsx")
    for field in (
        "observed_at",
        "ingested_at",
        "confidence",
        "classification",
        "permissions",
        "content_fingerprint",
        "step_id",
        "run_id",
        "lineage",
    ):
        assert f"evidence.{field}" in view


def test_evidence_type_matches_rest_projection() -> None:
    types = _read("lib/types.ts")
    assert "run_id: string;" in types
    assert "step_id: string | null;" in types
    assert "ingested_at: string;" in types
    assert "run_id?: string;" not in types


def test_inspector_uses_typed_content_and_meta() -> None:
    inspector = _read("components/runtime-inspector.tsx")
    assert "EvidenceContent" in inspector
    assert "EvidenceMeta" in inspector
    assert "evidence-detail-body" in inspector
    # The raw JSON pre is no longer the inspector's primary detail body.
    assert "JSON.stringify(selectedEvidence.content" not in inspector


def test_workspace_evidence_view_uses_typed_content() -> None:
    view = _read("components/evidence-view.tsx")
    assert "EvidenceContent" in view
    assert "EvidenceMeta" in view
    # run_id is always persisted; the "no Run" branch must not render.
    assert "无 Run" not in view


def test_typed_evidence_styles_exist() -> None:
    styles = _read("app/globals.css")
    for selector in (
        ".typed-evidence",
        ".ev-events",
        ".ev-items",
        ".ev-code-items",
        ".ev-table",
        ".diff-view",
        ".config-diff",
        ".evidence-meta",
        ".evidence-raw-json",
        ".evidence-detail-body",
    ):
        assert selector in styles


def test_release_notes_and_project_status_track_phase89() -> None:
    result = validate_release_notes(ROOT / "docs" / "release" / "0.89.0-dev.yaml", ROOT)
    assert result["version"] == "0.89.0-dev"
    status = yaml.safe_load((ROOT / "docs" / "project-status.yaml").read_text(encoding="utf-8"))
    assert status["version"] == "0.93.0-dev"
    assert status["current_phase"] == "phase-93"
    assert "phase-89" in status["completed_phases"]
