from __future__ import annotations

import ast
import time
from pathlib import Path

from fastapi.testclient import TestClient

WEB_ROOT = Path(__file__).resolve().parents[3] / "apps" / "web"
IM_ROOT = Path(__file__).resolve().parents[3] / "apps" / "im-adapter" / "src" / "obsion_im"


def _wait_terminal(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return run
        time.sleep(0.05)
    raise AssertionError(f"Run did not reach a terminal state: {run_id}")


def _completed_vendor_run(
    client: TestClient, *, channel: str, sender_id: str, conversation_id: str
) -> str:
    user = client.post(
        "/api/v1/admin/users",
        json={
            "external_id": f"phase68-{channel}-user",
            "email": f"phase68-{channel}@obsion.dev",
            "display_name": f"Phase 68 {channel}",
            "attributes": {},
        },
    )
    assert user.status_code == 201, user.text
    binding = client.post(
        "/api/v1/admin/im-bindings",
        json={
            "channel": channel,
            "sender_id": sender_id,
            "user_id": user.json()["id"],
        },
    )
    assert binding.status_code == 201, binding.text
    accepted = client.post(
        "/api/v1/experience/im/messages",
        json={
            "channel": channel,
            "sender_id": sender_id,
            "conversation_id": conversation_id,
            "text": "你好",
        },
    )
    assert accepted.status_code == 202, accepted.text
    run_id = accepted.json()["run_id"]
    assert _wait_terminal(client, run_id)["status"] == "COMPLETED"
    return run_id


def test_dingtalk_and_wecom_deliveries_are_policy_authorized(client: TestClient) -> None:
    for channel, sender_id, conversation_id in (
        ("dingtalk", "staff_phase68", "cid_phase68"),
        ("wecom", "wecom_phase68", "wr_phase68"),
    ):
        run_id = _completed_vendor_run(
            client,
            channel=channel,
            sender_id=sender_id,
            conversation_id=conversation_id,
        )
        prepared = client.post(f"/api/v1/experience/im/runs/{run_id}/deliveries")
        assert prepared.status_code == 200, prepared.text
        payload = prepared.json()
        assert payload["channel"] == channel
        assert payload["conversation_id"] == conversation_id
        assert payload["status"] == "PENDING"
        completed = client.post(
            f"/api/v1/experience/im/deliveries/{payload['id']}/complete",
            json={"vendor_message_id": f"{channel}-msg-1"},
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "SENT"


def test_dingtalk_wecom_http_are_explicit_and_not_a_second_harness() -> None:
    admin = (WEB_ROOT / "src" / "components" / "admin-view.tsx").read_text(encoding="utf-8")
    assert "dingtalk-http" in admin
    assert "wecom-http" in admin
    assert "oapi.dingtalk.com" in admin
    assert "qyapi.weixin.qq.com" in admin
    assert "im.reply.deliver" in admin
    assert "--deliver http" in admin
    for module_name in ("dingtalk.py", "wecom.py"):
        tree = ast.parse((IM_ROOT / module_name).read_text(encoding="utf-8"))
        imports = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        ]
        assert "obsion.harness.runtime" not in imports
        assert "obsion.db.models" not in imports
    config = (IM_ROOT / "config.py").read_text(encoding="utf-8")
    assert "dingtalk-http" in config
    assert "wecom-http" in config
    assert "Generic HTTP delivery is not implemented" in config
