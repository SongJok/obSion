from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from obsion_im.config import FeishuEventSecurity, ImError, WeComEventSecurity, normalize_channel

FEISHU_TIMESTAMP_HEADER = "x-lark-request-timestamp"
FEISHU_NONCE_HEADER = "x-lark-request-nonce"
FEISHU_SIGNATURE_HEADER = "x-lark-signature"


def webhook_secret(environ: Mapping[str, str]) -> str | None:
    value = environ.get("OBSION_IM_WEBHOOK_SECRET")
    if value is None:
        return None
    secret = value.strip()
    return secret or None


def verify_inbound_signature(
    channel: str,
    payload: object,
    *,
    secret: str | None,
) -> None:
    if secret is None:
        return
    namespace = normalize_channel(channel)
    if namespace == "development":
        raise ImError("Development inbound does not accept webhook signatures")
    if not isinstance(payload, dict):
        raise ImError("Signed inbound must be a JSON object")
    if namespace == "feishu":
        _verify_feishu(payload, secret=secret)
        return
    if namespace == "dingtalk":
        _verify_dingtalk(payload, secret=secret)
        return
    _verify_wecom(payload, secret=secret)


def feishu_signature(timestamp: str, nonce: str, secret: str, body: str) -> str:
    material = f"{timestamp}{nonce}{secret}{body}".encode()
    return hashlib.sha256(material).hexdigest()


def official_feishu_signature(timestamp: str, nonce: str, encrypt_key: str, body: bytes) -> str:
    material = timestamp.encode() + nonce.encode() + encrypt_key.encode() + body
    return hashlib.sha256(material).hexdigest()


def official_feishu_headers(headers: Mapping[str, str]) -> dict[str, str] | None:
    lowered = {str(key).lower(): str(value) for key, value in headers.items()}
    timestamp = lowered.get(FEISHU_TIMESTAMP_HEADER, "").strip()
    nonce = lowered.get(FEISHU_NONCE_HEADER, "").strip()
    signature = lowered.get(FEISHU_SIGNATURE_HEADER, "").strip()
    if not timestamp and not nonce and not signature:
        return None
    if not timestamp or not nonce or not signature:
        raise ImError("Official Feishu headers require timestamp, nonce, and signature")
    return {"timestamp": timestamp, "nonce": nonce, "signature": signature}


def verify_official_feishu_signature(
    headers: Mapping[str, str],
    body: bytes,
    *,
    encrypt_key: str,
) -> None:
    official = official_feishu_headers(headers)
    if official is None:
        raise ImError("Official Feishu signature headers are required")
    received = official["signature"]
    if received.lower().startswith("sha256="):
        received = received[7:]
    expected = official_feishu_signature(
        official["timestamp"], official["nonce"], encrypt_key, body
    )
    if not hmac.compare_digest(expected, received.lower()):
        raise ImError("Official Feishu signature is invalid")


def decrypt_feishu_event(ciphertext: str, encrypt_key: str) -> str:
    try:
        raw = base64.b64decode(ciphertext, validate=True)
    except (ValueError, TypeError) as exc:
        raise ImError("Feishu encrypted event was not valid base64") from exc
    if len(raw) < 32 or len(raw) % 16 != 0:
        raise ImError("Feishu encrypted event is too short")
    key = hashlib.sha256(encrypt_key.encode()).digest()
    iv, data = raw[:16], raw[16:]
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    padded = decryptor.update(data) + decryptor.finalize()
    if not padded:
        raise ImError("Feishu encrypted event was empty")
    pad = padded[-1]
    if pad < 1 or pad > 16 or padded[-pad:] != bytes([pad]) * pad:
        raise ImError("Feishu encrypted event padding is invalid")
    return padded[:-pad].decode("utf-8")


def wecom_aes_key(encoding_aes_key: str) -> bytes:
    if len(encoding_aes_key) != 43:
        raise ImError("WeCom EncodingAESKey must be 43 characters")
    try:
        key = base64.b64decode(encoding_aes_key + "=", validate=True)
    except (ValueError, TypeError) as exc:
        raise ImError("WeCom EncodingAESKey was not valid base64") from exc
    if len(key) != 32:
        raise ImError("WeCom EncodingAESKey must decode to 32 bytes")
    return key


