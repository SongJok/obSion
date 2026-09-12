"""Real Harness/Gateway with synthetic model and HTTP boundaries; no vendor sends."""

import json
from datetime import timedelta
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from managed_outbox_fixture import queued_managed_reply
from sqlalchemy import select
from test_managed_answer_access import (
    test_managed_access_is_rechecked_across_model_work as _managed_answer,
)

from obsion.common.time import utc_now
from obsion.db.models import AuditRecord, DingTalkRobotOutbox, ImDelivery, Policy, Run, Turn
from obsion.domain.enums import DecisionEffect


@pytest.mark.parametrize(
    ("expiry", "group"),
    [(expiry, group) for group in [False, True] for expiry in ["none", "source", "claim"]]
    + [("audience", True), ("member", True), ("classification", True)],
)
def test_real_gateway_rechecks_after_token_before_message(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, expiry: str, group: bool
) -> None:
    from obsion.capabilities.dingtalk_robot import (
        GROUP_SEND_PATH,
        SEND_PATH,
        TOKEN_PATH,
        DingTalkRobotTransport,
    )

    _managed_answer(client, monkeypatch, "none")
    monkeypatch.setenv("OBSION_REPLY_FIXTURE_SECRET", "synthetic-final-send-secret")
    requests = []
    future = utc_now() + timedelta(minutes=10)
    audience_future = utc_now() + timedelta(minutes=3)
    send_path = GROUP_SEND_PATH if group else SEND_PATH

    def respond(request):
        requests.append(request.url.path)
        if request.url.path == TOKEN_PATH:
            if expiry == "source":
                monkeypatch.setattr("obsion.knowledge.source_access.utc_now", lambda: future)
            if expiry == "claim":
                monkeypatch.setattr("obsion.application.dingtalk_outbox.utc_now", lambda: future)
            if expiry == "audience":
                monkeypatch.setattr(
                    "obsion.application.dingtalk_outbox.utc_now", lambda: audience_future
                )
            return httpx.Response(
                200, json={"accessToken": "synthetic-final-token", "expireIn": 7200}
            )
        assert request.url.path == send_path
        assert expiry in {"none", "member", "classification"}, (
            "Expired permission must prevent the message POST"
        )
        sent_text = json.loads(json.loads(request.content)["msgParam"])["content"]
        if expiry in {"member", "classification"}:
            assert sent_text == "任务已完成，结果未在群内公开。请在 Obsion 工作台登录查看。"
        else:
            assert "采购制度要求财务复核" in sent_text
        return httpx.Response(200, json={"processQueryKey": "synthetic-final-receipt"})

    async def dispatch():
        async with client.app.state.database.sessions() as session, session.begin():
            run = await session.scalar(
                select(Run).where(
                    Run.organization_id == client.app.state.settings.dev_organization_id,
                    Run.status == "COMPLETED",
                )
            )
            turn = await session.get(Turn, run.turn_id)
            session.add(
                Policy(
                    organization_id=run.organization_id,
                    name="synthetic final message allow",
                    version=1,
                    priority=10000,
                    effect=DecisionEffect.ALLOW,
                    enabled=True,
                    conditions={"actions": ["im.reply.deliver"]},
                    obligations=[],
                    reason="Synthetic test",
                    created_by=turn.created_by,
                )
            )
            run_id = run.id
        service, _, claim, _ = await queued_managed_reply(
            client,
            run_id,
            real_gateway=client.app.state.capability_gateway,
            group=group,
            extra_group_member=expiry == "member",
            group_classification="INTERNAL" if expiry == "classification" else "RESTRICTED",
        )
        service.transport = DingTalkRobotTransport(transport=httpx.MockTransport(respond))
        async with client.app.state.database.sessions() as session, session.begin():
            result = await service.dispatch(session, claim)
            if expiry in {"none", "member", "classification"}:
                assert result.status == "ACCEPTED"
                assert result.accepted_process_query_key == "synthetic-final-receipt"
            else:
                assert result.status == "QUEUED" and result.accepted_process_query_key is None
                assert result.last_error_code == "im_delivery_denied"
        async with client.app.state.database.sessions() as session:
            persisted = await session.get(DingTalkRobotOutbox, claim.outbox_id)
            assert persisted.status == result.status
            assert persisted.policy_decision_id is not None
        return run_id

    client.portal.call(dispatch)
    assert requests == (
        [TOKEN_PATH, send_path] if expiry in {"none", "member", "classification"} else [TOKEN_PATH]
    )


