from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class CliError(Exception):
    """User-facing CLI failure that must not include credentials."""


@dataclass(frozen=True, slots=True)
class CliSettings:
    base_url: str
    token: str | None
    protocol: str
    json_output: bool = False
    poll_interval_seconds: float = 0.05
    wait_timeout_seconds: float = 120.0

    @property
    def uses_app_server(self) -> bool:
        return self.protocol == "app-server"


def load_settings(
    *,
    url: str | None = None,
    token: str | None = None,
    protocol: str | None = None,
    json_output: bool = False,
    config_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> CliSettings:
    env = environ if environ is not None else os.environ
    file_values = _read_config_file(config_path, env)
    base_url = (
        url
        or env.get("OBSION_URL")
        or env.get("OBSION_BASE_URL")
        or file_values.get("base_url")
        or "http://127.0.0.1:8080"
    ).rstrip("/")
    resolved_protocol = (
        protocol or env.get("OBSION_PROTOCOL") or file_values.get("protocol") or "app-server"
    ).lower()
    if resolved_protocol not in {"rest", "app-server"}:
        raise CliError("Protocol must be rest or app-server")
    resolved_token = token or env.get("OBSION_TOKEN")
    if resolved_token is not None and not resolved_token.strip():
        resolved_token = None
    return CliSettings(
        base_url=base_url,
        token=resolved_token,
        protocol=resolved_protocol,
        json_output=json_output,
    )


def _read_config_file(config_path: Path | None, env: Mapping[str, str]) -> dict[str, str]:
    path = config_path
    if path is None:
        configured = env.get("OBSION_CONFIG")
        path = (
            Path(configured) if configured else Path.home() / ".config" / "obsion" / "config.toml"
        )
    if not path.is_file():
        return {}
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise CliError(f"Unable to read config file {path}") from exc
    if not isinstance(document, dict):
        raise CliError("Config file must be a TOML table")
    forbidden = {"token", "password", "secret", "api_key", "bearer"}
    if forbidden & {str(key).lower() for key in document}:
        raise CliError("Config files must not contain credentials. Set OBSION_TOKEN instead.")
    values: dict[str, str] = {}
    for key in ("base_url", "protocol"):
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            values[key] = value.strip()
    return values