def decrypt_wecom_cipher(ciphertext: str, encoding_aes_key: str) -> tuple[str, str]:
    """Decrypt WeCom AES package to (message, receive_id)."""
    key = wecom_aes_key(encoding_aes_key)
    try:
        raw = base64.b64decode(ciphertext, validate=True)
    except (ValueError, TypeError) as exc:
        raise ImError("WeCom encrypted content was not valid base64") from exc
    if len(raw) < 32 or len(raw) % 16 != 0:
        raise ImError("WeCom encrypted content is too short")
    decryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
    padded = decryptor.update(raw) + decryptor.finalize()
    if not padded:
        raise ImError("WeCom encrypted content was empty")
    pad = padded[-1]
    if pad < 1 or pad > 32 or padded[-pad:] != bytes([pad]) * pad:
        raise ImError("WeCom encrypted content padding is invalid")
    plain = padded[:-pad]
    if len(plain) < 20:
        raise ImError("WeCom encrypted content is missing the message header")
    content = plain[16:]
    msg_len = int.from_bytes(content[:4], "big")
    if msg_len < 0 or 4 + msg_len > len(content):
        raise ImError("WeCom encrypted content length is invalid")
    message = content[4 : 4 + msg_len].decode("utf-8")
    receive_id = content[4 + msg_len :].decode("utf-8")
    return message, receive_id


def encrypt_wecom_cipher(message: str, encoding_aes_key: str, receive_id: str) -> str:
    """Test helper and round-trip encoder for the documented WeCom AES package."""
    import os

    key = wecom_aes_key(encoding_aes_key)
    msg = message.encode("utf-8")
    receive = receive_id.encode("utf-8")
    plain = os.urandom(16) + len(msg).to_bytes(4, "big") + msg + receive
    pad = 32 - (len(plain) % 32)
    if pad == 0:
        pad = 32
    padded = plain + bytes([pad]) * pad
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    return base64.b64encode(encryptor.update(padded) + encryptor.finalize()).decode()


