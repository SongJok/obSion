"""Content-only runtime observations, never a signed deployment attestation."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from obsion.config import Settings


@dataclass(frozen=True)
class PackageSnapshot:
    sha256: str
    files: int


def snapshot_package(root: Path) -> PackageSnapshot:
    """Hash Python and JSON package resources without reading configuration/secrets.

    The same algorithm is used against a reviewed checkout and an installed wheel.
    Paths in the digest are relative, and no paths or file bodies leave this helper.
    """
    if not root.is_dir() or root.is_symlink():
        raise ValueError("package_snapshot_unavailable")
    hashes: dict[str, str] = {}
    total = 0
    for path in sorted(root.rglob("*")):
        if "__pycache__" in path.parts:
            continue
        if path.is_symlink():
            raise ValueError("package_snapshot_symlink")
        if not path.is_file() or path.suffix not in {".py", ".json"}:
            continue
        size = path.stat().st_size
        total += size
        if len(hashes) >= 4096 or size > 8 * 1024 * 1024 or total > 64 * 1024 * 1024:
            raise ValueError("package_snapshot_exceeds_limit")
        content = path.read_bytes()
        if len(content) != size:
            raise ValueError("package_snapshot_changed")
        hashes[path.relative_to(root).as_posix()] = hashlib.sha256(content).hexdigest()
    if "__init__.py" not in hashes:
        raise ValueError("package_snapshot_unavailable")
    encoded = json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    return PackageSnapshot(hashlib.sha256(encoded).hexdigest(), len(hashes))


@dataclass(frozen=True)
class ExecutionIdentity:
    runtime_instance_id: str
    revision: str | None
    image_digest: str | None
    package: PackageSnapshot | None

    @classmethod
    def observe(cls, settings: Settings) -> ExecutionIdentity:
        try:
            package = snapshot_package(Path(__file__).resolve().parents[1])
        except (OSError, ValueError):
            # Normal tasks may run in a source-less distribution. Acceptance
            # must distinguish unavailable observation from a matching build.
            package = None
        return cls(str(uuid4()), settings.release_revision, settings.release_image_digest, package)

    def event_payload(self) -> dict[str, str | int | bool | None]:
        return {
            "execution_id": str(uuid4()),
            "runtime_instance_id": self.runtime_instance_id,
            "revision": self.revision,
            "image_digest": self.image_digest,
            "package_sha256": self.package.sha256 if self.package else None,
            "package_files": self.package.files if self.package else 0,
            "observation": "installed_package_at_runtime_initialization",
            "signature_verified": False,
        }
