from __future__ import annotations

import json

import pytest

from obsion_im.config import FeishuEventSecurity, ImError
from obsion_im.envelopes import UrlVerification, parse_inbound
from obsion_im.signatures import (
    decrypt_feishu_event,
    official_feishu_signature,
    prepare_feishu_http_payload,
    verify_official_feishu_signature,
)
from obsion_im.webhook import parse_posted

OFFICIAL_PLAINTEXT = "hello world"
OFFICIAL_KEY = "test key"
OFFICIAL_CIPHERTEXT = "P37w+VZImNgPEO1RBhJ6RtKl7n6zymIbEG1pReEzghk="

FEISHU_EVENT = {
    "schema": "2.0",
    "header": {"event_type": "im.message.receive_v1", "event_id": "evt-1", "token": "verify-token"},
    "event": {
        "sender": {"sender_id": {"open_id": "ou_alice"}, "sender_type": "user"},
        "message": {
            "chat_id": "oc_ops",
            "message_type": "text",
            "content": json.dumps({"text": "你好"}, ensure_ascii=False),
        },
    },
}


def _headers(body: bytes, encrypt_key: str) -> dict[str, str]:
    return {
        "X-Lark-Request-Timestamp": "1710000000",
        "X-Lark-Request-Nonce": "n1",
        "X-Lark-Signature": official_feishu_signature("1710000000", "n1", encrypt_key, body),
    }


def test_official_feishu_decrypt_matches_documented_vector() -> None:
    assert decrypt_feishu_event(OFFICIAL_CIPHERTEXT, OFFICIAL_KEY) == OFFICIAL_PLAINTEXT


def test_official_feishu_signature_accepts_raw_body() -> None:
    body = json.dumps(FEISHU_EVENT, ensure_ascii=False).encode()
    verify_official_feishu_signature(_headers(body, "encrypt-key"), body, encrypt_key="encrypt-key")


def test_official_feishu_signature_rejects_tampered_body() -> None:
    body = json.dumps(FEISHU_EVENT, ensure_ascii=False).encode()
    headers = _headers(body, "encrypt-key")
    with pytest.raises(ImError, match="Official Feishu signature is invalid"):
        verify_official_feishu_signature(headers, body + b" ", encrypt_key="encrypt-key")


def test_official_headers_without_encrypt_key_fail_closed() -> None:
    body = json.dumps(FEISHU_EVENT, ensure_ascii=False).encode()
    with pytest.raises(ImError, match="OBSION_FEISHU_ENCRYPT_KEY"):
        prepare_feishu_http_payload(body, _headers(body, "encrypt-key"), FeishuEventSecurity())


def test_encrypt_key_requires_official_headers_for_events() -> None:
    body = json.dumps(FEISHU_EVENT, ensure_ascii=False).encode()
    with pytest.raises(ImError, match="X-Lark-Signature"):
        prepare_feishu_http_payload(
            body,
            {},
            FeishuEventSecurity(encrypt_key="encrypt-key"),
        )


def test_url_verification_remains_unsigned_when_encrypt_key_is_set() -> None:
    body = json.dumps(
        {"type": "url_verification", "challenge": "challenge-1", "token": "verify-token"}
    ).encode()
    payload = prepare_feishu_http_payload(
        body,
        {},
        FeishuEventSecurity(encrypt_key="encrypt-key", verification_token="verify-token"),
    )
    parsed = parse_inbound("feishu", payload)
    assert isinstance(parsed, UrlVerification)
    assert parsed.challenge == "challenge-1"


def test_webhook_parses_officially_signed_feishu_event() -> None:
    body = json.dumps(FEISHU_EVENT, ensure_ascii=False).encode()
    inbound = parse_posted(
        "feishu",
        body.decode(),
        "application/json",
        secret=None,
        headers=_headers(body, "encrypt-key"),
        feishu_security=FeishuEventSecurity(encrypt_key="encrypt-key"),
        raw_body=body,
    )
    assert inbound.sender_id == "ou_alice"
    assert inbound.conversation_id == "oc_ops"
    assert inbound.text == "你好"


def test_encrypted_event_is_decrypted_after_official_signature() -> None:
    import base64
    import hashlib
    import os

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    plaintext = json.dumps(FEISHU_EVENT, ensure_ascii=False).encode()
    key = hashlib.sha256(b"encrypt-key").digest()
    iv = os.urandom(16)
    pad = 16 - (len(plaintext) % 16)
    padded = plaintext + bytes([pad]) * pad
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ciphertext = base64.b64encode(iv + encryptor.update(padded) + encryptor.finalize()).decode()
    wrapped = json.dumps({"encrypt": ciphertext}).encode()
    inbound = parse_posted(
        "feishu",
        wrapped.decode(),
        "application/json",
        secret=None,
        headers=_headers(wrapped, "encrypt-key"),
        feishu_security=FeishuEventSecurity(encrypt_key="encrypt-key"),
        raw_body=wrapped,
    )
    assert inbound.text == "你好"


def test_verification_token_mismatch_fails_closed() -> None:
    body = json.dumps(FEISHU_EVENT, ensure_ascii=False).encode()
    with pytest.raises(ImError, match="verification token"):
        prepare_feishu_http_payload(
            body,
            _headers(body, "encrypt-key"),
            FeishuEventSecurity(encrypt_key="encrypt-key", verification_token="other"),
        )
