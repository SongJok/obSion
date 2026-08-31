from __future__ import annotations

import asyncio
import json
import ssl
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from obsion_im.config import DingTalkCredentials, FeishuEventSecurity, ImError, WeComEventSecurity
from obsion_im.envelopes import UrlVerification
from obsion_im.webhook import (
    LoopbackWebhookServer,
    parse_listen_bind,
    parse_posted,
    resolve_public_ingress,
)


def test_listen_bind_rejects_non_loopback_and_urls() -> None:
    assert parse_listen_bind("127.0.0.1") == ("127.0.0.1", 0)
    assert parse_listen_bind("localhost:8787") == ("127.0.0.1", 8787)
    with pytest.raises(ImError, match="127.0.0.1"):
        parse_listen_bind("0.0.0.0:8787")
    with pytest.raises(ImError, match="not a URL"):
        parse_listen_bind("http://127.0.0.1:8787")
    assert parse_listen_bind("im.example.com:8787", public=True) == (
        "im.example.com",
        8787,
    )
    assert parse_listen_bind("0.0.0.0:8787", public=True) == ("0.0.0.0", 8787)  # noqa: S104
    with pytest.raises(ImError, match="cannot bind loopback"):
        parse_listen_bind("127.0.0.1:8787", public=True)


def test_parse_posted_rejects_wecom_ciphertext() -> None:
    with pytest.raises(ImError, match="OBSION_WECOM_ENCODING_AES_KEY"):
        parse_posted(
            "wecom",
            "<xml><Encrypt><![CDATA[cipher]]></Encrypt></xml>",
            "text/xml",
            secret=None,
        )


@pytest.mark.asyncio
async def test_loopback_webhook_handles_health_and_url_verification() -> None:
    async def dispatch(
        body: bytes, content_type: str, _headers: dict[str, str]
    ) -> tuple[int, str, bytes]:
        parsed = parse_posted("feishu", body.decode("utf-8"), content_type, secret=None)
        if isinstance(parsed, UrlVerification):
            payload = json.dumps(
                {"challenge": parsed.challenge, "type": "url_verification"},
                sort_keys=True,
            ).encode()
            return 200, "application/json", payload
        return 400, "application/json", b"{}"

    loop = asyncio.get_running_loop()
    server = LoopbackWebhookServer(("127.0.0.1", 0), dispatch, loop)
    task = loop.run_in_executor(None, server.serve_forever)
    port = server.server_address[1]
    origin = f"http://127.0.0.1:{port}"
    try:
        health = await loop.run_in_executor(None, lambda: _get(f"{origin}/healthz"))
        assert health["status"] == "ok"
        assert health["delivery"] == "local_outbox"
        assert health["exposure"] == "loopback"
        assert health["tls"] is False
        challenge = await loop.run_in_executor(
            None,
            lambda: _post(
                origin,
                {"type": "url_verification", "challenge": "challenge-loopback"},
            ),
        )
        assert challenge["challenge"] == "challenge-loopback"
    finally:
        server.shutdown()
        await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_loopback_webhook_does_not_post_to_vendor_hosts() -> None:
    async def dispatch(
        body: bytes, content_type: str, _headers: dict[str, str]
    ) -> tuple[int, str, bytes]:
        parse_posted("feishu", body.decode("utf-8"), content_type, secret=None)
        return 401, "application/json", json.dumps({"error": "token required"}).encode()

    loop = asyncio.get_running_loop()
    server = LoopbackWebhookServer(("127.0.0.1", 0), dispatch, loop)
    task = loop.run_in_executor(None, server.serve_forever)
    try:
        with pytest.raises(urllib.error.HTTPError) as caught:
            await loop.run_in_executor(
                None,
                lambda: _post(
                    f"http://127.0.0.1:{server.server_address[1]}",
                    {
                        "schema": "2.0",
                        "header": {"event_type": "im.message.receive_v1"},
                        "event": {
                            "sender": {"sender_id": {"open_id": "ou_alice"}},
                            "message": {
                                "chat_id": "oc_ops",
                                "message_type": "text",
                                "content": json.dumps({"text": "你好"}),
                            },
                        },
                    },
                ),
            )
        assert caught.value.code == 401
    finally:
        server.shutdown()
        await asyncio.wait_for(task, timeout=5)


def test_public_ingress_requires_feishu_tls_and_hosts(tmp_path: Path) -> None:
    cert, key = _tls_material(tmp_path)
    security = FeishuEventSecurity(encrypt_key="test key")
    with pytest.raises(ImError, match="ENCODING_AES_KEY"):
        resolve_public_ingress(
            public=True,
            channel="wecom",
            feishu_security=security,
            wecom_security=WeComEventSecurity(),
            environ={},
        )
    with pytest.raises(ImError, match="ENCRYPT_KEY"):
        resolve_public_ingress(
            public=True,
            channel="feishu",
            feishu_security=FeishuEventSecurity(),
            environ={},
        )
    with pytest.raises(ImError, match="TLS_CERT"):
        resolve_public_ingress(
            public=True,
            channel="feishu",
            feishu_security=security,
            environ={},
        )
    with pytest.raises(ImError, match="PUBLIC_HOSTS"):
        resolve_public_ingress(
            public=True,
            channel="feishu",
            feishu_security=security,
            environ={
                "OBSION_IM_TLS_CERT": str(cert),
                "OBSION_IM_TLS_KEY": str(key),
            },
        )
    ingress = resolve_public_ingress(
        public=True,
        channel="feishu",
        feishu_security=security,
        environ={
            "OBSION_IM_TLS_CERT": str(cert),
            "OBSION_IM_TLS_KEY": str(key),
            "OBSION_IM_PUBLIC_HOSTS": "im.example.com",
        },
    )
    assert ingress is not None
    assert ingress.hosts == frozenset({"im.example.com"})
    assert (
        resolve_public_ingress(
            public=False,
            channel="feishu",
            feishu_security=security,
            environ={},
        )
        is None
    )


