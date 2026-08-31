from __future__ import annotations

import ast
from pathlib import Path

WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web"
IM_ROOT = Path(__file__).resolve().parents[3] / "apps" / "im-adapter" / "src" / "obsion_im"


def test_wecom_aes_is_experience_inbound_not_a_second_harness() -> None:
    admin = (WEB_ROOT / "src" / "components" / "admin-view.tsx").read_text(encoding="utf-8")
    assert "EncodingAESKey" in admin or "WECOM_ENCODING_AES_KEY" in admin or "WeCom AES" in admin
    signatures = (IM_ROOT / "signatures.py").read_text(encoding="utf-8")
    assert "decrypt_wecom_cipher" in signatures
    assert "EncodingAESKey" in signatures or "encoding_aes_key" in signatures
    tree = ast.parse(signatures)
    imports = [
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    ]
    assert "obsion.harness.runtime" not in imports
    assert "wechatpy" not in imports
    config = (IM_ROOT / "config.py").read_text(encoding="utf-8")
    assert "OBSION_WECOM_ENCODING_AES_KEY" in config
    envelopes = (IM_ROOT / "envelopes.py").read_text(encoding="utf-8")
    assert "OBSION_WECOM_ENCODING_AES_KEY" in envelopes
