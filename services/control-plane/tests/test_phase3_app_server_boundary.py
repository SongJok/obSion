from __future__ import annotations

import ast
from pathlib import Path

APP_SERVER_ROOT = Path(__file__).parents[1] / "src" / "obsion" / "app_server"
FORBIDDEN_IMPORT_PREFIXES = (
    "sqlalchemy",
    "obsion.db",
    "obsion.harness",
    "obsion.model_gateway",
    "obsion.persistence",
)


def _imports(tree: ast.AST) -> list[tuple[str, int]]:
    imports: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append((node.module, node.lineno))
    return imports


def test_app_server_transport_has_no_database_or_model_dependency() -> None:
    violations: list[str] = []
    for path in sorted(APP_SERVER_ROOT.glob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for imported, line in _imports(tree):
            if imported.startswith(FORBIDDEN_IMPORT_PREFIXES):
                violations.append(f"{path.name}:{line} imports {imported}")

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"sessions", "begin", "execute", "scalar", "scalars"}
            ):
                violations.append(
                    f"{path.name}:{node.lineno} calls persistence primitive {node.func.attr}"
                )
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"Database", "EventStore", "ModelGateway"}
            ):
                violations.append(f"{path.name}:{node.lineno} constructs forbidden {node.func.id}")

    assert violations == [], "App Server transport crossed its application boundary:\n" + "\n".join(
        violations
    )


def test_app_server_transport_delegates_through_application_facade() -> None:
    dispatcher = (APP_SERVER_ROOT / "dispatcher.py").read_text(encoding="utf-8")
    websocket = (APP_SERVER_ROOT / "websocket.py").read_text(encoding="utf-8")

    assert "AppServerApplication" in dispatcher
    assert "AppServerApplication" in websocket
    assert "self.application" in dispatcher
    assert "self.application" in websocket
