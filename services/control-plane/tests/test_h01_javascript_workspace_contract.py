from __future__ import annotations

import json
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_root_javascript_commands_build_the_shared_sdk_before_consumers() -> None:
    """A clean checkout has no ignored SDK dist directory.

    Every aggregate command must therefore materialize the shared SDK before
    TypeScript resolves the desktop, IDE, or web workspace imports.  Keeping
    this contract at the root also makes each CI command independently usable.
    """

    manifest = json.loads((REPOSITORY_ROOT / "package.json").read_text(encoding="utf-8"))
    scripts = manifest["scripts"]

    assert scripts["build:sdk"] == "npm run build --workspace @obsion/sdk"
    for command in ("build", "lint", "typecheck", "test"):
        assert scripts[f"pre{command}"] == "npm run build:sdk"

    sdk_manifest = json.loads(
        (REPOSITORY_ROOT / "packages" / "sdk-ts" / "package.json").read_text(encoding="utf-8")
    )
    assert sdk_manifest["main"] == "dist/index.js"
    assert sdk_manifest["types"] == "dist/index.d.ts"
    assert sdk_manifest["scripts"]["build"] == "tsc -p tsconfig.json"
