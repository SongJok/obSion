"""本地回归入口不得继承真实集成配置或读取开发目录 dotenv。"""

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]


def runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "test_local_runner", ROOT / "scripts/test_local.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_local_environment_removes_application_and_pytest_configuration() -> None:
    environment = runner().local_environment(
        {
            "PATH": "/usr/bin",
            "OBSION_FEISHU_SEND_LIVE": "1",
            "OBSION_YUNXIAO_LIVE": "1",
            "OBSION_RUN_POSTGRES_TESTS": "1",
            "OBSION_DATABASE_URL": "postgresql://example.invalid/test",
            "OBSION_MODEL_ALLOWED_HOSTS": '["example.invalid"]',
            "PYTEST_ADDOPTS": "-p unexpected_plugin",
            "PYTEST_PLUGINS": "unexpected_plugin",
        }
    )
    assert environment == {"PATH": "/usr/bin", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"}


def test_local_runner_uses_empty_directory_and_preserves_failure() -> None:
    module = runner()
    directories: list[Path] = []

    def execute(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        directory = Path(str(kwargs["cwd"]))
        directories.append(directory)
        assert directory.is_dir()
        assert not list(directory.iterdir())
        assert directory != ROOT
        assert kwargs["check"] is False
        assert command[command.index("--rootdir") + 1] == str(ROOT)
        assert command[command.index("-m", 3) + 1] == (
            "not live and not feishu_browse_live and not feishu_send_live and not yunxiao_live"
        )
        assert all(str(ROOT / path) in command for path in module.TEST_PATHS)
        return subprocess.CompletedProcess(command, 1)

    with (
        patch.object(module.sys, "argv", ["test_local.py"]),
        patch.object(module.subprocess, "run", side_effect=execute),
    ):
        assert module.main() == 1
    assert directories and all(not directory.exists() for directory in directories)


def test_local_runner_rejects_argument_injection() -> None:
    module = runner()
    with (
        patch.object(module.sys, "argv", ["test_local.py", "-m", "live"]),
        patch.object(module.subprocess, "run") as execute,
    ):
        assert module.main() == 2
        execute.assert_not_called()
