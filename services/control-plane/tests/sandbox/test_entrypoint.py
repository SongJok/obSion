"""可信入口单元/本地进程生命周期验证；不模拟成 Linux/gVisor 隔离验收。"""

import os
import signal
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from obsion.sandbox import entrypoint
from obsion.sandbox.contracts import SandboxCommand


@pytest.mark.parametrize("uid,platform", [(0, "linux"), (1000, "darwin")])
def test_process_limit_rejects_root_or_non_linux(monkeypatch, uid, platform):
    monkeypatch.setattr(entrypoint.sys, "platform", platform)
    monkeypatch.setattr(entrypoint.os, "getuid", lambda: uid)
    monkeypatch.setattr(entrypoint.os, "geteuid", lambda: uid)
    calls = []
    monkeypatch.setattr(entrypoint.resource, "setrlimit", lambda *args: calls.append(args))
    with pytest.raises(RuntimeError, match="non-root Linux"):
        entrypoint._apply_process_limit(128)
    assert not calls


@pytest.mark.parametrize(
    "existing_hard,expected",
    [
        (entrypoint.resource.RLIM_INFINITY, 128),
        (256, 128),
        (64, 64),
    ],
)
def test_process_limit_sets_both_hard_and_soft_without_increasing(
    monkeypatch, existing_hard, expected
):
    monkeypatch.setattr(entrypoint.sys, "platform", "linux")
    monkeypatch.setattr(entrypoint.os, "getuid", lambda: 65532)
    monkeypatch.setattr(entrypoint.os, "geteuid", lambda: 65532)
    prctl_calls = []
    monkeypatch.setattr(
        entrypoint.ctypes,
        "CDLL",
        lambda *a, **kw: SimpleNamespace(
            prctl=lambda *args: prctl_calls.append(args) or 0,
        ),
    )
    limits = {entrypoint.resource.RLIMIT_NPROC: (32, existing_hard)}
    monkeypatch.setattr(entrypoint.resource, "getrlimit", lambda which: limits[which])
    monkeypatch.setattr(
        entrypoint.resource, "setrlimit", lambda which, pair: limits.update({which: pair})
    )
    entrypoint._apply_process_limit(128)
    assert limits[entrypoint.resource.RLIMIT_NPROC] == (expected, expected)
    assert limits[entrypoint.resource.RLIMIT_CORE] == (0, 0)
    assert prctl_calls == [(38, 1, 0, 0, 0)]


def test_no_new_privileges_failure_prevents_process_start(monkeypatch):
    monkeypatch.setattr(entrypoint.sys, "platform", "linux")
    monkeypatch.setattr(entrypoint.os, "getuid", lambda: 65532)
    monkeypatch.setattr(entrypoint.os, "geteuid", lambda: 65532)
    monkeypatch.setattr(
        entrypoint.ctypes, "CDLL", lambda *a, **kw: SimpleNamespace(prctl=lambda *a: -1)
    )
    with pytest.raises(RuntimeError, match="no_new_privs"):
        entrypoint._apply_process_limit(128)


def test_entrypoint_applies_limits_before_spawn(monkeypatch):
    import json

    order = []
    monkeypatch.setattr(entrypoint, "_resolve_cwd", lambda cwd: cwd)
    monkeypatch.setattr(entrypoint, "_apply_process_limit", lambda n: order.append(("limit", n)))
    monkeypatch.setattr(
        entrypoint,
        "_supervise",
        lambda argv, cwd, timeout: order.append(("spawn", argv, cwd, timeout)) or 7,
    )
    command = SandboxCommand("/bin/false")
    assert entrypoint.main(["--command", json.dumps(command.to_wire())]) == 7
    assert order == [("limit", 128), ("spawn", ["/bin/false"], "/workspace", 120)]


@pytest.mark.parametrize(
    "argument", ["中" * 10922, "\x01" * 10000, '"' * 32000], ids=["unicode", "control", "quotes"]
)
def test_entrypoint_accepts_shared_serializer_at_large_sizes(monkeypatch, argument):
    received = []
    monkeypatch.setattr(entrypoint, "_resolve_cwd", lambda cwd: cwd)
    monkeypatch.setattr(entrypoint, "_apply_process_limit", lambda n: None)
    monkeypatch.setattr(
        entrypoint, "_supervise", lambda argv, cwd, timeout: received.append(argv) or 0
    )
    command = SandboxCommand("/bin/printf", (argument,))
    assert entrypoint.main(["--command", command.to_json()]) == 0
    assert received == [["/bin/printf", argument]]


def test_entrypoint_rejects_raw_oversize_before_limits_and_spawn(monkeypatch, capsys):
    monkeypatch.setattr(
        entrypoint, "_resolve_cwd", lambda *a: pytest.fail("must reject before cwd")
    )
    monkeypatch.setattr(
        entrypoint, "_apply_process_limit", lambda *a: pytest.fail("must not set limits")
    )
    monkeypatch.setattr(entrypoint, "_supervise", lambda *a: pytest.fail("must not spawn"))
    assert entrypoint.main(["--command", " " * 65537]) == 125
    assert capsys.readouterr().err == "sandbox_entrypoint_rejected\n"


