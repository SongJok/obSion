from __future__ import annotations

import asyncio
import json
import ssl
from collections.abc import Callable, Coroutine, Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from obsion_im.config import (
    LOCAL_DELIVERY,
    DingTalkCredentials,
    FeishuEventSecurity,
    ImError,
    ImSettings,
    WeComEventSecurity,
    normalize_channel,
)
from obsion_im.envelopes import Inbound, parse_inbound, reject_wecom_ciphertext
from obsion_im.signatures import (
    prepare_feishu_http_payload,
    prepare_wecom_payload,
    verify_inbound_signature,
)

MAX_BODY_BYTES = 1_048_576
LOOPBACK_HOST = "127.0.0.1"
Dispatch = Callable[
    [bytes, str, Mapping[str, str]], Coroutine[object, object, tuple[int, str, bytes]]
]


@dataclass(frozen=True, slots=True)
class PublicIngress:
    cert_path: Path
    key_path: Path
    hosts: frozenset[str]

    def ssl_context(self) -> ssl.SSLContext:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(self.cert_path, self.key_path)
        return context


def parse_listen_bind(value: str, *, public: bool = False) -> tuple[str, int]:
    raw = value.strip()
    if not raw:
        raise ImError("Listen address is required")
    if "://" in raw:
        raise ImError("Listen address must be host:port, not a URL")
    host = LOOPBACK_HOST
    port = 0
    if ":" in raw:
        host, _, port_text = raw.rpartition(":")
        host = host.strip() or LOOPBACK_HOST
        if port_text.strip():
            try:
                port = int(port_text)
            except ValueError as exc:
                raise ImError("Listen port must be an integer") from exc
    else:
        host = raw
    if host == "localhost":
        host = LOOPBACK_HOST
    if public:
        if host == LOOPBACK_HOST:
            raise ImError("Public IM webhook cannot bind loopback")
        if not _public_bind_host(host):
            raise ImError("Public IM webhook bind host is invalid")
    elif host != LOOPBACK_HOST:
        raise ImError("IM webhook may only bind 127.0.0.1 unless --public is set")
    if port < 0 or port > 65535:
        raise ImError("Listen port is out of range")
    return host, port


def resolve_public_ingress(
    *,
    public: bool,
    channel: str,
    feishu_security: FeishuEventSecurity,
    wecom_security: WeComEventSecurity | None = None,
    dingtalk_credentials: DingTalkCredentials | None = None,
    environ: Mapping[str, str],
) -> PublicIngress | None:
    if not public:
        return None
    namespace = normalize_channel(channel)
    if namespace == "feishu":
        if not feishu_security.encrypt_key:
            raise ImError("Public Feishu webhook requires OBSION_FEISHU_ENCRYPT_KEY")
    elif namespace == "wecom":
        security = wecom_security or WeComEventSecurity()
        if not security.encoding_aes_key:
            raise ImError("Public WeCom webhook requires OBSION_WECOM_ENCODING_AES_KEY")
        if not security.token:
            raise ImError("Public WeCom webhook requires OBSION_WECOM_TOKEN")
    elif namespace == "dingtalk":
        secret = (environ.get("OBSION_IM_WEBHOOK_SECRET") or "").strip()
        if dingtalk_credentials is None and not secret:
            raise ImError(
                "Public DingTalk webhook requires OBSION_DINGTALK_APP_SECRET "
                "or OBSION_IM_WEBHOOK_SECRET"
            )
    else:
        raise ImError("Public IM webhook is implemented for feishu, dingtalk, and wecom only")
    cert_raw = (environ.get("OBSION_IM_TLS_CERT") or "").strip()
    key_raw = (environ.get("OBSION_IM_TLS_KEY") or "").strip()
    cert_path = Path(cert_raw)
    key_path = Path(key_raw)
    if not cert_raw or not key_raw or not cert_path.is_file() or not key_path.is_file():
        raise ImError("Public IM webhook requires OBSION_IM_TLS_CERT and OBSION_IM_TLS_KEY files")
    hosts = _parse_public_hosts(environ.get("OBSION_IM_PUBLIC_HOSTS"))
    if not hosts:
        raise ImError("Public IM webhook requires OBSION_IM_PUBLIC_HOSTS")
    return PublicIngress(cert_path=cert_path, key_path=key_path, hosts=hosts)


