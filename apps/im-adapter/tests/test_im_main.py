from __future__ import annotations

import io
import json
from pathlib import Path

from obsion_im.main import build_parser, main


def test_parser_requires_a_stable_sender_id() -> None:
    args = build_parser().parse_args(
        [
            "ingest",
            "--conversation",
            "ops",
            "--text",
            "你好",
            "--sender-id",
            "alice-stable",
        ]
    )
    assert args.command == "ingest"
    assert args.channel == "development"
    assert args.sender_id == "alice-stable"


def test_main_requires_a_token_and_does_not_connect(monkeypatch) -> None:
    monkeypatch.delenv("OBSION_TOKEN", raising=False)
    stderr = io.StringIO()
    code = main(
        [
            "ingest",
            "--conversation",
            "ops",
            "--text",
            "你好",
            "--sender-id",
            "alice-stable",
        ],
        err=stderr,
    )
    assert code == 1
    assert "OBSION_TOKEN" in stderr.getvalue()


def test_vendor_ingest_requires_an_envelope(tmp_path: Path) -> None:
    stderr = io.StringIO()
    missing = str(tmp_path / "missing.toml")
    code = main(
        ["--config", missing, "--channel", "feishu", "ingest"],
        err=stderr,
        environ={},
    )
    assert code == 1
    assert "envelope" in stderr.getvalue().lower()


def test_feishu_url_verification_does_not_need_a_token(tmp_path: Path) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    missing = str(tmp_path / "missing.toml")
    code = main(
        [
            "--config",
            missing,
            "--channel",
            "feishu",
            "--json",
            "ingest",
            "--envelope",
            '{"type":"url_verification","challenge":"challenge-1"}',
        ],
        out=stdout,
        err=stderr,
        environ={},
    )
    assert code == 0, stderr.getvalue()
    assert json.loads(stdout.getvalue())["challenge"] == "challenge-1"


def test_invalid_envelope_json_does_not_connect(tmp_path: Path) -> None:
    stderr = io.StringIO()
    missing = str(tmp_path / "missing.toml")
    code = main(
        [
            "--config",
            missing,
            "--channel",
            "feishu",
            "ingest",
            "--envelope",
            "{not-json",
        ],
        err=stderr,
        environ={},
    )
    assert code == 1
    assert "valid JSON" in stderr.getvalue()


def test_http_delivery_flag_is_rejected_without_connecting(tmp_path: Path) -> None:
    stderr = io.StringIO()
    code = main(
        [
            "--config",
            str(tmp_path / "missing.toml"),
            "--channel",
            "feishu",
            "--deliver",
            "http",
            "ingest",
            "--envelope",
            '{"schema":"2.0"}',
        ],
        err=stderr,
        environ={},
    )
    assert code == 1
    assert "HTTP delivery is not implemented" in stderr.getvalue()


def test_listen_on_all_interfaces_is_rejected(tmp_path: Path) -> None:
    stderr = io.StringIO()
    code = main(
        [
            "--config",
            str(tmp_path / "missing.toml"),
            "--channel",
            "feishu",
            "serve",
            "--listen",
            "0.0.0.0:8787",
        ],
        err=stderr,
        environ={},
    )
    assert code == 1
    assert "127.0.0.1" in stderr.getvalue()


def test_public_listen_without_tls_is_rejected(tmp_path: Path) -> None:
    stderr = io.StringIO()
    code = main(
        [
            "--config",
            str(tmp_path / "missing.toml"),
            "--channel",
            "feishu",
            "serve",
            "--listen",
            "0.0.0.0:8787",
            "--public",
        ],
        err=stderr,
        environ={"OBSION_FEISHU_ENCRYPT_KEY": "test key"},
    )
    assert code == 1
    assert "TLS_CERT" in stderr.getvalue()


def test_http_outbox_url_is_rejected(tmp_path: Path) -> None:
    stderr = io.StringIO()
    code = main(
        [
            "--config",
            str(tmp_path / "missing.toml"),
            "--outbox",
            "https://example.invalid/hook",
            "ingest",
            "--conversation",
            "ops",
            "--text",
            "你好",
            "--sender-id",
            "alice-stable",
        ],
        err=stderr,
        environ={},
    )
    assert code == 1
    assert "local file path" in stderr.getvalue()


def test_feishu_http_health_uses_vendor_auth_without_obsion_token(
    tmp_path: Path, monkeypatch
) -> None:
    async def health(self) -> dict[str, object]:
        return {
            "channel": "feishu",
            "delivery": "feishu_http",
            "authenticated": True,
        }

    monkeypatch.setattr("obsion_im.feishu.FeishuClient.health", health)
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = main(
        [
            "--config",
            str(tmp_path / "missing.toml"),
            "--channel",
            "feishu",
            "--deliver",
            "feishu-http",
            "health",
        ],
        out=stdout,
        err=stderr,
        environ={
            "OBSION_FEISHU_APP_ID": "cli_test_app",
            "OBSION_FEISHU_APP_SECRET": "test-app-secret",
        },
    )
    assert code == 0, stderr.getvalue()
    assert json.loads(stdout.getvalue())["authenticated"] is True


def test_dingtalk_and_wecom_http_health_use_vendor_auth_without_obsion_token(
    tmp_path: Path, monkeypatch
) -> None:
    async def ding_health(self) -> dict[str, object]:
        return {"channel": "dingtalk", "delivery": "dingtalk_http", "authenticated": True}

    async def wecom_health(self) -> dict[str, object]:
        return {"channel": "wecom", "delivery": "wecom_http", "authenticated": True}

    monkeypatch.setattr("obsion_im.dingtalk.DingTalkClient.health", ding_health)
    monkeypatch.setattr("obsion_im.wecom.WeComClient.health", wecom_health)
    missing = str(tmp_path / "missing.toml")
    ding_out = io.StringIO()
    ding_err = io.StringIO()
    ding_code = main(
        [
            "--config",
            missing,
            "--channel",
            "dingtalk",
            "--deliver",
            "dingtalk-http",
            "health",
        ],
        out=ding_out,
        err=ding_err,
        environ={
            "OBSION_DINGTALK_APP_KEY": "ding-test-key",
            "OBSION_DINGTALK_APP_SECRET": "ding-test-secret",
        },
    )
    assert ding_code == 0, ding_err.getvalue()
    assert json.loads(ding_out.getvalue())["channel"] == "dingtalk"

    wecom_out = io.StringIO()
    wecom_err = io.StringIO()
    wecom_code = main(
        [
            "--config",
            missing,
            "--channel",
            "wecom",
            "--deliver",
            "wecom-http",
            "health",
        ],
        out=wecom_out,
        err=wecom_err,
        environ={
            "OBSION_WECOM_CORP_ID": "ww-corp",
            "OBSION_WECOM_CORP_SECRET": "ww-secret",
            "OBSION_WECOM_AGENT_ID": "1000002",
        },
    )
    assert wecom_code == 0, wecom_err.getvalue()
    assert json.loads(wecom_out.getvalue())["channel"] == "wecom"
