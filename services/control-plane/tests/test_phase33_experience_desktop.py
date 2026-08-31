from __future__ import annotations

import json
from pathlib import Path

DESKTOP_ROOT = Path(__file__).resolve().parents[3] / "apps" / "desktop"
FORBIDDEN = (
    "obsion.harness",
    "obsion.db",
    "obsion.capabilities",
    "obsion.model_gateway",
    "sqlalchemy",
    "fastapi",
    'from "obsion"',
)
ELECTRON_IMPORTS = ('from "electron"', "from 'electron'", 'import("electron")')


def test_desktop_is_an_experience_client_not_a_second_harness() -> None:
    violations: list[str] = []
    src = DESKTOP_ROOT / "src"
    for path in sorted(src.glob("*.ts")):
        text = path.read_text(encoding="utf-8")
        for needle in FORBIDDEN:
            if needle in text:
                violations.append(f"{path.name} contains {needle}")
        if path.name not in {"electron-main.ts", "electron.d.ts"} and any(
            token in text for token in ELECTRON_IMPORTS
        ):
            violations.append(f"{path.name} imports electron")
    manifest = json.loads((DESKTOP_ROOT / "package.json").read_text(encoding="utf-8"))
    assert set(manifest["dependencies"]) == {"@obsion/sdk"}
    assert "obsion.token" not in json.dumps(manifest)
    assert "obsion.password" not in json.dumps(manifest)
    assert "electron-main.ts" in {path.name for path in src.glob("*.ts")}
    shell = (src / "shell.ts").read_text(encoding="utf-8")
    assert "App Server" in shell
    assert "Harness" in shell
    assert violations == [], "Desktop crossed the Experience client boundary:\n" + "\n".join(
        violations
    )
