from __future__ import annotations

from pathlib import Path

import pytest

from obsion_cli.config import CliError
from obsion_im.config import ImError, load_im_settings, normalize_channel


def test_load_im_settings_defaults_to_development_channel(tmp_path: Path) -> None:
    settings = load_im_settings(config_path=tmp_path / "missing.toml", environ={})
    assert settings.channel == "development"
    assert settings.workspace_name == "IM"
    assert settings.cli.protocol == "app-server"
    assert settings.delivery == "local_outbox"
    assert settings.outbox_path is None


def test_vendor_channels_are_inbound_namespaces_not_http_clients(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    feishu = load_im_settings(channel="feishu", config_path=missing, environ={})
    assert feishu.channel == "feishu"
    dingtalk = load_im_settings(config_path=missing, environ={"OBSION_IM_CHANNEL": "dingtalk"})
    assert dingtalk.channel == "dingtalk"
    assert normalize_channel("lark") == "feishu"
    assert normalize_channel("wechat_work") == "wecom"


def test_unknown_channel_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ImError, match="Channel must be development"):
        load_im_settings(channel="slack", config_path=tmp_path / "missing.toml", environ={})


def test_config_file_still_rejects_embedded_credentials(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        'base_url = "http://127.0.0.1:8080"\ntoken = "secret-value"\n',
        encoding="utf-8",
    )
    with pytest.raises(CliError, match="must not contain credentials"):
        load_im_settings(config_path=config, environ={})


def test_config_file_rejects_vendor_app_secrets(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        'base_url = "http://127.0.0.1:8080"\napp_secret = "tenant-app-secret"\n',
        encoding="utf-8",
    )
    with pytest.raises(ImError, match="vendor IM credentials"):
        load_im_settings(config_path=config, environ={})


def test_http_delivery_env_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ImError, match="HTTP delivery is not implemented"):
        load_im_settings(
            config_path=tmp_path / "missing.toml",
            environ={"OBSION_IM_DELIVER": "http"},
        )


def test_feishu_http_requires_namespaced_environment_credentials(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    with pytest.raises(ImError, match="OBSION_FEISHU_APP_ID"):
        load_im_settings(
            channel="feishu",
            delivery="feishu-http",
            config_path=missing,
            environ={},
        )
    settings = load_im_settings(
        channel="feishu",
        delivery="feishu-http",
        config_path=missing,
        environ={
            "OBSION_FEISHU_APP_ID": "cli_test_app",
            "OBSION_FEISHU_APP_SECRET": "test-app-secret",
        },
    )
    assert settings.delivery == "feishu_http"
    assert settings.feishu_credentials is not None
    assert "test-app-secret" not in repr(settings)


def test_feishu_event_security_comes_from_environment_and_is_hidden(tmp_path: Path) -> None:
    settings = load_im_settings(
        channel="feishu",
        config_path=tmp_path / "missing.toml",
        environ={
            "OBSION_FEISHU_ENCRYPT_KEY": "encrypt-key",
            "OBSION_FEISHU_VERIFICATION_TOKEN": "verify-token",
        },
    )
    assert settings.feishu_security.encrypt_key == "encrypt-key"
    assert settings.feishu_security.verification_token == "verify-token"
    rendered = repr(settings)
    assert "encrypt-key" not in rendered
    assert "verify-token" not in rendered


def test_feishu_http_is_not_available_for_other_vendors(tmp_path: Path) -> None:
    with pytest.raises(ImError, match="requires --channel feishu"):
        load_im_settings(
            channel="dingtalk",
            delivery="feishu-http",
            config_path=tmp_path / "missing.toml",
            environ={
                "OBSION_FEISHU_APP_ID": "cli_test_app",
                "OBSION_FEISHU_APP_SECRET": "test-app-secret",
            },
        )


def test_dingtalk_http_requires_namespaced_environment_credentials(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    with pytest.raises(ImError, match="OBSION_DINGTALK_APP_KEY"):
        load_im_settings(
            channel="dingtalk",
            delivery="dingtalk-http",
            config_path=missing,
            environ={},
        )
    settings = load_im_settings(
        channel="dingtalk",
        delivery="dingtalk-http",
        config_path=missing,
        environ={
            "OBSION_DINGTALK_APP_KEY": "ding-test-key",
            "OBSION_DINGTALK_APP_SECRET": "ding-test-secret",
        },
    )
    assert settings.delivery == "dingtalk_http"
    assert settings.dingtalk_credentials is not None
    assert "ding-test-secret" not in repr(settings)


def test_wecom_http_requires_corp_credentials_and_agent_id(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    with pytest.raises(ImError, match="OBSION_WECOM_CORP_ID"):
        load_im_settings(
            channel="wecom",
            delivery="wecom-http",
            config_path=missing,
            environ={"OBSION_WECOM_CORP_ID": "ww-corp"},
        )
    settings = load_im_settings(
        channel="wecom",
        delivery="wecom-http",
        config_path=missing,
        environ={
            "OBSION_WECOM_CORP_ID": "ww-corp",
            "OBSION_WECOM_CORP_SECRET": "ww-secret",
            "OBSION_WECOM_AGENT_ID": "1000002",
        },
    )
    assert settings.delivery == "wecom_http"
    assert settings.wecom_credentials is not None
    assert "ww-secret" not in repr(settings)


def test_vendor_http_transports_require_matching_channels(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"
    with pytest.raises(ImError, match="requires --channel dingtalk"):
        load_im_settings(
            channel="wecom",
            delivery="dingtalk-http",
            config_path=missing,
            environ={
                "OBSION_DINGTALK_APP_KEY": "ding-test-key",
                "OBSION_DINGTALK_APP_SECRET": "ding-test-secret",
            },
        )
    with pytest.raises(ImError, match="requires --channel wecom"):
        load_im_settings(
            channel="dingtalk",
            delivery="wecom-http",
            config_path=missing,
            environ={
                "OBSION_WECOM_CORP_ID": "ww-corp",
                "OBSION_WECOM_CORP_SECRET": "ww-secret",
                "OBSION_WECOM_AGENT_ID": "1000002",
            },
        )


def test_nested_vendor_secrets_are_also_rejected(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text(
        'base_url = "http://127.0.0.1:8080"\n[im]\ncorp_secret = "tenant-corp-secret"\n',
        encoding="utf-8",
    )
    with pytest.raises(ImError, match="vendor IM credentials"):
        load_im_settings(config_path=config, environ={})
