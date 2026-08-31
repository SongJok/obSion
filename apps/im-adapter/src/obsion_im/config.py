from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from obsion_cli.config import CliError, CliSettings, load_settings

IDENTITY_NAMESPACES = frozenset({"development", "feishu", "dingtalk", "wecom"})
LOCAL_DELIVERY = "local_outbox"
FEISHU_HTTP_DELIVERY = "feishu_http"
DINGTALK_HTTP_DELIVERY = "dingtalk_http"
WECOM_HTTP_DELIVERY = "wecom_http"
VENDOR_HTTP_DELIVERIES = frozenset(
    {FEISHU_HTTP_DELIVERY, DINGTALK_HTTP_DELIVERY, WECOM_HTTP_DELIVERY}
)
CHANNEL_ALIASES = {
    "lark": "feishu",
    "dingding": "dingtalk",
    "wechat_work": "wecom",
}
VENDOR_CREDENTIAL_KEYS = frozenset(
    {
        "app_id",
        "app_key",
        "app_secret",
        "encrypt_key",
        "webhook_secret",
        "client_secret",
        "corp_id",
        "corp_secret",
        "agent_id",
        "aes_key",
        "encoding_aes_key",
    }
)


class ImError(CliError):
    """User-facing IM adapter failure that must not include credentials."""


@dataclass(frozen=True, slots=True)
class FeishuCredentials:
    app_id: str = field(repr=False)
    app_secret: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class DingTalkCredentials:
    app_key: str = field(repr=False)
    app_secret: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class WeComCredentials:
    corp_id: str = field(repr=False)
    corp_secret: str = field(repr=False)
    agent_id: int = field(repr=False)


@dataclass(frozen=True, slots=True)
class FeishuEventSecurity:
    encrypt_key: str | None = field(default=None, repr=False)
    verification_token: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class WeComEventSecurity:
    token: str | None = field(default=None, repr=False)
    encoding_aes_key: str | None = field(default=None, repr=False)
    corp_id: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ImSettings:
    cli: CliSettings
    channel: str
    workspace_name: str = "IM"
    delivery: str = LOCAL_DELIVERY
    outbox_path: Path | None = None
    feishu_credentials: FeishuCredentials | None = field(default=None, repr=False)
    dingtalk_credentials: DingTalkCredentials | None = field(default=None, repr=False)
    wecom_credentials: WeComCredentials | None = field(default=None, repr=False)
    feishu_security: FeishuEventSecurity = field(default_factory=FeishuEventSecurity, repr=False)
    wecom_security: WeComEventSecurity = field(default_factory=WeComEventSecurity, repr=False)

    @property
    def token(self) -> str | None:
        return self.cli.token


def normalize_channel(channel: str) -> str:
    return CHANNEL_ALIASES.get(channel.strip().lower(), channel.strip().lower())


def require_local_delivery(delivery: str) -> str:
    """Require the inspectable local outbox transport."""
    normalized = delivery.strip().lower().replace("-", "_")
    if normalized == LOCAL_DELIVERY:
        return LOCAL_DELIVERY
    raise ImError(
        "Generic HTTP delivery is not implemented. Use local-outbox, or an explicit "
        "feishu-http / dingtalk-http / wecom-http transport with environment credentials."
    )


