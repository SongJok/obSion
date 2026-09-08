"""Persistent group audience controls for the DingTalk delivery boundary."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select
from test_im_inbox import MESSAGE, provision

from obsion.db.im_models import ImGroupAudience


def _workspace(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/v1/workspaces",
        json={"name": "group-workspace", "description": "group"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_group_audience_requires_workspace_membership_and_can_be_revoked(
    client: TestClient,
) -> None:
    installation = provision(client, "audience")
    workspace = _workspace(client)
    auth = installation["auth"]
    payload = {
        "conversation_id": "cid-group",
        "workspace_id": workspace["id"],
        "member_user_ids": [auth["principal_id"]],
        "member_fingerprint": "a" * 64,
        "max_classification": "INTERNAL",
        "allow_final_answer": False,
        "allow_status": True,
        "verification_source": "operator-test",
    }
    created = client.post(
        f"/api/v1/admin/im-installations/{installation['id']}/group-audiences",
        json=payload,
    )
    assert created.status_code == 201, created.text
    audience = created.json()
    assert audience["verified_until"] > audience["verified_at"]
    listed = client.get(f"/api/v1/admin/im-installations/{installation['id']}/group-audiences")
    assert listed.status_code == 200 and listed.json()[0]["id"] == audience["id"]
    revoked = client.post(
        f"/api/v1/admin/im-installations/{installation['id']}/group-audiences/{audience['id']}/revoke"
    )
    assert revoked.status_code == 200 and revoked.json()["status"] == "REVOKED"


def test_group_message_without_audience_is_processed_privately_but_never_queued_for_group(
    client: TestClient,
) -> None:
    installation = provision(client, "unconfigured")
    receipt = client.post(installation["url"], json={**MESSAGE, "conversation_id": "no-audience"})
    assert receipt.status_code == 202
    processed = client.post(f"{installation['url']}/{receipt.json()['id']}/process")
    assert processed.status_code == 200

    async def read() -> int:
        async with client.app.state.database.sessions() as session, session.begin():
            return int(
                await session.scalar(
                    select(ImGroupAudience.id).where(
                        ImGroupAudience.installation_id == UUID(installation["id"])
                    )
                )
                is not None
            )

    assert asyncio.run(read()) == 0
