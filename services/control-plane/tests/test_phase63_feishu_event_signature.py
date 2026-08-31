from __future__ import annotations

from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web"
IM_ROOT = Path(__file__).resolve().parents[3] / "apps" / "im-adapter" / "src" / "obsion_im"


def test_feishu_official_signature_is_documented_and_not_a_second_harness() -> None:
    admin = (WEB_ROOT / "src" / "components" / "admin-view.tsx").read_text(encoding="utf-8")
    assert "X-Lark-Signature" in admin
    assert "OBSION_FEISHU_ENCRYPT_KEY" in admin
    assert "AES-256-CBC" in admin
    signatures = (IM_ROOT / "signatures.py").read_text(encoding="utf-8")
    assert "official_feishu_signature" in signatures
    assert "decrypt_feishu_event" in signatures
    assert "x-lark-signature" in signatures
    webhook = (IM_ROOT / "webhook.py").read_text(encoding="utf-8")
    assert "may only bind 127.0.0.1" in webhook
    assert "prepare_feishu_http_payload" in webhook
