from __future__ import annotations

import ast
from pathlib import Path

CLI_ROOT = Path(__file__).resolve().parents[1] / "src" / "obsion_cli"
FORBIDDEN_PREFIXES = (
    "obsion.harness",
    "obsion.db",
    "obsion.capabilities",
    "obsion.model_gateway",
    "obsion.persistence",
    "obsion.api",
    "sqlalchemy",
    "fastapi",
)


def _imports(tree: ast.AST) -> list[tuple[str, int]]:
    imports: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.append((node.module, node.lineno))
    return imports


def test_cli_is_an_experience_client_not_a_second_harness() -> None:
    violations: list[str] = []
    for path in sorted(CLI_ROOT.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for imported, line in _imports(tree):
            if imported == "obsion" or imported.startswith("obsion."):
                violations.append(f"{path.name}:{line} imports control-plane module {imported}")
            if imported.startswith(FORBIDDEN_PREFIXES):
                violations.append(f"{path.name}:{line} imports {imported}")
    assert violations == [], "CLI crossed the Experience client boundary:\n" + "\n".join(violations)
