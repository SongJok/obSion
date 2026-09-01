from __future__ import annotations

from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web"


def test_knowledge_citation_helpers_never_invent_fields() -> None:
    helper = (WEB_ROOT / "src" / "lib" / "knowledge-citation.ts").read_text(encoding="utf-8")
    assert "Never invent missing fields" in helper or "never invent" in helper.lower()
    assert "provenanceEntries" in helper
    assert "hitsFromEvidenceContent" in helper
    assert "connector_name" in helper
    assert "external_id" in helper
    assert "revision_id" in helper
    assert "operation" in helper


def test_knowledge_view_renders_provenance_component() -> None:
    view = (WEB_ROOT / "src" / "components" / "knowledge-view.tsx").read_text(encoding="utf-8")
    assert "KnowledgeProvenance" in view
    assert "含溯源引用" in view or "溯源" in view
    assert "企微" in view


def test_runtime_inspector_surfaces_document_citations() -> None:
    # Phase 89 moved the citation renderer into the shared typed Evidence
    # content component; the inspector consumes it via EvidenceContent.
    inspector = (WEB_ROOT / "src" / "components" / "runtime-inspector.tsx").read_text(
        encoding="utf-8"
    )
    assert "EvidenceContent" in inspector
    content = (WEB_ROOT / "src" / "components" / "evidence-content.tsx").read_text(encoding="utf-8")
    assert "KnowledgeHits" in content
    assert "hitsFromEvidenceContent" in content
    assert "KnowledgeProvenance" in content


def test_provenance_component_fail_closed_copy() -> None:
    component = (WEB_ROOT / "src" / "components" / "knowledge-provenance.tsx").read_text(
        encoding="utf-8"
    )
    assert "不编造" in component
    assert "citation-provenance" in component
