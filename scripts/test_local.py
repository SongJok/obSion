"""在无 dotenv 和真实集成开关的临时目录运行本地 Python 回归。"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEST_PATHS = (
    "services/control-plane/tests",
    "packages/sdk-python/tests",
    "apps/cli/tests",
    "apps/im-adapter/tests",
)


def local_environment(source: Mapping[str, str]) -> dict[str, str]:
    """不继承应用配置、pytest 注入参数或真实租户启用开关。"""
    environment = {
        key: value for key, value in source.items() if not key.startswith(("OBSION_", "PYTEST_"))
    }
    environment["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    return environment


def main() -> int:
    # 不接受任意参数，以免通过插件或配置选项重新引入真实集成。
    if len(sys.argv) != 1:
        print("本地隔离回归不接受参数；真实集成请使用专用验证入口。", file=sys.stderr)
        return 2
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "pytest_asyncio.plugin",
        "-c",
        str(ROOT / "pyproject.toml"),
        "--rootdir",
        str(ROOT),
        "-m",
        "not live and not feishu_browse_live and not feishu_send_live",
        *(str(ROOT / path) for path in TEST_PATHS),
    ]
    with tempfile.TemporaryDirectory(prefix="obsion-local-tests-") as directory:
        return subprocess.run(  # noqa: S603
            command,
            cwd=directory,
            env=local_environment(os.environ),
            check=False,
        ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
