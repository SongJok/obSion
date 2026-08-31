from __future__ import annotations

import json
from pathlib import Path

IDE_ROOT = Path(__file__).resolve().parents[3] / "apps" / "ide-extension"
FORBIDDEN = (
    "obsion.harness",
    "obsion.db",
    "obsion.capabilities",
    "obsion.model_gateway",
    "sqlalchemy",
    "fastapi",
    'from "obsion"',
)
VSCODE_IMPORTS = ('from "vscode"', "from 'vscode'", "import * as vscode")


def test_ide_extension_is_an_experience_client_not_a_second_harness() -> None:
    violations: list[str] = []
    src = IDE_ROOT / "src"
    for path in sorted(src.glob("*.ts")):
        text = path.read_text(encoding="utf-8")
        for needle in FORBIDDEN:
            if needle in text:
                violations.append(f"{path.name} contains {needle}")
        if path.name != "extension.ts" and any(token in text for token in VSCODE_IMPORTS):
            violations.append(f"{path.name} imports vscode")
    manifest = json.loads((IDE_ROOT / "package.json").read_text(encoding="utf-8"))
    properties = manifest["contributes"]["configuration"]["properties"]
    assert set(properties) == {"obsion.baseUrl", "obsion.protocol"}
    assert set(manifest["dependencies"]) == {"@obsion/sdk"}
    assert "obsion.token" not in json.dumps(manifest)
    assert "obsion.password" not in json.dumps(manifest)
    assert violations == [], "IDE crossed the Experience client boundary:\n" + "\n".join(violations)
