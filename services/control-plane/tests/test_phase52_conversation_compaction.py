from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

from obsion.model_gateway.compaction import ConversationCompactor, ConversationTurn

_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "obsion"
WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web"


def _turn(ordinal: int, user: str, assistant: str | None = None) -> ConversationTurn:
    return ConversationTurn(
        ordinal=ordinal,
        source_turn_id=uuid4(),
        source_principal_id=uuid4(),
        user_content=user,
        assistant_content=assistant,
    )


def test_recent_turns_stay_verbatim_and_older_turns_are_extractive() -> None:
    older = [_turn(1, "first question", "first answer"), _turn(2, "second question")]
    recent = [_turn(3, "third question", "third answer"), _turn(4, "fourth question")]
    compacted = ConversationCompactor(keep_recent=2).compact([*older, *recent])
    assert compacted.method == "extractive"
    assert compacted.kept_turns == 2
    assert compacted.summarized_turns == 2
    assert [item.ordinal for item in compacted.recent] == [3, 4]
    assert compacted.summary_segment is not None
    assert compacted.summary_segment.source == "conversation-compact"
    assert compacted.summary_segment.trust.value == "UNTRUSTED_DATA"
    assert "first question" in compacted.summary_segment.content
    assert "first answer" not in [item.user_content for item in compacted.recent]
    identity = ConversationCompactor(keep_recent=2).compact(recent)
    assert identity.summary_segment is None
    assert identity.summarized_turns == 0
    assert identity.as_dict()["method"] == "extractive"


def test_compactor_is_not_a_model_loop() -> None:
    source = (_SOURCE_ROOT / "model_gateway" / "compaction.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert "obsion.model_gateway.gateway" not in imports
    assert "httpx" not in imports
    assert "openai" not in imports
    assert "eval(" not in source
    assert "complete(" not in source
    harness = (_SOURCE_ROOT / "harness" / "runtime.py").read_text(encoding="utf-8")
    assert "ConversationCompactor" in harness
    assert "_conversation_segments" in harness
    inspector = (WEB_ROOT / "src" / "components" / "runtime-inspector.tsx").read_text(
        encoding="utf-8"
    )
    assert "抽取式会话压缩" in inspector
    assert "conversation_compact" in inspector
