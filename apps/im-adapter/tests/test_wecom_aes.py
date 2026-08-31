from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from obsion_im.config import ImError, WeComEventSecurity, load_im_settings
from obsion_im.envelopes import UrlVerification, parse_inbound
from obsion_im.signatures import (
    decrypt_wecom_cipher,
    encrypt_wecom_cipher,
    prepare_wecom_payload,
    wecom_aes_key,
    wecom_signature,
)
from obsion_im.webhook import parse_posted

# 43-char EncodingAESKey that base64-decodes (with =) to 32 bytes.
WECOM_AES_KEY = base64.b64encode(b"0" * 32).decode().rstrip("=")
assert len(WECOM_AES_KEY) == 43
WECOM_CORP_ID = "ww-test-corp"
WECOM_TOKEN = "wecom-token"

INNER_XML = (
    "<xml>"
    "<ToUserName><![CDATA[ww-test-corp]]></ToUserName>"
    "<FromUserName><![CDATA[wecom-alice]]></FromUserName>"
    "<CreateTime>1710000000</CreateTime>"
    "<MsgType><![CDATA[text]]></MsgType>"
    "<Content><![CDATA[报表]]></Content>"
    "<ChatId><![CDATA[wr_ops]]></ChatId>"
    "</xml>"
)


def test_wecom_aes_key_must_be_43_characters() -> None:
    with pytest.raises(ImError, match="43"):
        wecom_aes_key("short")


def test_wecom_encrypt_decrypt_round_trip() -> None:
    cipher = encrypt_wecom_cipher(INNER_XML, WECOM_AES_KEY, WECOM_CORP_ID)
    message, receive_id = decrypt_wecom_cipher(cipher, WECOM_AES_KEY)
    assert message == INNER_XML
    assert receive_id == WECOM_CORP_ID


def test_wecom_ciphertext_still_fails_closed_without_encoding_aes_key() -> None:
    with pytest.raises(ImError, match="OBSION_WECOM_ENCODING_AES_KEY"):
        parse_inbound("wecom", {"Encrypt": "cipher"})
    with pytest.raises(ImError, match="OBSION_WECOM_ENCODING_AES_KEY"):
        parse_posted(
            "wecom",
            "<xml><Encrypt><![CDATA[cipher]]></Encrypt></xml>",
            "text/xml",
            secret=None,
        )


def test_prepare_wecom_payload_decrypts_signed_json_encrypt() -> None:
    cipher = encrypt_wecom_cipher(INNER_XML, WECOM_AES_KEY, WECOM_CORP_ID)
    timestamp = "1710000000"
    nonce = "n2"
    payload = {
        "timestamp": timestamp,
        "nonce": nonce,
        "msg_signature": wecom_signature(WECOM_TOKEN, timestamp, nonce, cipher),
        "Encrypt": cipher,
    }
    prepared = prepare_wecom_payload(
        payload,
        security=WeComEventSecurity(
            token=WECOM_TOKEN,
            encoding_aes_key=WECOM_AES_KEY,
            corp_id=WECOM_CORP_ID,
        ),
    )
    inbound = parse_inbound("wecom", prepared)
    assert inbound.sender_id == "wecom-alice"
    assert inbound.conversation_id == "wr_ops"
    assert inbound.text == "报表"


def test_prepare_wecom_payload_rejects_bad_signature() -> None:
    cipher = encrypt_wecom_cipher(INNER_XML, WECOM_AES_KEY, WECOM_CORP_ID)
    with pytest.raises(ImError, match="signature is invalid"):
        prepare_wecom_payload(
            {
                "timestamp": "1710000000",
                "nonce": "n2",
                "msg_signature": "deadbeef",
                "Encrypt": cipher,
            },
            security=WeComEventSecurity(
                token=WECOM_TOKEN,
                encoding_aes_key=WECOM_AES_KEY,
                corp_id=WECOM_CORP_ID,
            ),
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"Content": "报表", "FromUserName": "wecom-alice"},
        (
            "<xml><FromUserName><![CDATA[wecom-alice]]></FromUserName>"
            "<Content><![CDATA[报表]]></Content></xml>"
        ),
    ],
)
def test_configured_wecom_security_rejects_plaintext(payload: object) -> None:
    with pytest.raises(ImError, match="requires an encrypted inbound envelope"):
        prepare_wecom_payload(
            payload,
            security=WeComEventSecurity(encoding_aes_key=WECOM_AES_KEY),
        )