def test_public_ingress_allows_dingtalk_and_wecom_with_channel_security(
    tmp_path: Path,
) -> None:
    cert, key = _tls_material(tmp_path)
    tls_env = {
        "OBSION_IM_TLS_CERT": str(cert),
        "OBSION_IM_TLS_KEY": str(key),
        "OBSION_IM_PUBLIC_HOSTS": "im.example.com",
    }
    with pytest.raises(ImError, match="WECOM_TOKEN"):
        resolve_public_ingress(
            public=True,
            channel="wecom",
            feishu_security=FeishuEventSecurity(),
            wecom_security=WeComEventSecurity(encoding_aes_key="a" * 43),
            environ=tls_env,
        )
    wecom = resolve_public_ingress(
        public=True,
        channel="wecom",
        feishu_security=FeishuEventSecurity(),
        wecom_security=WeComEventSecurity(encoding_aes_key="a" * 43, token="token"),
        environ=tls_env,
    )
    assert wecom is not None
    with pytest.raises(ImError, match="DINGTALK_APP_SECRET|WEBHOOK_SECRET"):
        resolve_public_ingress(
            public=True,
            channel="dingtalk",
            feishu_security=FeishuEventSecurity(),
            environ=tls_env,
        )
    dingtalk = resolve_public_ingress(
        public=True,
        channel="dingtalk",
        feishu_security=FeishuEventSecurity(),
        dingtalk_credentials=DingTalkCredentials(app_key="k", app_secret="s"),
        environ=tls_env,
    )
    assert dingtalk is not None
    with pytest.raises(ImError, match="feishu, dingtalk, and wecom"):
        resolve_public_ingress(
            public=True,
            channel="development",
            feishu_security=FeishuEventSecurity(),
            environ=tls_env,
        )


@pytest.mark.asyncio
async def test_public_webhook_requires_allowed_host_and_tls(tmp_path: Path) -> None:
    cert, key = _tls_material(tmp_path)
    ingress = resolve_public_ingress(
        public=True,
        channel="feishu",
        feishu_security=FeishuEventSecurity(encrypt_key="test key"),
        environ={
            "OBSION_IM_TLS_CERT": str(cert),
            "OBSION_IM_TLS_KEY": str(key),
            "OBSION_IM_PUBLIC_HOSTS": "im.example.com",
        },
    )
    assert ingress is not None

    async def dispatch(
        body: bytes, content_type: str, _headers: dict[str, str]
    ) -> tuple[int, str, bytes]:
        del body, content_type, _headers
        return 200, "application/json", b"{}"

    loop = asyncio.get_running_loop()
    server = LoopbackWebhookServer(
        ("127.0.0.1", 0),
        dispatch,
        loop,
        public_ingress=ingress,
    )
    task = loop.run_in_executor(None, server.serve_forever)
    port = server.server_address[1]
    context = ssl.create_default_context(cafile=str(cert))
    context.check_hostname = False
    try:
        denied = await loop.run_in_executor(
            None,
            lambda: _get(
                f"https://127.0.0.1:{port}/healthz",
                context=context,
                headers={"Host": "evil.example"},
                expect_error=True,
            ),
        )
        assert denied == 403
        health = await loop.run_in_executor(
            None,
            lambda: _get(
                f"https://127.0.0.1:{port}/healthz",
                context=context,
                headers={"Host": "im.example.com"},
            ),
        )
        assert health["exposure"] == "public"
        assert health["tls"] is True
        assert health["feishu_verification"] == "unsigned"
    finally:
        server.shutdown()
        await asyncio.wait_for(task, timeout=5)


def _tls_material(tmp_path: Path) -> tuple[Path, Path]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "im.example.com")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("im.example.com")]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "tls.crt"
    key_path = tmp_path / "tls.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    return cert_path, key_path


def _get(
    url: str,
    *,
    context: ssl.SSLContext | None = None,
    headers: dict[str, str] | None = None,
    expect_error: bool = False,
) -> dict[str, object] | int:
    request = urllib.request.Request(  # noqa: S310
        url,
        headers=headers or {},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=2, context=context) as response:  # noqa: S310
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if expect_error:
            return exc.code
        raise


def _post(url: str, payload: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(  # noqa: S310
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=2) as response:  # noqa: S310
        return json.loads(response.read())