def resolve_delivery(
    delivery: str,
    *,
    channel: str,
    feishu_credentials: FeishuCredentials | None,
    dingtalk_credentials: DingTalkCredentials | None,
    wecom_credentials: WeComCredentials | None,
) -> str:
    normalized = delivery.strip().lower().replace("-", "_")
    if normalized == LOCAL_DELIVERY:
        return LOCAL_DELIVERY
    if normalized == FEISHU_HTTP_DELIVERY:
        if channel != "feishu":
            raise ImError("feishu-http delivery requires --channel feishu")
        if feishu_credentials is None:
            raise ImError(
                "feishu-http delivery requires OBSION_FEISHU_APP_ID and OBSION_FEISHU_APP_SECRET"
            )
        return FEISHU_HTTP_DELIVERY
    if normalized == DINGTALK_HTTP_DELIVERY:
        if channel != "dingtalk":
            raise ImError("dingtalk-http delivery requires --channel dingtalk")
        if dingtalk_credentials is None:
            raise ImError(
                "dingtalk-http delivery requires OBSION_DINGTALK_APP_KEY and "
                "OBSION_DINGTALK_APP_SECRET"
            )
        return DINGTALK_HTTP_DELIVERY
    if normalized == WECOM_HTTP_DELIVERY:
        if channel != "wecom":
            raise ImError("wecom-http delivery requires --channel wecom")
        if wecom_credentials is None:
            raise ImError(
                "wecom-http delivery requires OBSION_WECOM_CORP_ID, "
                "OBSION_WECOM_CORP_SECRET, and OBSION_WECOM_AGENT_ID"
            )
        return WECOM_HTTP_DELIVERY
    return require_local_delivery(normalized)


def reject_remote_outbox(path: Path | None) -> Path | None:
    if path is None:
        return None
    raw = str(path)
    if "://" in raw or raw.startswith(("http:", "https:")):
        raise ImError("Outbox must be a local file path, not an HTTP URL")
    return path


def load_im_settings(
    *,
    url: str | None = None,
    token: str | None = None,
    protocol: str | None = None,
    channel: str | None = None,
    json_output: bool = False,
    config_path: Path | None = None,
    environ: Mapping[str, str] | None = None,
    delivery: str | None = None,
    outbox_path: Path | None = None,
) -> ImSettings:
    env = dict(environ) if environ is not None else None
    cli = load_settings(
        url=url,
        token=token,
        protocol=protocol,
        json_output=json_output,
        config_path=config_path,
        environ=env,
    )
    mapping: Mapping[str, str] = environ if environ is not None else os.environ
    _reject_vendor_secrets(config_path, mapping)
    resolved = normalize_channel(channel or mapping.get("OBSION_IM_CHANNEL") or "development")
    if resolved not in IDENTITY_NAMESPACES:
        raise ImError(
            "Channel must be development, feishu, dingtalk, or wecom. "
            "Vendor names are identity namespaces, not HTTP clients."
        )
    feishu_credentials = _load_feishu_credentials(mapping)
    dingtalk_credentials = _load_dingtalk_credentials(mapping)
    wecom_credentials = _load_wecom_credentials(mapping)
    feishu_security = _load_feishu_security(mapping)
    wecom_security = _load_wecom_security(mapping)
    resolved_delivery = resolve_delivery(
        delivery or mapping.get("OBSION_IM_DELIVER") or LOCAL_DELIVERY,
        channel=resolved,
        feishu_credentials=feishu_credentials,
        dingtalk_credentials=dingtalk_credentials,
        wecom_credentials=wecom_credentials,
    )
    resolved_outbox = reject_remote_outbox(
        outbox_path
        if outbox_path is not None
        else (Path(mapping["OBSION_IM_OUTBOX"]) if mapping.get("OBSION_IM_OUTBOX") else None)
    )
    if resolved_delivery != LOCAL_DELIVERY and resolved_outbox is not None:
        raise ImError("Outbox paths are only valid with local-outbox delivery")
    return ImSettings(
        cli=cli,
        channel=resolved,
        delivery=resolved_delivery,
        outbox_path=resolved_outbox,
        feishu_credentials=feishu_credentials,
        dingtalk_credentials=dingtalk_credentials,
        wecom_credentials=wecom_credentials,
        feishu_security=feishu_security,
        wecom_security=wecom_security,
    )


def _load_feishu_security(mapping: Mapping[str, str]) -> FeishuEventSecurity:
    encrypt_key = (mapping.get("OBSION_FEISHU_ENCRYPT_KEY") or "").strip() or None
    verification_token = (mapping.get("OBSION_FEISHU_VERIFICATION_TOKEN") or "").strip() or None
    return FeishuEventSecurity(encrypt_key=encrypt_key, verification_token=verification_token)


