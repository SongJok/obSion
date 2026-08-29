from __future__ import annotations

import ast
import re
from collections import defaultdict
from pathlib import Path

from obsion.db.base import Base

_SOURCE_ROOT = Path(__file__).parents[1] / "src" / "obsion"
_RUNTIME_PROTOCOL_TABLE_MARKERS = (
    "chat",
    "conversation",
    "event",
    "frame",
    "message",
    "notification",
    "packet",
    "stream",
    "trajectory",
)
_REVIEWED_PROTOCOL_TABLES = {
    "events",
    "notification_deliveries",
    "outbox_messages",
    "run_conversation_snapshots",
}
_EVENT_MODELS = {"Event", "OutboxMessage"}
_RAW_EVENT_WRITE = re.compile(
    r"\b(?:insert\s+into|update|delete\s+from)\s+"
    r"(?:public\.)?(?:events|outbox_messages)\b",
    flags=re.IGNORECASE,
)
_CONTROL_NOTIFICATIONS = {
    "run.subscription.completed",
    "run.subscription.error",
    "server.heartbeat",
    "server.pong",
    "server.ready",
    "server.warning",
}


def test_no_second_persisted_runtime_message_model_is_declared() -> None:
    protocol_like_tables = {
        table_name
        for table_name in Base.metadata.tables
        if any(marker in table_name.lower() for marker in _RUNTIME_PROTOCOL_TABLE_MARKERS)
    }

    assert protocol_like_tables == _REVIEWED_PROTOCOL_TABLES


def test_event_and_outbox_writes_are_owned_only_by_event_store() -> None:
    writes: dict[str, list[tuple[str, str]]] = defaultdict(list)
    raw_writes: list[tuple[str, int]] = []

    for path in _SOURCE_ROOT.rglob("*.py"):
        relative_path = path.relative_to(_SOURCE_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        direct_aliases, module_aliases = _event_model_aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                model = _event_model_reference(node.func, direct_aliases, module_aliases)
                if model is not None:
                    writes[model].append((relative_path, "construct"))
                mutation_model = _mutation_model(node, direct_aliases, module_aliases)
                if mutation_model is not None:
                    writes[mutation_model].append((relative_path, "bulk_mutation"))
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and _RAW_EVENT_WRITE.search(node.value)
            ):
                raw_writes.append((relative_path, node.lineno))

    assert dict(writes) == {
        "Event": [("persistence/events.py", "construct")],
        "OutboxMessage": [("persistence/events.py", "construct")],
    }
    assert raw_writes == []


def test_public_live_transports_have_one_event_projection_and_control_plane() -> None:
    sinks: dict[str, list[str]] = defaultdict(list)
    notification_literals: set[str] = set()
    dynamic_notifications: list[str] = []

    for path in _SOURCE_ROOT.rglob("*.py"):
        relative_path = path.relative_to(_SOURCE_ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node.func)
            if name in {"send_bytes", "send_json", "send_text", "StreamingResponse"}:
                sinks[name].append(relative_path)
            if name != "notification" or not node.args:
                continue
            method = node.args[0]
            if isinstance(method, ast.Constant) and isinstance(method.value, str):
                notification_literals.add(method.value)
            else:
                dynamic_notifications.append(ast.unparse(method))

    assert dict(sinks) == {
        "StreamingResponse": ["api/events.py"],
        "send_json": ["app_server/websocket.py"],
    }
    assert notification_literals == _CONTROL_NOTIFICATIONS
    assert dynamic_notifications == ["str(event['name'])"]


def test_event_write_guard_recognizes_supported_import_and_mutation_forms() -> None:
    tree = ast.parse(
        """
from obsion.db.models import Event as RuntimeEvent
import obsion.db.models as model_module
from obsion.db import models as db_models
import obsion.db.models

RuntimeEvent()
model_module.OutboxMessage()
insert(db_models.Event)
obsion.db.models.OutboxMessage.__table__.delete()
"""
    )
    direct_aliases, module_aliases = _event_model_aliases(tree)
    constructions: list[str] = []
    mutations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if model := _event_model_reference(node.func, direct_aliases, module_aliases):
            constructions.append(model)
        if model := _mutation_model(node, direct_aliases, module_aliases):
            mutations.append(model)

    assert sorted(constructions) == ["Event", "OutboxMessage"]
    assert sorted(mutations) == ["Event", "OutboxMessage"]
    assert _RAW_EVENT_WRITE.search("INSERT INTO events (id) VALUES ('1')")
    assert _RAW_EVENT_WRITE.search("UPDATE public.outbox_messages SET attempt_count = 1")
    assert _RAW_EVENT_WRITE.search("DELETE FROM events WHERE id = '1'")


def _event_model_aliases(tree: ast.Module) -> tuple[dict[str, str], set[str]]:
    direct_aliases: dict[str, str] = {}
    module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "obsion.db.models":
            for alias in node.names:
                if alias.name in _EVENT_MODELS:
                    direct_aliases[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "obsion.db.models":
                    module_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "obsion.db":
            for alias in node.names:
                if alias.name == "models":
                    module_aliases.add(alias.asname or alias.name)
    return direct_aliases, module_aliases


def _mutation_model(
    call: ast.Call,
    direct_aliases: dict[str, str],
    module_aliases: set[str],
) -> str | None:
    if _call_name(call.func) in {"delete", "insert", "update"} and call.args:
        return _event_model_reference(call.args[0], direct_aliases, module_aliases)
    if not isinstance(call.func, ast.Attribute) or call.func.attr not in {
        "delete",
        "insert",
        "update",
    }:
        return None
    receiver = call.func.value
    if isinstance(receiver, ast.Attribute) and receiver.attr == "__table__":
        receiver = receiver.value
    return _event_model_reference(receiver, direct_aliases, module_aliases)


def _event_model_reference(
    node: ast.expr,
    direct_aliases: dict[str, str],
    module_aliases: set[str],
) -> str | None:
    if isinstance(node, ast.Name):
        return direct_aliases.get(node.id)
    dotted = _dotted_name(node)
    for module_alias in module_aliases:
        prefix = f"{module_alias}."
        if dotted.startswith(prefix) and dotted.removeprefix(prefix) in _EVENT_MODELS:
            return dotted.removeprefix(prefix)
    return None


def _dotted_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _call_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""