def _public_bind_host(host: str) -> bool:
    if host in {"0.0.0.0", "::"}:  # noqa: S104
        return True
    if host.startswith("[") or "/" in host or "@" in host:
        return False
    labels = host.casefold().split(".")
    if "localhost" in labels:
        return False
    return bool(host) and all(label for label in labels)


def _parse_public_hosts(raw: str | None) -> frozenset[str]:
    if not raw or not raw.strip():
        return frozenset()
    hosts = {_normalize_host(item) for item in raw.split(",") if item.strip()}
    return frozenset(host for host in hosts if host)


def _normalize_host(value: str) -> str:
    host = value.strip().casefold()
    if host.startswith("[") and "]" in host:
        host = host[1 : host.index("]")]
    elif host.count(":") == 1:
        host, _, _port = host.partition(":")
    return host


def parse_posted(
    channel: str,
    body: str,
    content_type: str,
    *,
    secret: str | None,
    headers: Mapping[str, str] | None = None,
    feishu_security: FeishuEventSecurity | None = None,
    wecom_security: WeComEventSecurity | None = None,
    raw_body: bytes | None = None,
) -> Inbound:
    namespace = normalize_channel(channel)
    payload: object
    lowered = content_type.split(";", 1)[0].strip().lower()
    request_headers = headers or {}
    if namespace == "feishu" and feishu_security is not None:
        payload = prepare_feishu_http_payload(
            raw_body if raw_body is not None else body.encode("utf-8"),
            request_headers,
            feishu_security,
        )
        if secret is not None and official_headers_absent(request_headers):
            verify_inbound_signature(namespace, payload, secret=secret)
        return parse_inbound(namespace, payload)
    if "xml" in lowered or body.lstrip().startswith("<"):
        payload = body
        if secret is not None and (
            wecom_security is None or wecom_security.encoding_aes_key is None
        ):
            raise ImError("Signed WeCom XML must be wrapped in a JSON object")
    else:
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise ImError("Envelope must be valid JSON") from exc
        if not isinstance(parsed, dict):
            raise ImError("Envelope must be a JSON object")
        payload = parsed
        if namespace != "wecom":
            verify_inbound_signature(namespace, payload, secret=secret)
    if namespace == "wecom":
        if wecom_security is not None and (
            wecom_security.token is not None or wecom_security.encoding_aes_key is not None
        ):
            payload = prepare_wecom_payload(
                payload,
                security=wecom_security,
                require_signature=secret is not None or wecom_security.token is not None,
            )
        else:
            reject_wecom_ciphertext(payload)
            if isinstance(payload, dict):
                verify_inbound_signature(namespace, payload, secret=secret)
    return parse_inbound(namespace, payload)


def _feishu_verification_mode(settings: ImSettings) -> str:
    if settings.channel == "feishu":
        if settings.feishu_security.encrypt_key:
            return "official"
        return "local_or_unsigned"
    if settings.channel == "wecom":
        if settings.wecom_security.encoding_aes_key:
            return "encoding_aes_key"
        return "unsigned"
    if settings.channel == "dingtalk":
        if settings.dingtalk_credentials is not None:
            return "app_secret"
        return "local_or_unsigned"
    return "unsigned"


def official_headers_absent(headers: Mapping[str, str]) -> bool:
    from obsion_im.signatures import official_feishu_headers

    return official_feishu_headers(headers) is None


class LoopbackWebhookServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        bind: tuple[str, int],
        dispatch: Dispatch,
        loop: asyncio.AbstractEventLoop,
        *,
        delivery: str = LOCAL_DELIVERY,
        feishu_verification: str = "unsigned",
        public_ingress: PublicIngress | None = None,
    ):
        self.dispatch = dispatch
        self.loop = loop
        self.delivery = delivery
        self.feishu_verification = feishu_verification
        self.public_ingress = public_ingress
        super().__init__(bind, _WebhookHandler)
        if public_ingress is not None:
            self.socket = public_ingress.ssl_context().wrap_socket(self.socket, server_side=True)