def _load_wecom_security(mapping: Mapping[str, str]) -> WeComEventSecurity:
    token = (mapping.get("OBSION_WECOM_TOKEN") or "").strip() or None
    encoding_aes_key = (mapping.get("OBSION_WECOM_ENCODING_AES_KEY") or "").strip() or None
    corp_id = (mapping.get("OBSION_WECOM_CORP_ID") or "").strip() or None
    if encoding_aes_key is not None and len(encoding_aes_key) != 43:
        raise ImError("OBSION_WECOM_ENCODING_AES_KEY must be a 43-character EncodingAESKey")
    return WeComEventSecurity(token=token, encoding_aes_key=encoding_aes_key, corp_id=corp_id)


def _load_feishu_credentials(mapping: Mapping[str, str]) -> FeishuCredentials | None:
    app_id = (mapping.get("OBSION_FEISHU_APP_ID") or "").strip()
    app_secret = (mapping.get("OBSION_FEISHU_APP_SECRET") or "").strip()
    if bool(app_id) != bool(app_secret):
        raise ImError("OBSION_FEISHU_APP_ID and OBSION_FEISHU_APP_SECRET must be set together")
    if not app_id:
        return None
    return FeishuCredentials(app_id=app_id, app_secret=app_secret)


def _load_dingtalk_credentials(mapping: Mapping[str, str]) -> DingTalkCredentials | None:
    app_key = (mapping.get("OBSION_DINGTALK_APP_KEY") or "").strip()
    app_secret = (mapping.get("OBSION_DINGTALK_APP_SECRET") or "").strip()
    if bool(app_key) != bool(app_secret):
        raise ImError("OBSION_DINGTALK_APP_KEY and OBSION_DINGTALK_APP_SECRET must be set together")
    if not app_key:
        return None
    return DingTalkCredentials(app_key=app_key, app_secret=app_secret)


def _load_wecom_credentials(mapping: Mapping[str, str]) -> WeComCredentials | None:
    corp_id = (mapping.get("OBSION_WECOM_CORP_ID") or "").strip()
    corp_secret = (mapping.get("OBSION_WECOM_CORP_SECRET") or "").strip()
    agent_raw = (mapping.get("OBSION_WECOM_AGENT_ID") or "").strip()
    if (corp_secret or agent_raw) and (not corp_id or not corp_secret or not agent_raw):
        raise ImError(
            "OBSION_WECOM_CORP_ID, OBSION_WECOM_CORP_SECRET, and OBSION_WECOM_AGENT_ID "
            "must be set together for wecom-http delivery"
        )
    if not corp_id or not corp_secret or not agent_raw:
        return None
    try:
        agent_id = int(agent_raw)
    except ValueError as exc:
        raise ImError("OBSION_WECOM_AGENT_ID must be a positive integer") from exc
    if agent_id <= 0:
        raise ImError("OBSION_WECOM_AGENT_ID must be a positive integer")
    return WeComCredentials(corp_id=corp_id, corp_secret=corp_secret, agent_id=agent_id)


def _reject_vendor_secrets(config_path: Path | None, environ: Mapping[str, str]) -> None:
    import tomllib

    path = config_path
    if path is None:
        configured = environ.get("OBSION_CONFIG")
        path = (
            Path(configured) if configured else Path.home() / ".config" / "obsion" / "config.toml"
        )
    if not path.is_file():
        return
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return
    keys = _collect_keys(document)
    if VENDOR_CREDENTIAL_KEYS & keys:
        raise ImError(
            "Config files must not contain vendor IM credentials. "
            "Use namespaced OBSION_FEISHU_*, OBSION_DINGTALK_*, or OBSION_WECOM_* "
            "variables and secret storage instead."
        )


def _collect_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.add(str(key).lower())
            keys |= _collect_keys(nested)
    elif isinstance(value, list):
        for item in value:
            keys |= _collect_keys(item)
    return keys
