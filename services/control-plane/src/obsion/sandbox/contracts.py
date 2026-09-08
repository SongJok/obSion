"""单次隔离命令的无凭据契约；命令字符串过滤不承担隔离职责。"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import PurePosixPath
from typing import Any

MAX_COMMAND_JSON_BYTES = 65536


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    cpu_millis: int = 2000
    memory_mib: int = 4096
    disk_mib: int = 10240
    processes: int = 128
    timeout_seconds: int = 120

    def __post_init__(self) -> None:
        for name, maximum in (
            ("cpu_millis", 2000),
            ("memory_mib", 4096),
            ("disk_mib", 10240),
            ("processes", 128),
            ("timeout_seconds", 600),
        ):
            value = getattr(self, name)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"limits.{name} must be an integer in [1, {maximum}]")


@dataclass(frozen=True, slots=True)
class SandboxCommand:
    executable: str
    argv: tuple[str, ...] = ()
    cwd: str = "/workspace"
    limits: SandboxLimits = field(default_factory=SandboxLimits)

    def __post_init__(self) -> None:
        if (
            not isinstance(self.executable, str)
            or not self.executable.startswith("/")
            or "\x00" in self.executable
            or len(self.executable) > 4096
        ):
            raise ValueError("executable must be an absolute, NUL-free path")
        if not isinstance(self.argv, tuple) or len(self.argv) > 256:
            raise ValueError("argv must be a tuple with at most 256 arguments")
        if any(not isinstance(arg, str) or "\x00" in arg for arg in self.argv):
            raise ValueError("argv must contain NUL-free strings")
        if sum(len(arg.encode("utf-8")) for arg in self.argv) > 32768:
            raise ValueError("argv exceeds 32768 bytes")
        if not isinstance(self.cwd, str) or "\x00" in self.cwd or len(self.cwd) > 4096:
            raise ValueError("cwd must be a workspace path")
        path = PurePosixPath(self.cwd)
        if str(path) != self.cwd or ".." in path.parts or not path.is_relative_to("/workspace"):
            raise ValueError("cwd must be canonical and contained in /workspace")
        if not isinstance(self.limits, SandboxLimits):
            raise ValueError("limits must be SandboxLimits")
        # JSON 转义也消耗入口 argv 字节预算；在任何 HTTP 创建前拒绝超限契约。
        self.to_json()

    def to_json(self) -> str:
        encoded = json.dumps(self.to_wire(), ensure_ascii=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_COMMAND_JSON_BYTES:
            raise ValueError("serialized command exceeds 65536 bytes")
        return encoded

    @classmethod
    def from_json(cls, value: str) -> SandboxCommand:
        if not isinstance(value, str) or len(value.encode("utf-8")) > MAX_COMMAND_JSON_BYTES:
            raise ValueError("serialized command exceeds 65536 bytes")
        return cls.from_wire(json.loads(value))

    def to_wire(self) -> dict[str, Any]:
        return {
            "executable": self.executable,
            "argv": list(self.argv),
            "cwd": self.cwd,
            "limits": asdict(self.limits),
        }

    @classmethod
    def from_wire(cls, value: Any) -> SandboxCommand:
        if not isinstance(value, dict) or set(value) != {"executable", "argv", "cwd", "limits"}:
            raise ValueError("command requires exactly executable, argv, cwd, limits")
        limits = value["limits"]
        if not isinstance(limits, dict) or set(limits) != set(asdict(SandboxLimits())):
            raise ValueError("command requires all supported limits and no extra fields")
        if not isinstance(value["argv"], list):
            raise ValueError("wire argv must be a list")
        return cls(value["executable"], tuple(value["argv"]), value["cwd"], SandboxLimits(**limits))


@dataclass(frozen=True, slots=True)
class SandboxRequest:
    """可信调用者提供稳定 ID 用于对账，该 ID 不是权限凭证。"""

    sandbox_id: str
    command: SandboxCommand

    def __post_init__(self) -> None:
        if not isinstance(self.sandbox_id, str) or not re.fullmatch(
            r"[0-9a-f]{32}", self.sandbox_id
        ):
            raise ValueError("sandbox_id must be 32 lowercase hexadecimal characters")
        if not isinstance(self.command, SandboxCommand):
            raise ValueError("command must be SandboxCommand")


@dataclass(frozen=True, slots=True)
class SandboxHandle:
    """无 client、API 地址、认证信息或命令输出的资源标识。"""

    name: str
    fingerprint: str
    job_uid: str | None = None
    policy_uid: str | None = None

    def __post_init__(self) -> None:
        if not re.fullmatch(r"obsion-sb-[0-9a-f]{32}", self.name):
            raise ValueError("invalid sandbox resource name")
        if not re.fullmatch(r"[0-9a-f]{64}", self.fingerprint):
            raise ValueError("invalid sandbox fingerprint")
        for uid in (self.job_uid, self.policy_uid):
            if uid is not None and (not isinstance(uid, str) or not uid or len(uid) > 128):
                raise ValueError("invalid resource UID")


@dataclass(frozen=True, slots=True)
class SandboxStatus:
    phase: str
    pod_name: str | None = None
    exit_code: int | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SandboxLogs:
    text: str
    truncated: bool
    bytes_returned: int


@dataclass(frozen=True, slots=True)
class CleanupResult:
    """False 不代表已回收；调用者须保存返回 handle 用于下一轮或重启后对账。"""

    complete: bool
    handle: SandboxHandle