def test_entrypoint_fail_closed_with_sanitized_error(monkeypatch, capsys):
    import json

    def fail(cwd):
        raise ValueError("secret-source-text")

    monkeypatch.setattr(entrypoint, "_resolve_cwd", fail)
    monkeypatch.setattr(entrypoint, "_supervise", lambda *a: pytest.fail("must not spawn"))
    assert entrypoint.main(["--command", json.dumps(SandboxCommand("/bin/false").to_wire())]) == 125
    assert capsys.readouterr().err == "sandbox_entrypoint_rejected\n"


def test_realpath_workspace_escape_is_rejected(monkeypatch, tmp_path):
    original = Path.resolve

    def resolve(path, strict=False):
        if str(path) == "/workspace":
            return Path("/workspace")
        if str(path) == "/workspace/escape":
            return tmp_path
        return original(path, strict=strict)

    monkeypatch.setattr(entrypoint.Path, "resolve", resolve)
    with pytest.raises(ValueError, match="escapes"):
        entrypoint._resolve_cwd("/workspace/escape")


def test_supervisor_uses_literal_argv_and_clean_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSION_FAKE_CLUSTER_CREDENTIAL", "must-not-inherit")
    output = tmp_path / "result.json"
    script = (
        "import json,os,sys; "
        "open(sys.argv[1], 'w').write(json.dumps({'args': sys.argv[2:], "
        "'secret': os.environ.get('OBSION_FAKE_CLUSTER_CREDENTIAL')}))"
    )
    assert (
        entrypoint._supervise(
            [
                sys.executable,
                "-I",
                "-c",
                script,
                str(output),
                "literal;$(touch nope)",
                "a b",
            ],
            str(tmp_path),
            5,
        )
        == 0
    )
    import json

    assert json.loads(output.read_text()) == {
        "args": ["literal;$(touch nope)", "a b"],
        "secret": None,
    }
    assert not (tmp_path / "nope").exists()


def test_supervisor_timeout_kills_entire_process_group(tmp_path):
    # 只启动受测试控制的两个 sleep 子进程；不运行任意用户代码/不改变宿主 rlimit。
    pid_file = tmp_path / "pids"
    script = (
        "import os,subprocess,sys,time; "
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); "
        "open(sys.argv[1],'w').write(str(os.getpid())+' '+str(child.pid)); "
        "time.sleep(30)"
    )
    killed = []
    original = entrypoint.os.killpg

    # 实际 killpg 仍执行；记录 PID 与信号，验证不是仅杀父进程。
    from unittest.mock import patch

    def killpg(pid, sig):
        killed.append((pid, sig))
        original(pid, sig)

    with patch.object(entrypoint.os, "killpg", killpg):
        assert (
            entrypoint._supervise([sys.executable, "-c", script, str(pid_file)], str(tmp_path), 1)
            == 124
        )
    parent, child = (int(value) for value in pid_file.read_text().split())
    assert killed == [(parent, signal.SIGKILL)]
    with pytest.raises(ProcessLookupError):
        os.kill(parent, 0)
    # macOS/Linux 可能暂存由 init 回收的僵尸，僵尸不是仍可运行的进程。
    state = subprocess.run(  # noqa: S603 - 仅查询本测试启动的 PID
        ["/bin/ps", "-o", "stat=", "-p", str(child)],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    assert not state or state.startswith("Z")


def test_supervisor_cancellation_kills_process_group(monkeypatch):
    handlers = {}
    calls = []
    process = SimpleNamespace(pid=987654)

    def wait(timeout=None):
        if timeout is not None:
            handlers[signal.SIGTERM](signal.SIGTERM, None)
        calls.append("reaped")
        return -signal.SIGKILL

    process.wait = wait
    monkeypatch.setattr(entrypoint.signal, "getsignal", lambda sig: signal.SIG_DFL)
    monkeypatch.setattr(
        entrypoint.signal, "signal", lambda sig, handler: handlers.update({sig: handler})
    )
    monkeypatch.setattr(entrypoint.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(entrypoint.os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    assert entrypoint._supervise(["/bin/sleep", "30"], "/workspace", 5) == 143
    assert calls == [(987654, signal.SIGKILL), "reaped"]
    assert handlers == {signal.SIGTERM: signal.SIG_DFL, signal.SIGINT: signal.SIG_DFL}


def test_cancellation_during_spawn_does_not_lose_process_group(monkeypatch):
    handlers = {}
    calls = []
    process = SimpleNamespace(pid=987654, wait=lambda **kw: calls.append("reaped") or -9)

    def spawn(*args, **kwargs):
        handlers[signal.SIGTERM](signal.SIGTERM, None)
        return process

    monkeypatch.setattr(entrypoint.signal, "getsignal", lambda sig: signal.SIG_DFL)
    monkeypatch.setattr(
        entrypoint.signal, "signal", lambda sig, handler: handlers.update({sig: handler})
    )
    monkeypatch.setattr(entrypoint.subprocess, "Popen", spawn)
    monkeypatch.setattr(entrypoint.os, "killpg", lambda pid, sig: calls.append((pid, sig)))
    assert entrypoint._supervise(["/bin/sleep", "30"], "/workspace", 5) == 143
    assert calls == [(987654, signal.SIGKILL), "reaped"]