def prepare_feishu_http_payload(
    body: bytes,
    headers: Mapping[str, str],
    security: FeishuEventSecurity,
) -> dict[str, object]:
    official = official_feishu_headers(headers)
    if official is not None:
        if security.encrypt_key is None:
            raise ImError("Official Feishu signature headers require OBSION_FEISHU_ENCRYPT_KEY")
        verify_official_feishu_signature(headers, body, encrypt_key=security.encrypt_key)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ImError("Envelope must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ImError("Envelope must be a JSON object")
    encrypted = str(payload.get("encrypt") or "").strip()
    if encrypted and not any(
        key in payload for key in ("schema", "header", "event", "type", "challenge")
    ):
        if security.encrypt_key is None:
            raise ImError("Feishu encrypted events require OBSION_FEISHU_ENCRYPT_KEY")
        if official is None:
            raise ImError("Encrypted Feishu events require official signature headers")
        try:
            decrypted = json.loads(decrypt_feishu_event(encrypted, security.encrypt_key))
        except json.JSONDecodeError as exc:
            raise ImError("Decrypted Feishu event was not valid JSON") from exc
        if not isinstance(decrypted, dict):
            raise ImError("Decrypted Feishu event must be a JSON object")
        payload = decrypted
    elif security.encrypt_key is not None and official is None:
        if str(payload.get("type") or "") != "url_verification":
            raise ImError(
                "Feishu encrypt key is set; official X-Lark-Signature headers are required"
            )
    if security.verification_token is not None:
        token = str(payload.get("token") or "").strip()
        header = payload.get("header")
        if isinstance(header, dict) and not token:
            token = str(header.get("token") or "").strip()
        if token != security.verification_token:
            raise ImError("Feishu verification token does not match")
    return payload


def prepare_wecom_payload(
    payload: object,
    *,
    security: WeComEventSecurity,
    require_signature: bool = False,
) -> object:
    """Validate and decrypt WeCom Encrypt packages without plaintext fallback."""
    encrypt = _wecom_encrypt_field(payload)
    plaintext = _wecom_has_plaintext(payload)
    if encrypt and plaintext:
        raise ImError("WeCom inbound must not mix Encrypt with plaintext message fields")
    if not encrypt:
        if security.token is not None or security.encoding_aes_key is not None:
            raise ImError("Configured WeCom event security requires an encrypted inbound envelope")
        return payload
    if security.encoding_aes_key is None:
        raise ImError("WeCom AES ciphertext decrypt requires OBSION_WECOM_ENCODING_AES_KEY")
    if not isinstance(payload, dict):
        if security.token is not None:
            raise ImError("WeCom encrypted inbound requires timestamp, nonce, and msg_signature")
        if require_signature:
            raise ImError("WeCom encrypted inbound requires OBSION_WECOM_TOKEN")
    if isinstance(payload, dict):
        timestamp = str(payload.get("timestamp") or payload.get("TimeStamp") or "").strip()
        nonce = str(payload.get("nonce") or payload.get("Nonce") or "").strip()
        signature = str(
            payload.get("msg_signature")
            or payload.get("MsgSignature")
            or payload.get("signature")
            or ""
        ).strip()
        if security.token is not None:
            if not timestamp or not nonce or not signature:
                raise ImError(
                    "WeCom encrypted inbound requires timestamp, nonce, and msg_signature"
                )
            expected = wecom_signature(security.token, timestamp, nonce, encrypt)
            if not hmac.compare_digest(expected, signature):
                raise ImError("WeCom inbound signature is invalid")
        elif require_signature:
            raise ImError("WeCom encrypted inbound requires OBSION_WECOM_TOKEN")
    message, receive_id = decrypt_wecom_cipher(encrypt, security.encoding_aes_key)
    if security.corp_id is not None and receive_id and receive_id != security.corp_id:
        raise ImError("WeCom encrypted receive id does not match OBSION_WECOM_CORP_ID")
    if message.lstrip().startswith("<"):
        return message
    if not message.strip():
        raise ImError("WeCom decrypted content was empty")
    # URL verification echostr decrypts to a bare challenge string.
    return {
        "type": "url_verification",
        "challenge": message,
    }


def _wecom_encrypt_field(payload: object) -> str:
    if isinstance(payload, str):
        from obsion_im.envelopes import _xml_field

        return _xml_field(payload, "Encrypt")
    if isinstance(payload, dict):
        return str(payload.get("Encrypt") or payload.get("encrypt") or "").strip()
    return ""


def _wecom_has_plaintext(payload: object) -> bool:
    if isinstance(payload, str):
        from obsion_im.envelopes import _xml_field

        return bool(_xml_field(payload, "Content") or _xml_field(payload, "FromUserName"))
    if isinstance(payload, dict):
        return bool(
            str(payload.get("Content") or "").strip()
            or str(payload.get("FromUserName") or "").strip()
        )
    return False


def dingtalk_signature(timestamp: str, secret: str) -> str:
    digest = hmac.new(secret.encode(), timestamp.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def wecom_signature(token: str, timestamp: str, nonce: str, echostr: str) -> str:
    ordered = "".join(sorted((token, timestamp, nonce, echostr)))
    return hashlib.sha1(ordered.encode()).hexdigest()  # noqa: S324


def canonical_event_body(payload: dict[str, object]) -> str:
    event = payload.get("event")
    if isinstance(event, dict):
        return json.dumps(event, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _verify_feishu(payload: dict[str, object], *, secret: str) -> None:
    timestamp = str(payload.get("timestamp") or "")
    nonce = str(payload.get("nonce") or "")
    signature = str(payload.get("signature") or "")
    if not timestamp or not nonce or not signature:
        raise ImError("Feishu inbound is missing timestamp, nonce, or signature")
    expected = feishu_signature(timestamp, nonce, secret, canonical_event_body(payload))
    if not hmac.compare_digest(expected, signature):
        raise ImError("Feishu inbound signature is invalid")


def _verify_dingtalk(payload: dict[str, object], secret: str) -> None:
    timestamp = str(payload.get("timestamp") or "")
    signature = str(payload.get("sign") or payload.get("signature") or "")
    if not timestamp or not signature:
        raise ImError("DingTalk inbound is missing timestamp or sign")
    expected = dingtalk_signature(timestamp, secret)
    if not hmac.compare_digest(expected, signature):
        raise ImError("DingTalk inbound signature is invalid")


def _verify_wecom(payload: dict[str, object], secret: str) -> None:
    timestamp = str(payload.get("timestamp") or "")
    nonce = str(payload.get("nonce") or "")
    echostr = str(payload.get("echostr") or payload.get("encrypt") or "")
    signature = str(payload.get("msg_signature") or payload.get("signature") or "")
    if not timestamp or not nonce or not signature:
        raise ImError("WeCom inbound is missing timestamp, nonce, or signature")
    expected = wecom_signature(secret, timestamp, nonce, echostr)
    if not hmac.compare_digest(expected, signature):
        raise ImError("WeCom inbound signature is invalid")
