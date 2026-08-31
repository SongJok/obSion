from __future__ import annotations

from pathlib import Path

import pytest

from obsion_cli.config import CliError, load_settings


def test_load_settings_prefers_cli_over_env(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('base_url = "http://from-file:8080"\nprotocol = "rest"\n', encoding="utf-8")
    settings = load_settings(
        url="http://from-flag:9",
        protocol="app-server",
        token="from-flag",
        config_path=config,
        environ={"OBSION_URL": "http://from-env:8080", "OBSION_TOKEN": "from-env"},
    )
    assert settings.base_url == "http://from-flag:9"
    assert settings.protocol == "app-server"
    assert settings.token == "from-flag"  # noqa: S105


def test_config_file_rejects_embedded_credentials(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        'base_url = "http://127.0.0.1:8080"\ntoken = "secret-value"\n',
        encoding="utf-8",
    )
    with pytest.raises(CliError, match="must not contain credentials"):
        load_settings(config_path=config, environ={})


def test_missing_config_file_is_optional(tmp_path: Path) -> None:
    settings = load_settings(config_path=tmp_path / "missing.toml", environ={})
    assert settings.base_url == "http://127.0.0.1:8080"
    assert settings.protocol == "app-server"
    assert settings.token is None
