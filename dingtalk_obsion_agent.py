#!/usr/bin/env python3
"""DWS-compatible thin entrypoint for the governed Obsion IM runtime.

The DWS process supplies the sender and conversation environment variables and
captures stdout as the reply. The control plane remains the only place that
creates a Thread/Turn/Run, invokes a model or capability, and authorizes the
answer. This adapter never receives a model credential and keeps no history.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(_ROOT / "apps" / "cli" / "src"))
sys.path.insert(0, str(_ROOT / "apps" / "im-adapter" / "src"))
sys.path.insert(0, str(_ROOT / "packages" / "sdk-python" / "src"))

from obsion_cli.config import CliError, load_settings  # noqa: E402
from obsion_cli.runtime import ExperienceRuntime  # noqa: E402
from obsion_im.bridge import ImBridge  # noqa: E402
from obsion_im.channel import DevelopmentImChannel, InboundMessage  # noqa: E402
from obsion_im.config import ImError  # noqa: E402
from obsion_sdk import ObsionAPIError  # noqa: E402


def extract_question(text: str) -> str:
    """Remove a DWS mention while preserving the user's actual question."""
    value = text.strip()
    if not value:
        return ""
    parts = value.split()
    while parts and parts[0].startswith("@"):
        parts.pop(0)
    return " ".join(parts).strip()


def _environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise CliError(f"{name} is required")
    return value


async def _ask_control_plane(question: str) -> str:
    token = _environment("OBSION_TOKEN")
    base_url = os.getenv("OBSION_API_URL") or os.getenv("OBSION_URL") or "http://127.0.0.1:8080"
    settings = load_settings(url=base_url, token=token, protocol="rest")
    inbound = InboundMessage(
        channel="dingtalk",
        sender_id=_environment("DWS_SENDER_STAFF_ID"),
        conversation_id=_environment("DWS_CONVERSATION_ID"),
        sender_display=os.getenv("DWS_SENDER_NAME") or None,
        text=question,
    )
    runtime = await ExperienceRuntime.connect(settings)
    channel = DevelopmentImChannel()
    try:
        outbound = await ImBridge(runtime, channel, workspace_name="IM").handle(inbound)
        return outbound.text.strip() or "(no answer)"
    finally:
        await runtime.aclose()


def main() -> int:
    if len(sys.argv) < 2:
        print('用法: python dingtalk_obsion_agent.py "<用户提问>"', file=sys.stderr)
        return 2
    question = extract_question(" ".join(sys.argv[1:]))
    if not question:
        print("钉钉消息不能为空", file=sys.stderr)
        return 2
    try:
        answer = asyncio.run(_ask_control_plane(question))
    except (CliError, ImError, ObsionAPIError):
        # Do not echo vendor/control-plane bodies: they may contain identifiers
        # or connector details and DWS only needs a stable failure response.
        print("Obsion 控制面暂时无法处理该请求，请稍后重试或联系管理员。", file=sys.stderr)
        return 1
    except Exception:
        print("Obsion 控制面暂时无法处理该请求，请稍后重试或联系管理员。", file=sys.stderr)
        return 1
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
