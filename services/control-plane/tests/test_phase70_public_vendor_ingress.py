from __future__ import annotations

from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web"
IM_ROOT = Path(__file__).resolve().parents[3] / "apps" / "im-adapter" / "src" / "obsion_im"


def test_public_dingtalk_wecom_ingress_is_explicit_tls() -> None:
    webhook = (IM_ROOT / "webhook.py").read_text(encoding="utf-8")
    assert "Public WeCom webhook requires OBSION_WECOM_ENCODING_AES_KEY" in webhook
    assert "Public DingTalk webhook requires" in webhook
    assert "feishu, dingtalk, and wecom only" in webhook
    admin = (WEB_ROOT / "src" / "components" / "admin-view.tsx").read_text(encoding="utf-8")
    assert "dingtalk" in admin.lower() or "钉钉" in admin
    assert "--public" in admin
