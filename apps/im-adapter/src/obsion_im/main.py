from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TextIO

from obsion_cli.runtime import ExperienceRuntime
from obsion_im.bridge import ImBridge, outbound_as_dict
from obsion_im.channel import InboundMessage, OutboundMessage, create_im_channel
from obsion_im.config import (
    LOCAL_DELIVERY,
    DingTalkCredentials,
    ImError,
    ImSettings,
    WeComEventSecurity,
    load_im_settings,
)
from obsion_im.dingtalk_stream import DingTalkStreamAdapter, parse_dingtalk_http_event
from obsion_im.envelopes import UrlVerification, parse_inbound
from obsion_im.outbox import ImOutboxWorker
from obsion_im.signatures import verify_inbound_signature, webhook_secret
from obsion_im.webhook import parse_listen_bind, resolve_public_ingress, run_webhook
from obsion_sdk import ObsionAPIError, ObsionAppServerError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="obsion-im",
        description=(
            "Obsion Experience IM adapter. Translates inbound messages onto the App Server. "
            "development, feishu, dingtalk, and wecom are identity namespaces. "
            "Outbound replies default to vendor-shaped local outbox envelopes. "
            "Explicit feishu-http, dingtalk-http, and wecom-http use tenant applications. "
            "serve --listen binds 127.0.0.1 unless --public is set with TLS. "
            "Senders must already be bound to a provisioned User; nicknames cannot authorize."
        ),
    )
    parser.add_argument("--url", help="Control plane origin, for example http://127.0.0.1:8080")
    parser.add_argument(
        "--token",
        help="Bearer token. Prefer OBSION_TOKEN instead of shell history.",
    )
    parser.add_argument(
        "--protocol",
        choices=("app-server", "rest"),
        help="Lifecycle protocol. Default app-server; rest is for environments without WebSocket.",
    )
    parser.add_argument(
        "--channel",
        default="development",
        help="Inbound identity namespace: development, feishu, dingtalk, or wecom.",
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument(
        "--config",
        type=Path,
        help="TOML config path. Must not contain credentials.",
    )
    parser.add_argument(
        "--deliver",
        help=(
            "Outbound delivery: local-outbox (default), feishu-http, dingtalk-http, "
            "or wecom-http. Each HTTP transport requires its matching --channel."
        ),
    )
    parser.add_argument(
        "--outbox",
        type=Path,
        help="Append JSONL vendor envelopes to this path. Must not be a vendor HTTP URL.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser(
        "ingest",
        help=(
            "Submit one message. Development uses conversation/text/sender-id; "
            "vendor channels use --envelope."
        ),
    )
    ingest.add_argument("--conversation")
    ingest.add_argument("--text")
    ingest.add_argument(
        "--sender-id",
        help="Stable sender id already bound to a User. Display names are not accepted.",
    )
    ingest.add_argument(
        "--sender-display",
        default=None,
        help="Optional display label. Ignored for authorization.",
    )
    ingest.add_argument(
        "--envelope",
        help="JSON vendor envelope. Required for feishu, dingtalk, and wecom ingest.",
    )

    serve = commands.add_parser(
        "serve",
        help=(
            "Read JSON lines from stdin, or --listen 127.0.0.1[:port] for a loopback "
            "webhook. --public requires TLS, channel-specific vendor security, "
            "and a Host allowlist."
        ),
    )
    serve.add_argument(
        "--listen",
        help=("Bind a webhook, for example 127.0.0.1:8787. Non-loopback binds require --public."),
    )
    serve.add_argument(
        "--public",
        action="store_true",
        help=(
            "Expose a TLS vendor webhook. Requires OBSION_IM_TLS_CERT, "
            "OBSION_IM_TLS_KEY, OBSION_IM_PUBLIC_HOSTS, and channel security "
            "(Feishu Encrypt Key, WeCom EncodingAESKey+Token, or DingTalk secret)."
        ),
    )
    commands.add_parser(
        "health",
        help=(
            "Validate the selected delivery transport without creating a Turn or sending a message."
        ),
    )
    outbox = commands.add_parser(
        "outbox",
        help=(
            "Lease durable outbound deliveries for the selected explicit vendor transport. "
            "UNKNOWN outcomes are held for administrator reconciliation."
        ),
    )
    outbox.add_argument(
        "--once",
        action="store_true",
        help="Claim and process at most one eligible delivery, then exit.",
    )
    outbox.add_argument(
        "--worker-id",
        help="Stable worker identity used for fenced Outbox claims.",
    )
    stream = commands.add_parser(
        "stream",
        help="官方钉钉 Stream：仅持久入站，不发送回复",
    )
    stream.add_argument("--installation-id", help="固定安装 UUID；也可设 OBSION_IM_INSTALLATION_ID")
    worker = commands.add_parser("inbox-worker", help="恢复持久 RECEIVED，仅创建逻辑任务")
    worker.add_argument("--installation-id", help="固定安装 UUID；也可设 OBSION_IM_INSTALLATION_ID")
    worker.add_argument("--once", action="store_true", help="执行一个有界恢复批次后退出")
    worker.add_argument("--limit", type=int, default=20)
    worker.add_argument("--max-events", type=int, default=200)
    worker.add_argument("--poll-interval", type=float, default=1.0)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
    environ: Mapping[str, str] | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    env: Mapping[str, str] = environ if environ is not None else os.environ
    try:
        if args.command in {"stream", "inbox-worker"}:
            return _inbox_command(args, env, out=out, err=err)
        settings = load_im_settings(
            url=args.url,
            token=args.token,
            protocol=args.protocol,
            channel=args.channel,
            json_output=args.json_output,
            config_path=args.config,
            environ=env,
            delivery=args.deliver,
            outbox_path=args.outbox,
        )
        if args.command == "stream" and settings.channel != "dingtalk":
            raise ImError("DingTalk Stream requires --channel dingtalk")
        secret = webhook_secret(env)
        public = bool(getattr(args, "public", False))
        if (
            settings.channel == "dingtalk"
            and secret is None
            and settings.dingtalk_credentials is not None
        ):
            secret = settings.dingtalk_credentials.app_secret
        parsed: InboundMessage | UrlVerification | None = None
        if args.command == "ingest":
            parsed = _ingest_inbound(settings, args, secret=secret)
            if isinstance(parsed, UrlVerification):
                out.write(_render_challenge(parsed, json_output=settings.cli.json_output))
                return 0
        if args.command == "serve" and getattr(args, "listen", None):
            bind = parse_listen_bind(args.listen, public=public)
            public_ingress = resolve_public_ingress(
                public=public,
                channel=settings.channel,
                feishu_security=settings.feishu_security,
                wecom_security=settings.wecom_security,
                dingtalk_credentials=settings.dingtalk_credentials,
                environ=env,
            )

            def _announce(line: str) -> None:
                out.write(f"{line}\n")
                out.flush()

            asyncio.run(
                run_webhook(
                    settings,
                    secret,
                    bind,
                    public_ingress=public_ingress,
                    on_listen=_announce,
                )
            )
            return 0
        if args.command == "health":
            health = asyncio.run(_health(settings))
            out.write(json.dumps(health, ensure_ascii=False, sort_keys=True) + "\n")
            return 0
        if settings.token is None:
            raise ImError(
                "Set OBSION_TOKEN or pass --token. Tokens are never written to config files."
            )
        if args.command == "outbox":
            delivered = asyncio.run(_run_outbox(settings, args))
            if getattr(args, "once", False):
                out.write(json.dumps({"delivered": delivered}, sort_keys=True) + "\n")
            return 0
        if args.command == "stream":
            asyncio.run(_run_dingtalk_stream(settings))
            return 0
        output = asyncio.run(_dispatch(settings, args, inbound=parsed, secret=secret))
        out.write(output)
        return 0
    except (ImError, ObsionAPIError, ObsionAppServerError) as exc:
        err.write(f"{exc}\n")
        return 1
    except KeyboardInterrupt:
        err.write("Interrupted\n")
        return 130


def _inbox_command(
    args: argparse.Namespace, env: Mapping[str, str], *, out: TextIO, err: TextIO
) -> int:
    from dataclasses import asdict
    from uuid import UUID

    from obsion_im.inbox import InboxClient, InboxSettings, WorkerResult, run_inbox_worker
    from obsion_im.stream import StreamSettings, run_stream

    try:
        # 入站模式不能悄悄忽略用户的发送选项，也不创建本地 Outbox。
        if (
            args.deliver is not None
            or args.outbox is not None
            or "OBSION_IM_DELIVER" in env
            or "OBSION_IM_OUTBOX" in env
        ):
            raise ImError("stream/inbox-worker 不支持 --deliver、--outbox 或对应环境变量")
        settings = load_im_settings(
            url=args.url,
            token=args.token,
            protocol=args.protocol,
            channel=args.channel,
            json_output=args.json_output,
            config_path=args.config,
            environ=env,
        )
        if args.command == "stream" and settings.channel != "dingtalk":
            raise ImError("DingTalk Stream requires --channel dingtalk")
        raw_id = args.installation_id or env.get("OBSION_IM_INSTALLATION_ID")
        try:
            installation_id = UUID(raw_id) if raw_id else None
        except (ValueError, TypeError, AttributeError):
            installation_id = None
        if installation_id is None:
            raise ImError("请指定有效 --installation-id UUID 或 OBSION_IM_INSTALLATION_ID")
        inbox = InboxSettings(installation_id, settings.cli.base_url, settings.token or "")
        if args.command == "stream":
            if settings.channel != "dingtalk":
                raise ImError("Stream 要求 --channel dingtalk")
            credentials = settings.dingtalk_credentials
            if credentials is None:
                raise ImError("Stream 需要 OBSION_DINGTALK_APP_KEY 和 OBSION_DINGTALK_APP_SECRET")
            stream = StreamSettings(
                inbox,
                credentials.app_key,
                credentials.app_secret,
                env.get("OBSION_DINGTALK_CORP_ID", ""),
                env.get("OBSION_DINGTALK_STREAM_APP_ID") or None,
            )
            run_stream(stream)
            return 0

        def report_round(result: WorkerResult) -> None:
            out.write(json.dumps(asdict(result), sort_keys=True) + "\n")
            out.flush()

        async def recover() -> WorkerResult:
            client = InboxClient(inbox)
            try:
                return await run_inbox_worker(
                    client,
                    once=args.once,
                    limit=args.limit,
                    max_events=args.max_events,
                    poll_interval=args.poll_interval,
                    on_round=None if args.once else report_round,
                )
            finally:
                await client.aclose()

        result = asyncio.run(recover())
        out.write(json.dumps(asdict(result), sort_keys=True) + "\n")
        return 1 if result.failed or result.polls_failed else 0
    except ImError as exc:
        err.write(f"{exc}\n")
        return 1
    except Exception:
        err.write("Inbox 入口失败；请检查受管配置，事件由持久 Inbox 恢复\n")
        return 1


async def _dispatch(
    settings: ImSettings,
    args: argparse.Namespace,
    *,
    inbound: InboundMessage | UrlVerification | None,
    secret: str | None,
) -> str:
    runtime = await ExperienceRuntime.connect(settings.cli)
    channel = None
    try:
        channel = create_im_channel(settings)
        bridge = ImBridge(runtime, channel, workspace_name=settings.workspace_name)
        if args.command == "ingest":
            if not isinstance(inbound, InboundMessage):
                raise ImError("Inbound message was not parsed")
            outbound = await bridge.handle(inbound)
            return _render(outbound, json_output=settings.cli.json_output)
        replies: list[str] = []
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            parsed = _serve_line(
                settings.channel,
                line,
                secret=secret,
                wecom_security=settings.wecom_security,
                dingtalk_credentials=settings.dingtalk_credentials,
            )
            if isinstance(parsed, UrlVerification):
                replies.append(_render_challenge(parsed, json_output=True).rstrip())
                continue
            outbound = await bridge.handle(parsed)
            replies.append(_render(outbound, json_output=True).rstrip())
        return ("\n".join(replies) + "\n") if replies else ""
    finally:
        if channel is not None:
            await channel.aclose()
        await runtime.aclose()


async def _health(settings: ImSettings) -> dict[str, object]:
    channel = create_im_channel(settings)
    try:
        return await channel.health()
    finally:
        await channel.aclose()


async def _run_outbox(settings: ImSettings, args: argparse.Namespace) -> bool:
    if settings.delivery == LOCAL_DELIVERY:
        raise ImError(
            "The Outbox worker requires an explicit vendor transport: "
            "feishu-http, dingtalk-http, or wecom-http"
        )
    runtime = await ExperienceRuntime.connect(settings.cli)
    channel = None
    try:
        channel = create_im_channel(settings)
        worker = ImOutboxWorker(
            runtime,
            channel,
            worker_id=getattr(args, "worker_id", None),
        )
        return await worker.run(once=bool(getattr(args, "once", False)))
    finally:
        if channel is not None:
            await channel.aclose()
        await runtime.aclose()


async def _run_dingtalk_stream(settings: ImSettings) -> None:
    if settings.channel != "dingtalk":
        raise ImError("DingTalk Stream requires --channel dingtalk")
    if settings.dingtalk_credentials is None:
        raise ImError(
            "DingTalk Stream requires OBSION_DINGTALK_APP_KEY and OBSION_DINGTALK_APP_SECRET"
        )
    runtime = await ExperienceRuntime.connect(settings.cli)
    channel = None
    try:
        channel = create_im_channel(settings)
        bridge = ImBridge(runtime, channel, workspace_name=settings.workspace_name)
        await DingTalkStreamAdapter(bridge, settings.dingtalk_credentials).start()
    finally:
        if channel is not None:
            await channel.aclose()
        await runtime.aclose()


def _ingest_inbound(
    settings: ImSettings, args: argparse.Namespace, *, secret: str | None
) -> InboundMessage | UrlVerification:
    if args.envelope:
        payload: object = _load_json_object(args.envelope, "Envelope")
        if settings.channel == "wecom" and settings.wecom_security.encoding_aes_key is not None:
            from obsion_im.signatures import prepare_wecom_payload

            payload = prepare_wecom_payload(
                payload,
                security=settings.wecom_security,
                require_signature=secret is not None or settings.wecom_security.token is not None,
            )
        elif settings.channel == "dingtalk" and settings.dingtalk_credentials is not None:
            verify_inbound_signature(settings.channel, payload, secret=secret)
            return parse_dingtalk_http_event(
                payload,
                app_key=settings.dingtalk_credentials.app_key,
                credentials=settings.dingtalk_credentials,
            )
        else:
            verify_inbound_signature(settings.channel, payload, secret=secret)
        return parse_inbound(settings.channel, payload)
    if settings.channel != "development":
        raise ImError("Vendor channels require --envelope with a documented callback payload")
    if not args.conversation or not args.text or not args.sender_id:
        raise ImError("Development ingest requires --conversation, --text, and --sender-id")
    return InboundMessage(
        conversation_id=args.conversation,
        text=args.text,
        sender_id=args.sender_id,
        sender_display=args.sender_display,
        channel=settings.channel,
    )


def _serve_line(
    channel: str,
    line: str,
    *,
    secret: str | None,
    wecom_security: WeComEventSecurity | None = None,
    dingtalk_credentials: DingTalkCredentials | None = None,
    dingtalk_app_key: str | None = None,
) -> InboundMessage | UrlVerification:
    from obsion_im.signatures import prepare_wecom_payload

    security = wecom_security
    if channel == "wecom" and line.lstrip().startswith("<"):
        if secret is not None and (security is None or security.encoding_aes_key is None):
            raise ImError("Signed WeCom XML must be wrapped in a JSON object")
        payload: object = line
        if security is not None and security.encoding_aes_key is not None:
            payload = prepare_wecom_payload(
                payload,
                security=security,
                require_signature=False,
            )
        return parse_inbound(channel, payload)
    payload = _load_json_object(line, "Each serve line")
    if channel == "wecom" and security is not None and security.encoding_aes_key is not None:
        payload = prepare_wecom_payload(
            payload,
            security=security,
            require_signature=secret is not None or security.token is not None,
        )
    elif channel == "dingtalk" and (
        dingtalk_credentials is not None or dingtalk_app_key is not None
    ):
        verify_inbound_signature(channel, payload, secret=secret)
        return parse_dingtalk_http_event(
            payload,
            app_key=(
                dingtalk_credentials.app_key
                if dingtalk_credentials is not None
                else dingtalk_app_key or ""
            ),
            credentials=dingtalk_credentials,
        )
    else:
        verify_inbound_signature(channel, payload, secret=secret)
    return parse_inbound(channel, payload)


def _load_json_object(raw: str, label: str) -> dict[str, object]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ImError(f"{label} must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ImError(f"{label} must be a JSON object")
    return payload


def _render_challenge(challenge: UrlVerification, *, json_output: bool) -> str:
    if json_output:
        return (
            json.dumps(
                {
                    "challenge": challenge.challenge,
                    "channel": challenge.channel,
                    "type": "url_verification",
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )
    return f"{challenge.challenge}\n"


def _render(outbound: OutboundMessage, *, json_output: bool) -> str:
    if json_output:
        return json.dumps(outbound_as_dict(outbound), ensure_ascii=False, sort_keys=True) + "\n"
    return f"{outbound.text}\n"


if __name__ == "__main__":
    raise SystemExit(main())
