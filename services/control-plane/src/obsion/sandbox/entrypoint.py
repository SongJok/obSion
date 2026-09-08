"""可信镜像入口，禁止将其用作宿主机本地沙箱后端。

本包必须安装在固定摘要、只读镜像的 /usr/local 中，以 Python -I -B 启动。
监督进程不接收凭据，只传递固定最小环境，不继承控制面或 Python 启动钩子。
仍依赖 Linux/gVisor 与 cgroups；rlimit 本身不是沙箱。
"""

from __future__ import annotations

import argparse
import ctypes
import os
import resource
import signal
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from types import FrameType

from obsion.sandbox.contracts import SandboxCommand


class _Cancelled(Exception):
    pass


def _require_non_root_linux() -> None:
    if sys.platform != "linux" or os.getuid() == 0 or os.geteuid() == 0:
        raise RuntimeError("entrypoint requires non-root Linux")


def _apply_process_limit(maximum: int) -> None:
    _require_non_root_linux()
    # 除 Pod 的 allowPrivilegeEscalation=false 外，入口自身也要求 no_new_privs。
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(38, 1, 0, 0, 0) != 0:  # PR_SET_NO_NEW_PRIVS
        raise RuntimeError("could not set no_new_privs")
    _, hard = resource.getrlimit(resource.RLIMIT_NPROC)
    ceiling = maximum if hard == resource.RLIM_INFINITY else min(maximum, hard)
    resource.setrlimit(resource.RLIMIT_NPROC, (ceiling, ceiling))
    if resource.getrlimit(resource.RLIMIT_NPROC) != (ceiling, ceiling):
        raise RuntimeError("could not enforce RLIMIT_NPROC")
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _resolve_cwd(cwd: str) -> str:
    root = Path("/workspace").resolve(strict=True)
    if str(root) != "/workspace":
        raise ValueError("workspace root must not be a symlink")
    resolved = Path(cwd).resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_dir():
        raise ValueError("cwd escapes the workspace or is not a directory")
    return str(resolved)


def _kill_group(process: subprocess.Popen[bytes]) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)


def _supervise(argv: list[str], cwd: str, timeout_seconds: int) -> int:
    """内部进程组生命周期函数，不注册为可供 Agent 直接调用的能力。"""
    process = None
    cancelled = False
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}

    def cancel(signum: int, frame: FrameType | None) -> None:
        nonlocal cancelled
        cancelled = True
        # Popen 尚未返回 PID 时只记取消标记，不在赋值前抛异常而丢失进程。
        # 不使用 preexec_fn，也不让命令继承临时屏蔽的终止信号。
        if process is not None:
            raise _Cancelled

    try:
        for sig in previous:
            signal.signal(sig, cancel)
        process = subprocess.Popen(  # noqa: S603 - gVisor 中执行结构化 argv，不隐式调用 shell
            argv,
            cwd=cwd,
            start_new_session=True,
            shell=False,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            env={
                "PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": "/workspace",
                "TMPDIR": "/workspace",
                "LANG": "C.UTF-8",
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
        if cancelled:
            raise _Cancelled
        try:
            code = process.wait(timeout=timeout_seconds)
            return code if code >= 0 else 128 - code
        except subprocess.TimeoutExpired:
            return 124
    except _Cancelled:
        return 143
    finally:
        # 即使命令成功退出，也清理同组后台进程。SIGKILL 不依赖子进程配合 TERM。
        # 脱离进程组的恶意进程仍须由容器退出/Job 删除及运行时回收，不夸大 killpg。
        for sig in previous:
            signal.signal(sig, signal.SIG_IGN)
        if process is not None:
            _kill_group(process)
            process.wait()
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trusted Obsion sandbox entrypoint")
    parser.add_argument("--command", required=True)
    args = parser.parse_args(argv)
    try:
        command = SandboxCommand.from_json(args.command)
        cwd = _resolve_cwd(command.cwd)
        _apply_process_limit(command.limits.processes)
        return _supervise([command.executable, *command.argv], cwd, command.limits.timeout_seconds)
    except (ValueError, OSError, RuntimeError):
        # 错误不输出任意命令参数、来源路径或继承环境。
        print("sandbox_entrypoint_rejected", file=sys.stderr)
        return 125


if __name__ == "__main__":
    raise SystemExit(main())