class _WebhookHandler(BaseHTTPRequestHandler):
    server: LoopbackWebhookServer

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return None

    def do_GET(self) -> None:  # noqa: N802
        if not self._host_allowed():
            self._send(403, "application/json", b'{"error":"host_denied"}')
            return
        path = self.path.split("?", 1)[0]
        if path in {"/", "/healthz"}:
            self._send(
                200,
                "application/json",
                json.dumps(
                    {
                        "delivery": self.server.delivery,
                        "exposure": (
                            "public" if self.server.public_ingress is not None else "loopback"
                        ),
                        "feishu_verification": self.server.feishu_verification,
                        "vendor_verification": self.server.feishu_verification,
                        "status": "ok",
                        "tls": self.server.public_ingress is not None,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode(),
            )
            return
        self._send(404, "application/json", b'{"error":"not_found"}')

    def do_POST(self) -> None:  # noqa: N802
        if not self._host_allowed():
            self._send(403, "application/json", b'{"error":"host_denied"}')
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._send(400, "application/json", b'{"error":"invalid_content_length"}')
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self._send(413, "application/json", b'{"error":"payload_too_large"}')
            return
        body = self.rfile.read(length)
        content_type = self.headers.get("Content-Type") or "application/json"
        headers = {str(key): str(value) for key, value in self.headers.items()}
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.server.dispatch(body, content_type, headers),
                self.server.loop,
            )
            status, content_type, payload = future.result(timeout=120)
        except ImError as exc:
            self._send(
                400,
                "application/json",
                json.dumps({"error": str(exc)}, ensure_ascii=False).encode(),
            )
            return
        except Exception:
            self._send(500, "application/json", b'{"error":"internal_error"}')
            return
        self._send(status, content_type, payload)

    def _host_allowed(self) -> bool:
        ingress = self.server.public_ingress
        if ingress is None:
            return True
        return _normalize_host(self.headers.get("Host") or "") in ingress.hosts

    def _send(self, status: int, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


async def serve_loopback(
    bind: tuple[str, int],
    dispatch: Dispatch,
) -> LoopbackWebhookServer:
    loop = asyncio.get_running_loop()
    server = LoopbackWebhookServer(bind, dispatch, loop)
    await loop.run_in_executor(None, server.serve_forever)
    return server


async def run_webhook(
    settings: ImSettings,
    secret: str | None,
    bind: tuple[str, int],
    *,
    public_ingress: PublicIngress | None = None,
    on_listen: Callable[[str], None] | None = None,
) -> None:
    from obsion_cli.runtime import ExperienceRuntime
    from obsion_im.bridge import ImBridge, outbound_as_dict
    from obsion_im.channel import ImChannel, create_im_channel
    from obsion_im.envelopes import UrlVerification

    runtime = None
    bridge = None
    transport: ImChannel | None = None
    lock = asyncio.Lock()

    async def dispatch(
        body: bytes, content_type: str, headers: Mapping[str, str]
    ) -> tuple[int, str, bytes]:
        nonlocal runtime, bridge, transport
        parsed = parse_posted(
            settings.channel,
            body.decode("utf-8"),
            content_type,
            secret=secret,
            headers=headers,
            feishu_security=settings.feishu_security,
            wecom_security=settings.wecom_security,
            raw_body=body,
        )
        if isinstance(parsed, UrlVerification):
            payload = json.dumps(
                {
                    "challenge": parsed.challenge,
                    "channel": parsed.channel,
                    "type": "url_verification",
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode()
            return 200, "application/json", payload
        if settings.token is None:
            message = "Set OBSION_TOKEN or pass --token. Tokens are never written to config files."
            return 401, "application/json", json.dumps({"error": message}).encode()
        async with lock:
            if bridge is None:
                runtime = await ExperienceRuntime.connect(settings.cli)
                transport = create_im_channel(settings)
                bridge = ImBridge(runtime, transport, workspace_name=settings.workspace_name)
        outbound = await bridge.handle(parsed)
        payload = json.dumps(
            outbound_as_dict(outbound),
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
        return 200, "application/json", payload

    loop = asyncio.get_running_loop()
    server = LoopbackWebhookServer(
        bind,
        dispatch,
        loop,
        delivery=settings.delivery,
        feishu_verification=_feishu_verification_mode(settings),
        public_ingress=public_ingress,
    )
    host, port = bind[0], server.server_port
    if on_listen is not None:
        scheme = "https" if public_ingress is not None else "http"
        on_listen(f"listening {scheme}://{host}:{port}")
    try:
        await loop.run_in_executor(None, server.serve_forever)
    finally:
        server.shutdown()
        if transport is not None:
            await transport.aclose()
        if runtime is not None:
            await runtime.aclose()