def test_token_only_wecom_security_rejects_plaintext_webhook() -> None:
    with pytest.raises(ImError, match="requires an encrypted inbound envelope"):
        parse_posted(
            "wecom",
            '{"FromUserName":"wecom-alice","Content":"报表"}',
            "application/json",
            secret=None,
            wecom_security=WeComEventSecurity(token=WECOM_TOKEN),
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"Encrypt": "cipher", "Content": "报表"},
        {"encrypt": "cipher", "FromUserName": "wecom-alice"},
        (
            "<xml><Encrypt><![CDATA[cipher]]></Encrypt>"
            "<FromUserName><![CDATA[wecom-alice]]></FromUserName></xml>"
        ),
    ],
)
def test_wecom_rejects_mixed_encrypted_plaintext_envelopes(payload: object) -> None:
    with pytest.raises(ImError, match="must not mix Encrypt"):
        prepare_wecom_payload(
            payload,
            security=WeComEventSecurity(encoding_aes_key=WECOM_AES_KEY),
        )
    with pytest.raises(ImError, match="must not mix Encrypt"):
        parse_inbound("wecom", payload)


def test_signed_wecom_encrypt_requires_all_signature_fields() -> None:
    cipher = encrypt_wecom_cipher(INNER_XML, WECOM_AES_KEY, WECOM_CORP_ID)
    security = WeComEventSecurity(
        token=WECOM_TOKEN,
        encoding_aes_key=WECOM_AES_KEY,
        corp_id=WECOM_CORP_ID,
    )
    for payload in (
        {"nonce": "n2", "msg_signature": "signature", "Encrypt": cipher},
        {"timestamp": "1710000000", "msg_signature": "signature", "Encrypt": cipher},
        {"timestamp": "1710000000", "nonce": "n2", "Encrypt": cipher},
    ):
        with pytest.raises(ImError, match="timestamp, nonce, and msg_signature"):
            prepare_wecom_payload(payload, security=security)


def test_prepare_wecom_payload_rejects_receive_id_mismatch() -> None:
    cipher = encrypt_wecom_cipher(INNER_XML, WECOM_AES_KEY, "other-corp")
    with pytest.raises(ImError, match="receive id"):
        prepare_wecom_payload(
            {"Encrypt": cipher},
            security=WeComEventSecurity(encoding_aes_key=WECOM_AES_KEY, corp_id=WECOM_CORP_ID),
        )


def test_echostr_decrypt_becomes_url_verification() -> None:
    cipher = encrypt_wecom_cipher("challenge-wecom", WECOM_AES_KEY, WECOM_CORP_ID)
    prepared = prepare_wecom_payload(
        {"Encrypt": cipher},
        security=WeComEventSecurity(encoding_aes_key=WECOM_AES_KEY, corp_id=WECOM_CORP_ID),
    )
    parsed = parse_inbound("wecom", prepared)
    assert isinstance(parsed, UrlVerification)
    assert parsed.challenge == "challenge-wecom"
    assert parsed.channel == "wecom"


def test_webhook_parses_encrypted_wecom_xml() -> None:
    cipher = encrypt_wecom_cipher(INNER_XML, WECOM_AES_KEY, WECOM_CORP_ID)
    body = f"<xml><Encrypt><![CDATA[{cipher}]]></Encrypt></xml>"
    inbound = parse_posted(
        "wecom",
        body,
        "text/xml",
        secret=None,
        wecom_security=WeComEventSecurity(
            encoding_aes_key=WECOM_AES_KEY,
            corp_id=WECOM_CORP_ID,
        ),
    )
    assert inbound.sender_id == "wecom-alice"
    assert inbound.text == "报表"


def test_wecom_security_loads_from_environment(tmp_path: Path) -> None:
    settings = load_im_settings(
        channel="wecom",
        config_path=tmp_path / "missing.toml",
        environ={
            "OBSION_WECOM_TOKEN": WECOM_TOKEN,
            "OBSION_WECOM_ENCODING_AES_KEY": WECOM_AES_KEY,
            "OBSION_WECOM_CORP_ID": WECOM_CORP_ID,
        },
    )
    assert settings.wecom_security.token == WECOM_TOKEN
    assert settings.wecom_security.encoding_aes_key == WECOM_AES_KEY
    assert WECOM_TOKEN not in repr(settings)
    with pytest.raises(ImError, match="43-character"):
        load_im_settings(
            channel="wecom",
            config_path=tmp_path / "missing.toml",
            environ={"OBSION_WECOM_ENCODING_AES_KEY": "too-short"},
        )


def test_padding_rejects_tampered_ciphertext() -> None:
    key = wecom_aes_key(WECOM_AES_KEY)
    # Valid-looking AES block that is not a WeCom package.
    bogus = base64.b64encode(os.urandom(32)).decode()
    with pytest.raises(ImError):
        decrypt_wecom_cipher(bogus, WECOM_AES_KEY)
    # Ensure the AES key material is not printable in errors.
    assert key
