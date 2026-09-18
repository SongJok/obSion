from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZipFile

from obsion.harness.execution_identity import snapshot_package

_REPOSITORY_ROOT = Path(__file__).parents[3]
_CONTROL_PLANE_ROOT = _REPOSITORY_ROOT / "services" / "control-plane"
_EVENT_CONTRACT_ROOT = _CONTROL_PLANE_ROOT / "src" / "obsion" / "contracts" / "events"


def test_wheel_contains_every_frozen_contract_resource() -> None:
    registry = json.loads((_EVENT_CONTRACT_ROOT / "registry.json").read_text(encoding="utf-8"))
    expected_payloads = {
        f"obsion/contracts/events/{version['payload_schema']}"
        for event in registry["events"]
        for version in event["versions"]
    }
    expected_resources = expected_payloads | {
        "obsion/contracts/errors/catalog.json",
        "obsion/contracts/errors/catalog.schema.json",
        "obsion/contracts/events/envelope.v1.schema.json",
        "obsion/contracts/events/registry.json",
        "obsion/contracts/events/registry.schema.json",
    }

    with TemporaryDirectory() as output_directory:
        uv = shutil.which("uv")
        assert uv is not None
        subprocess.run(  # noqa: S603 -- fixed build command; no untrusted input
            [
                uv,
                "build",
                "--package",
                "obsion-control-plane",
                "--wheel",
                "--no-build-isolation",
                "--offline",
                "--out-dir",
                output_directory,
            ],
            cwd=_REPOSITORY_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        wheels = list(Path(output_directory).glob("*.whl"))
        assert len(wheels) == 1
        with ZipFile(wheels[0]) as archive:
            packaged_resources = {
                name
                for name in archive.namelist()
                if name.startswith("obsion/contracts/") and name.endswith(".json")
            }
            package_hashes = {
                name.removeprefix("obsion/"): hashlib.sha256(archive.read(name)).hexdigest()
                for name in archive.namelist()
                if name.startswith("obsion/") and Path(name).suffix in {".py", ".json"}
            }

    assert packaged_resources == expected_resources
    wheel_digest = hashlib.sha256(
        json.dumps(package_hashes, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    reviewed_package = snapshot_package(_CONTROL_PLANE_ROOT / "src" / "obsion")
    assert wheel_digest == reviewed_package.sha256
    assert len(package_hashes) == reviewed_package.files
