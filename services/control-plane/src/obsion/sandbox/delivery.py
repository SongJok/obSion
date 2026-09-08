"""确定性补丁与未验证交付清单，不把命令退出码等同于项目修复成功。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import UUID

from obsion.release.hardening import scan_secret_text
from obsion.sandbox.contracts import SandboxCommand
from obsion.sandbox.project import MAX_PROJECT_BYTES, ProjectRejected, ProjectSnapshot

MAX_PATCH_BYTES = MAX_PROJECT_BYTES * 2 + 1048576


def _quoted_path(prefix: str, path: str) -> str:
    # Git 使用 UTF-8 字节的 C 风格八进制转义，不支持 JSON 的 \u 转义。
    raw = (prefix + path).encode("utf-8")
    return (
        '"'
        + "".join(
            chr(value) if 32 <= value < 127 and value not in {34, 92} else f"\\{value:03o}"
            for value in raw
        )
        + '"'
    )


def _lines(content: bytes) -> list[bytes]:
    if not content:
        return []
    parts = content.split(b"\n")
    lines = [part + b"\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def create_patch(before: ProjectSnapshot, after: ProjectSnapshot) -> bytes:
    if before.revision != after.revision:
        raise ProjectRejected("project_revision_mismatch")
    old_files = {item.path: item for item in before.files}
    new_files = {item.path: item for item in after.files}
    buffer = bytearray()

    def append(value: bytes) -> None:
        if len(buffer) + len(value) > MAX_PATCH_BYTES:
            raise ProjectRejected("project_patch_size_exceeded")
        buffer.extend(value)

    for path in sorted(old_files.keys() | new_files.keys()):
        old, new = old_files.get(path), new_files.get(path)
        if old == new:
            continue
        a, b = _quoted_path("a/", path), _quoted_path("b/", path)
        append(f"diff --git {a} {b}\n".encode())
        old_mode = "100755" if old and old.executable else "100644"
        new_mode = "100755" if new and new.executable else "100644"
        if old is None:
            append(f"new file mode {new_mode}\n".encode())
        elif new is None:
            append(f"deleted file mode {old_mode}\n".encode())
        elif old_mode != new_mode:
            append(f"old mode {old_mode}\nnew mode {new_mode}\n".encode())
        old_content = old.content if old else b""
        new_content = new.content if new else b""
        if old_content == new_content:
            continue
        append(f"--- {a if old else '/dev/null'}\n+++ {b if new else '/dev/null'}\n".encode())
        old_lines, new_lines = _lines(old_content), _lines(new_content)
        # 整文件替换保持线性复杂度，避免对不可信重复内容执行最坏二次复杂度 LCS。
        append(
            f"@@ -{1 if old_lines else 0},{len(old_lines)} "
            f"+{1 if new_lines else 0},{len(new_lines)} @@\n".encode()
        )
        for prefix, lines in ((b"-", old_lines), (b"+", new_lines)):
            for line in lines:
                append(prefix + line)
                if not line.endswith(b"\n"):
                    append(b"\n\\ No newline at end of file\n")
    return bytes(buffer)


@dataclass(frozen=True, slots=True)
class CommandObservation:
    """可信执行边界传入的观测引用；本类不证明来自真实 Kubernetes。"""

    step_id: UUID
    command: SandboxCommand
    outcome: Literal["exited", "timeout", "cancelled", "unknown"]
    exit_code: int | None
    evidence_id: UUID

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, UUID) or value.int == 0
            for value in (self.step_id, self.evidence_id)
        ):
            raise ProjectRejected("project_command_reference_invalid")
        if not isinstance(self.command, SandboxCommand) or self.outcome not in {
            "exited",
            "timeout",
            "cancelled",
            "unknown",
        }:
            raise ProjectRejected("project_command_observation_invalid")
        if any(scan_secret_text(argument, path="command") for argument in self.command.argv):
            raise ProjectRejected("project_secret_detected")
        if self.outcome == "unknown":
            if self.exit_code is not None:
                raise ProjectRejected("project_command_observation_invalid")
        elif type(self.exit_code) is not int or not 0 <= self.exit_code <= 255:
            raise ProjectRejected("project_command_observation_invalid")
        if (self.outcome == "timeout" and self.exit_code != 124) or (
            self.outcome == "cancelled" and self.exit_code != 143
        ):
            raise ProjectRejected("project_command_observation_invalid")

    def to_wire(self) -> dict[str, Any]:
        return {
            "step_id": str(self.step_id),
            "command": self.command.to_wire(),
            "outcome": self.outcome,
            "exit_code": self.exit_code,
            "evidence_id": str(self.evidence_id),
        }


@dataclass(frozen=True, slots=True)
class ProjectDelivery:
    base: ProjectSnapshot = field(repr=False)
    result: ProjectSnapshot = field(repr=False)
    observations: tuple[CommandObservation, ...]
    patch: bytes = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.observations, tuple)
            or len(self.observations) > 40
            or any(not isinstance(item, CommandObservation) for item in self.observations)
        ):
            raise ProjectRejected("project_command_observation_invalid")
        if len({item.step_id for item in self.observations}) != len(self.observations):
            raise ProjectRejected("project_command_observation_duplicate")
        object.__setattr__(self, "patch", create_patch(self.base, self.result))

    def manifest(self) -> dict[str, Any]:
        old_files = {item.path: item for item in self.base.files}
        new_files = {item.path: item for item in self.result.files}
        changes = []
        for path in sorted(old_files.keys() | new_files.keys()):
            old, new = old_files.get(path), new_files.get(path)
            if old != new:
                changes.append(
                    {
                        "path": path,
                        "before": old.metadata() if old else None,
                        "after": new.metadata() if new else None,
                    }
                )
        return {
            "version": 1,
            "revision": self.base.revision.to_wire(),
            "base_fingerprint": self.base.fingerprint,
            "result_fingerprint": self.result.fingerprint,
            "patch_sha256": hashlib.sha256(self.patch).hexdigest(),
            "patch_bytes": len(self.patch),
            "changes": changes,
            "commands": [item.to_wire() for item in self.observations],
            "verification": "NOT_EVALUATED",
            "unresolved": ["independent_verification_required"],
        }
