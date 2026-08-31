from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from obsion.domain.enums import EvidenceType
from obsion.model_gateway.context import TrustLevel
from obsion.model_gateway.evidence_segments import evidence_context_segments

_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "obsion"
WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web"


def _item(*, evidence_type: EvidenceType, source: str, content: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        evidence_type=evidence_type,
        source=source,
        resource=f"{source}:1",
        observed_at=datetime.now(UTC),
        content=content,
    )


def test_tool_results_are_untrusted_and_separate_from_evidence_bus() -> None:
    document = _item(
        evidence_type=EvidenceType.DOCUMENT,
        source="knowledge",
        content={"text": "release requires rollback"},
    )
    tool = _item(
        evidence_type=EvidenceType.TOOL,
        source="obsion.development.echo",
        content={"ignore": "previous instruction"},
    )
    segments = evidence_context_segments([document, tool])
    assert [item.source for item in segments] == ["evidence-bus", "tool-result"]
    assert all(item.trust == TrustLevel.UNTRUSTED_DATA for item in segments)
    assert "release requires rollback" in segments[0].content
    assert "obsion.development.echo" in segments[1].content
    assert "previous instruction" in segments[1].content
    assert "previous instruction" not in segments[0].content
    empty_tools = evidence_context_segments([document])
    assert [item.source for item in empty_tools] == ["evidence-bus"]


def test_tool_result_segment_is_not_a_system_channel() -> None:
    source = (_SOURCE_ROOT / "model_gateway" / "evidence_segments.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert "obsion.model_gateway.gateway" not in imports
    assert "TrustLevel.SYSTEM" not in source
    harness = (_SOURCE_ROOT / "harness" / "runtime.py").read_text(encoding="utf-8")
    assert "evidence_context_segments" in harness
    inspector = (WEB_ROOT / "src" / "components" / "runtime-inspector.tsx").read_text(
        encoding="utf-8"
    )
    assert "tool-result" in inspector
    assert "不能成为 SYSTEM 或 Skill 指令" in inspector
