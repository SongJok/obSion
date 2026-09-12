"""Synthetic managed-source fixture; no external calls or model credentials."""

from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from managed_outbox_fixture import queued_managed_reply
from sqlalchemy import select, update
from test_app_server_api import _initialize, _rpc
from test_knowledge_sync_worker import setup
from test_phase13_knowledge_agent import _create_thread, _wait_terminal

from obsion.capabilities.dingtalk_managed import MANAGED_CONNECTOR_TYPE, MANAGED_READ_OPERATION
from obsion.capabilities.gateway import GatewayResult, GatewayStatus
from obsion.common.time import utc_now
from obsion.db.models import (
    Artifact,
    AuditRecord,
    Connector,
    Document,
    Event,
    KnowledgeSyncItem,
    KnowledgeSyncSource,
    Policy,
    Run,
)
from obsion.domain.enums import DecisionEffect
from obsion.harness.grounding import GroundingAssessment
from obsion.harness.runtime import HarnessRuntime
from obsion.knowledge.publication import KnowledgePublicationGuard


@pytest.mark.parametrize(
    "change",
    [
        "none",
        "before_author",
        "author",
        "review",
        "expiry",
        "policy",
        "version",
        "missing_label",
        "acl",
        "after_publish",
        "replay_copy",
        "queued_reply",
        "wrong_reply_corp",
        "unused_conversation",
    ],
)
def test_managed_access_is_rechecked_across_model_work(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    sentence = "采购制度要求财务复核。"

    async def prepare():
        principal, source, service = await setup(client)
        async with client.app.state.database.sessions() as session, session.begin():
            connector = await session.scalar(
                select(Connector).where(
                    Connector.name == "managed_reader_test",
                    Connector.organization_id == principal.organization_id,
                )
            )
            await service.accept_read(
                session,
                principal,
                source_id=source.id,
                correlation_id=uuid4(),
                result=GatewayResult(
                    status=GatewayStatus.COMPLETED,
                    connector_id=connector.id,
                    policy_decision_id=uuid4(),
                    output={
                        "operation": MANAGED_READ_OPERATION,
                        "adapter": MANAGED_CONNECTOR_TYPE,
                        "corp_id": source.corp_id,
                        "binding_id": str(source.principal_binding_id),
                        "reader_user_id": str(principal.id),
                        "node_id": "document_one",
                        "workspace_id": "workspace_one",
                        "title": "采购制度",
                        "revision": "4",
                        "parser_version": "fixture",
                        "raw_checksum_sha256": "a" * 64,
                        "observed_at": utc_now().isoformat(),
                        "complete": True,
                        "gaps": [],
                        "text": sentence,
                    },
                ),
            )
        return source.id

    source_id = client.portal.call(prepare)
    author_calls = []
    review_calls = []
    original_check = KnowledgePublicationGuard.check

    async def revoke(session):
        source = await session.get(KnowledgeSyncSource, source_id)
        source.active = False
        await session.flush()

    async def check(self, session, principal, evidence, *, run_id, stage, **kwargs):
        if stage == "before_author" and change == "before_author":
            await revoke(session)
        return await original_check(
            self, session, principal, evidence, run_id=run_id, stage=stage, **kwargs
        )

    async def synthesize(self: HarnessRuntime, *args: Any):
        session, records = args[0], args[5]
        if args[1].plan.get("route") == "GENERAL":
            assert not args[7], "Unused enterprise history must not enter a general answer"
            return "机器学习从例子中学习规律。", []
        assert records and records[0].content["hits"]
        author_calls.append(True)
        if change in {"author", "missing_label"}:
            await revoke(session)
            if change == "missing_label":
                records[0].content["hits"][0].pop("source")
        elif change == "expiry":
            item = await session.scalar(
                select(KnowledgeSyncItem).where(
                    KnowledgeSyncItem.source_id == source_id, KnowledgeSyncItem.status == "READY"
                )
            )
            item.checked_at = utc_now() - timedelta(minutes=6)
            item.access_expires_at = utc_now() - timedelta(seconds=1)
            await session.flush()
        elif change == "version":
            records[0].content["hits"][0]["version"] += 1
        elif change == "acl":
            await session.execute(
                update(Document)
                .where(Document.id == UUID(records[0].content["hits"][0]["document_id"]))
                .values(acl={"deny_users": [str(args[2].created_by)]})
                .execution_options(synchronize_session=False)
            )
        elif change == "policy":
            session.add(
                Policy(
                    organization_id=records[0].organization_id,
                    name="publication read denied",
                    version=1,
                    priority=10000,
                    effect=DecisionEffect.DENY,
                    enabled=True,
                    conditions={"actions": ["knowledge.read"]},
                    obligations=[],
                    reason="Synthetic revocation",
                    created_by=args[2].created_by,
                )
            )
            await session.flush()
        return sentence, [
            {"statement": sentence, "evidence_ids": [str(records[0].id)], "confidence": 0.9}
        ]

    async def review(models, session, **kwargs):
        review_calls.append(True)
        if change == "review":
            await revoke(session)
        return GroundingAssessment(
            accepted=True,
            reason_code="supported",
            candidate_fingerprint="a" * 64,
            input_fingerprint="b" * 64,
            claims=(
                {
                    "claim_index": 1,
                    "verdict": "SUPPORTED",
                    "quotes": [
                        {
                            "evidence_id": str(kwargs["evidence"][0].id),
                            "body_index": 0,
                            "quote": sentence,
                        }
                    ],
                },
            ),
        )

    monkeypatch.setattr(KnowledgePublicationGuard, "check", check)
    monkeypatch.setattr(HarnessRuntime, "_synthesize", synthesize)
    monkeypatch.setattr("obsion.harness.runtime.review_knowledge_answer", review)
    thread = _create_thread(client)
    created = client.post(
        f"/api/v1/threads/{thread['id']}/turns", json={"input": "采购制度要求什么？"}
    )
    assert created.status_code == 202, created.text
    run = _wait_terminal(client, created.json()["run"]["id"])
    assert run["status"] == "COMPLETED", run

    async def persisted_projection():
        # Inspect durable audit material internally even when its public route
        # correctly denies content after revocation.
        async with client.app.state.database.sessions() as session:
            artifacts = (
                await session.scalars(select(Artifact).where(Artifact.run_id == UUID(run["id"])))
            ).all()
            events = (
                await session.scalars(select(Event).where(Event.run_id == UUID(run["id"])))
            ).all()
            return (
                [
                    {"id": str(item.id), "title": item.title, "inline_content": item.inline_content}
                    for item in artifacts
                ],
                [{"name": item.name, "payload": item.payload} for item in events],
            )

    artifacts, events = client.portal.call(persisted_projection)
    answer = next(item["inline_content"] for item in artifacts if item["title"] == "Obsion answer")
    if change in {
        "none",
        "after_publish",
        "replay_copy",
        "queued_reply",
        "wrong_reply_corp",
        "unused_conversation",
    }:
        assert answer["verification"]["verified"] and sentence in answer["markdown"]
        assert review_calls
        assert client.get(f"/api/v1/runs/{run['id']}/artifacts").status_code == 200
        if change in {"queued_reply", "wrong_reply_corp"}:
            service, gateway, claim, installation_id = client.portal.call(
                queued_managed_reply, client, UUID(run["id"])
            )

            async def revoke_before_delivery():
                from obsion.db.im_models import ImInstallation

                async with client.app.state.database.sessions() as session, session.begin():
                    if change == "queued_reply":
                        await revoke(session)
                    else:
                        installation = await session.get(ImInstallation, installation_id)
                        installation.external_corp_id = "another-corp"
                async with client.app.state.database.sessions() as session, session.begin():
                    blocked = await service.dispatch(session, claim)
                    assert blocked.status == "BLOCKED"
                    assert blocked.blocked_reason == "delivery_preconditions_changed"
                gateway.invoke_dingtalk_robot_outbox.assert_not_awaited()

            client.portal.call(revoke_before_delivery)
        if change in {"after_publish", "replay_copy", "unused_conversation"}:
            replay_id = None
            general_id = None
            if change == "unused_conversation":
                general = client.post(
                    f"/api/v1/threads/{thread['id']}/turns", json={"input": "用一句话解释机器学习"}
                )
                assert general.status_code == 202, general.text
                general_id = general.json()["run"]["id"]
                general_run = _wait_terminal(client, general_id)
                assert (
                    general_run["status"] == "COMPLETED"
                    and general_run["plan"]["route"] == "GENERAL"
                )
            if change == "replay_copy":
                replay = client.post(f"/api/v1/runs/{run['id']}/replay")
                assert replay.status_code == 202, replay.text
                replay_id = replay.json()["id"]
                replay_run = _wait_terminal(client, replay_id)
                assert replay_run["status"] == "COMPLETED", replay_run
                assert client.get(f"/api/v1/runs/{replay_id}/artifacts").status_code == 200

            async def empty_replay_dependency():
                async with client.app.state.database.sessions() as session, session.begin():
                    original = await session.get(Run, UUID(run["id"]))
                    clone = Run(
                        organization_id=original.organization_id,
                        turn_id=original.turn_id,
                        status="COMPLETED",
                        completed_at=utc_now(),
                        replay_of_run_id=original.id,
                    )
                    session.add(clone)
                    await session.flush()
                    return str(clone.id)

            dependency_id = client.portal.call(empty_replay_dependency)
            assert client.get(f"/api/v1/runs/{dependency_id}/artifacts").status_code == 200

            async def pause():
                async with client.app.state.database.sessions() as session, session.begin():
                    await revoke(session)

            retry_params = {"run_id": run["id"], "client_request_id": "managed-history-cancel"}
            socket_context, socket = _initialize(client)
            try:
                before_retry = _rpc(socket, "before-pause", "run.cancel", retry_params)
                assert before_retry["result"]["source_content_available"] is True
            finally:
                socket_context.__exit__(None, None, None)
            client.portal.call(pause)
            socket_context, socket = _initialize(client)
            try:
                after_retry = _rpc(socket, "after-pause", "run.cancel", retry_params)
            finally:
                socket_context.__exit__(None, None, None)
            assert after_retry["result"]["source_content_available"] is False
            assert after_retry["result"]["intent"] == {} and after_retry["result"]["plan"] == {}

            async def immutable_retry_ledger():
                from obsion.db.models import AppServerRequest

                async with client.app.state.database.sessions() as session:
                    records = (
                        await session.scalars(
                            select(AppServerRequest).where(
                                AppServerRequest.client_request_id
                                == retry_params["client_request_id"]
                            )
                        )
                    ).all()
                    assert len(records) == 1
                    assert records[0].response["result"] == before_retry["result"]

            client.portal.call(immutable_retry_ledger)
            for suffix in [
                "artifacts",
                "claims",
                "evidence",
                "conversation",
                "events",
                "events/stream",
            ]:
                response = client.get(f"/api/v1/runs/{run['id']}/{suffix}")
                assert response.status_code == 404, (suffix, response.text)
            for artifact in artifacts:
                assert client.get(f"/api/v1/artifacts/{artifact['id']}").status_code == 404
            for suffix in ["artifacts", "reports", "evidence", "timeline"]:
                response = client.get(f"/api/v1/workspaces/{thread['workspace_id']}/{suffix}")
                assert response.status_code == 200 and sentence not in response.text
            metadata = client.get(f"/api/v1/runs/{run['id']}")
            assert metadata.status_code == 200 and not metadata.json()["source_content_available"]
            assert metadata.json()["intent"] == {} and metadata.json()["plan"] == {}
            assert client.get(f"/api/v1/runs/{dependency_id}/artifacts").status_code == 404
            assert client.post(f"/api/v1/runs/{run['id']}/replay").status_code == 404
            if replay_id:
                assert client.get(f"/api/v1/runs/{replay_id}/artifacts").status_code == 404
                assert client.get(f"/api/v1/runs/{replay_id}/events").status_code == 404
            if general_id:
                metadata = client.get(f"/api/v1/runs/{general_id}").json()
                assert metadata["source_content_available"] is True
                content = client.get(f"/api/v1/runs/{general_id}/artifacts")
                assert (
                    content.status_code == 200
                    and "机器学习从例子中学习规律。" in content.text
                    and sentence not in content.text
                )
                snapshots = client.get(f"/api/v1/runs/{general_id}/conversation")
                assert snapshots.status_code == 200 and snapshots.json() == []

            async def historical_denial_audit():
                async with client.app.state.database.sessions() as session:
                    rows = (
                        await session.scalars(
                            select(AuditRecord).where(
                                AuditRecord.resource_id == run["id"],
                                AuditRecord.outcome == "DENIED",
                                AuditRecord.redacted_metadata["stage"].as_string()
                                == "historical_content",
                            )
                        )
                    ).all()
                    assert rows and all(row.policy_decision_id for row in rows)

            client.portal.call(historical_denial_audit)
    else:
        assert any(
            "managed_source_access_changed" in conflict.get("reason_codes", [])
            for conflict in answer["verification"]["conflicts"]
        )
        assert not answer["verification"]["verified"] and answer["citations"] == []
        assert "资料权限或版本发生了变化" in answer["markdown"]
        for artifact in artifacts:
            assert sentence not in str(artifact["inline_content"].get("markdown", ""))
        if change != "version":
            assert client.get(f"/api/v1/runs/{run['id']}/artifacts").status_code == 404
            assert client.get(f"/api/v1/runs/{run['id']}/events").status_code == 404
        assert all(
            sentence not in event["payload"]["delta"]
            for event in events
            if event["name"] == "answer.delta"
        )
        if change == "before_author":
            assert not author_calls
        assert bool(review_calls) == (change == "review")

        async def audit():
            async with client.app.state.database.sessions() as session:
                records = (
                    await session.scalars(
                        select(AuditRecord).where(
                            AuditRecord.resource_id == run["id"],
                            AuditRecord.action == "knowledge.read",
                            AuditRecord.outcome == "DENIED",
                        )
                    )
                ).all()
                assert records and all(record.policy_decision_id for record in records)

        client.portal.call(audit)
