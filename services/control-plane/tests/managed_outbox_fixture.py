"""Synthetic durable reply queue; native Gateway tests inject a mock HTTP transport."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

from sqlalchemy import select

from obsion.application.dingtalk_outbox import DingTalkOutboxService
from obsion.common.time import utc_now
from obsion.db.im_models import (
    ImGroupAudience,
    ImInboxMessage,
    ImInstallation,
    ImInstallationBinding,
)
from obsion.db.models import (
    CapabilityBinding,
    CapabilityDefinition,
    CapabilityVersion,
    Connector,
    Run,
    Thread,
    Turn,
    User,
    UserRole,
    WorkspaceMember,
)


async def queued_managed_reply(
    client,
    run_id: UUID,
    *,
    real_gateway=None,
    group=False,
    extra_group_member=False,
    group_classification="INTERNAL",
):
    gateway = real_gateway or SimpleNamespace(
        invoke_dingtalk_robot_outbox=AsyncMock(
            side_effect=AssertionError("Revoked content must never be sent")
        )
    )
    service = DingTalkOutboxService(gateway, lease_seconds=300 if group else 30, max_attempts=3)
    async with client.app.state.database.sessions() as session, session.begin():
        run = await session.get(Run, run_id)
        turn = await session.get(Turn, run.turn_id)
        thread = await session.get(Thread, turn.thread_id)
        connector = Connector(
            organization_id=run.organization_id,
            name="managed-answer-reply-test",
            connector_type="dingtalk-robot",
            environment="test",
            status="ACTIVE",
            credential_ref="env://OBSION_REPLY_FIXTURE_SECRET" if real_gateway else None,
            declared_grants=["im.reply.deliver"] if real_gateway else [],
            allowed_egress=["https://api.dingtalk.com"] if real_gateway else [],
            configuration={
                "protocol": "dingtalk.robot.oto.v1",
                "app_key": "reply-fixture",
                "robot_code": "reply-fixture",
            },
        )
        session.add(connector)
        await session.flush()
        installation = ImInstallation(
            organization_id=run.organization_id,
            provider="dingtalk",
            external_corp_id="corp_test",
            external_app_id="reply-fixture",
            connector_id=connector.id,
            adapter_principal_id=turn.created_by,
            status="ACTIVE",
            created_by=turn.created_by,
            verification_source="synthetic test",
        )
        session.add(installation)
        await session.flush()
        binding = ImInstallationBinding(
            organization_id=run.organization_id,
            installation_id=installation.id,
            sender_id="reply-sender",
            user_id=turn.created_by,
            active=True,
            created_by=turn.created_by,
        )
        session.add(binding)
        await session.flush()
        version = await session.scalar(
            select(CapabilityVersion)
            .join(CapabilityDefinition, CapabilityDefinition.id == CapabilityVersion.capability_id)
            .where(
                CapabilityDefinition.organization_id == run.organization_id,
                CapabilityDefinition.name == "im.dingtalk.robot.reply",
            )
        )
        assert version is not None
        session.add(
            CapabilityBinding(
                organization_id=run.organization_id,
                capability_version_id=version.id,
                connector_id=connector.id,
                environment="test",
                enabled=True,
                resource_selector={},
            )
        )
        if group:
            members = [str(turn.created_by)]
            if extra_group_member:
                member = User(
                    organization_id=run.organization_id,
                    external_id="synthetic-extra-group-member",
                    email="extra-group-member@example.test",
                    display_name="Synthetic group member",
                )
                session.add(member)
                await session.flush()
                role_ids = await session.scalars(
                    select(UserRole.role_id).where(UserRole.user_id == turn.created_by)
                )
                session.add_all(
                    [
                        UserRole(
                            organization_id=run.organization_id, user_id=member.id, role_id=role_id
                        )
                        for role_id in role_ids
                    ]
                )
                session.add(
                    WorkspaceMember(
                        organization_id=run.organization_id,
                        workspace_id=thread.workspace_id,
                        user_id=member.id,
                        permissions=["read"],
                        can_write=False,
                        created_by=turn.created_by,
                        created_at=utc_now(),
                    )
                )
                members.append(str(member.id))
            session.add(
                ImGroupAudience(
                    organization_id=run.organization_id,
                    installation_id=installation.id,
                    conversation_id="fixture-group",
                    workspace_id=thread.workspace_id,
                    member_user_ids=members,
                    member_fingerprint="b" * 64,
                    max_classification=group_classification,
                    allow_final_answer=True,
                    allow_status=True,
                    status="ACTIVE",
                    created_by=turn.created_by,
                    verification_source="synthetic test",
                    verified_at=utc_now(),
                    verified_until=utc_now() + timedelta(minutes=2),
                )
            )
        session.add(
            ImInboxMessage(
                organization_id=run.organization_id,
                installation_id=installation.id,
                vendor_event_id="managed-reply-event",
                fingerprint="a" * 64,
                binding_id=binding.id,
                subject_user_id=turn.created_by,
                sender_id=binding.sender_id,
                conversation_id="fixture-group" if group else "fixture-direct",
                conversation_type="group" if group else "direct",
                text="采购制度要求什么？",
                status="PROCESSED",
                processed_at=run.completed_at,
                turn_id=turn.id,
                run_id=run.id,
            )
        )
        await session.flush()
        outbox = await service.enqueue_next(session)
        assert outbox is not None and outbox.status == "QUEUED", (
            outbox.blocked_reason if outbox else "missing"
        )
        assert await service._dispatch_inputs(session, outbox) is not None
        installation_id = installation.id
    async with client.app.state.database.sessions() as session, session.begin():
        claim = await service.claim_next(session)
        assert claim is not None
    return service, gateway, claim, installation_id