async def trusted_legacy_context(client, run_id):
    from obsion.db.models import ImInboxEvent, ImPrincipalBinding, KnowledgeSyncSource
    from obsion.knowledge.publication import KnowledgePublicationGuard
    from obsion.security.auth import load_principal_by_id

    async with client.app.state.database.sessions() as session, session.begin():
        run = await session.get(Run, run_id)
        turn = await session.get(Turn, run.turn_id)
        source = await session.scalar(
            select(KnowledgeSyncSource).where(
                KnowledgeSyncSource.organization_id == run.organization_id
            )
        )
        binding = await session.get(ImPrincipalBinding, source.principal_binding_id)
        principal = await load_principal_by_id(session, run.organization_id, turn.created_by)
        # Establish that access itself is valid: the plaintext handoff is denied
        # because it cannot recheck at final send, not because the fixture lacks access.
        assert await KnowledgePublicationGuard().check(
            session,
            principal,
            [],
            run_id=run.id,
            stage="legacy_fixture_access",
            expected_corp_id=source.corp_id,
        )
        event = ImInboxEvent(
            organization_id=run.organization_id,
            installation_id=binding.installation_id,
            binding_id=binding.id,
            accepted_by=turn.created_by,
            channel="dingtalk",
            vendor_event_id="synthetic-legacy-final",
            payload_fingerprint="c" * 64,
            sender_id=binding.sender_id,
            conversation_id="synthetic-legacy-conversation",
            is_group=False,
            intent="QUERY",
            text="采购制度要求什么？",
            event_metadata={"subject_user_id": str(turn.created_by)},
            status="PENDING",
        )
        session.add(event)
        await session.flush()
        event.status = "PROCESSING"
        event.attempt_count = 1
        event.lease_owner = "synthetic-fixture"
        event.lease_expires_at = utc_now() + timedelta(seconds=30)
        await session.flush()
        event.status = "ACCEPTED"
        event.run_id = run.id
        event.processed_at = utc_now()
        event.lease_owner = None
        event.lease_expires_at = None
        turn.context_refs = [
            {
                "type": "im_delivery",
                "channel": "dingtalk",
                "conversation_id": event.conversation_id,
                "sender_id": binding.sender_id,
                "inbox_event_id": str(event.id),
            }
        ]


def test_legacy_plaintext_prepare_does_not_export_valid_managed_answer(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _managed_answer(client, monkeypatch, "none")

    async def run_id():
        async with client.app.state.database.sessions() as session:
            return await session.scalar(
                select(Run.id).where(
                    Run.organization_id == client.app.state.settings.dev_organization_id,
                    Run.status == "COMPLETED",
                )
            )

    identifier = client.portal.call(run_id)
    assert isinstance(identifier, UUID)
    client.portal.call(trusted_legacy_context, client, identifier)
    response = client.post(f"/api/v1/experience/im/runs/{identifier}/deliveries")
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "im_delivery_denied"
    assert "采购制度要求财务复核" not in response.text

    async def denied_without_delivery():
        async with client.app.state.database.sessions() as session:
            assert (
                await session.scalar(select(ImDelivery.id).where(ImDelivery.run_id == identifier))
                is None
            )
            audit = await session.scalar(
                select(AuditRecord).where(
                    AuditRecord.action == "experience.im.delivery.reject",
                    AuditRecord.resource_id == str(identifier),
                    AuditRecord.outcome == "DENIED",
                )
            )
            assert audit is not None and audit.policy_decision_id is not None

    client.portal.call(denied_without_delivery)
