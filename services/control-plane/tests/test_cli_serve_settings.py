"""启动入口使用配置端口，并保留显式命令行覆盖。"""

from unittest.mock import Mock

import pytest

from obsion import cli
from obsion.config import Settings


@pytest.mark.parametrize(
    ("arguments", "host", "port"),
    [
        ([], "127.0.0.1", 58081),
        (["--port", "58082"], "127.0.0.1", 58082),
        (["--host", "localhost"], "localhost", 58081),
    ],
)
def test_serve_respects_settings_and_explicit_overrides(
    monkeypatch: pytest.MonkeyPatch, arguments: list[str], host: str, port: int
) -> None:
    settings = Settings(_env_file=None, api_host="127.0.0.1", api_port=58081)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    run = Mock()
    monkeypatch.setattr(cli.uvicorn, "run", run)
    args = cli.build_parser().parse_args(["serve", "--reload", *arguments])
    cli._serve(args)
    run.assert_called_once_with(
        "obsion.main:create_app",
        factory=True,
        host=host,
        port=port,
        reload=True,
        proxy_headers=True,
    )
