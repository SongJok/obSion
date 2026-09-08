from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import obsion.config as obsion_config
from obsion.common.time import utc_now
from obsion.config import Settings
from obsion.db.models import (
    Connector,
    ImDelivery,
    Organization,
    PolicyDecision,
    Run,
    Thread,
    Turn,
    User,
    Workspace,
)
from obsion.domain.enums import ConnectorStatus, DecisionEffect, ImDeliveryStatus, RiskLevel

_ROOT = Path(__file__).resolve().parents[4]
_BASE_REVISION = "f3d4e5a6b7c8"
_INBOX_REVISION = "a81b92c03d14"


def _engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, poolclass=NullPool)


def test_postgres_im_migration_round_trip_and_safe_ledger_preservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.getenv("OBSION_RUN_IM_MIGRATION_TEST") != "1":
        pytest.skip("destructive PostgreSQL IM migration test requires explicit opt-in")
    settings = Settings(_env_file=None)
    assert settings.database_url.startswith("postgresql+asyncpg://"), "PostgreSQL is required"
    asyncio.run(_require_empty_database(settings.database_url))
    monkeypatch.setattr(obsion_config, "get_settings", lambda: settings)
    config = Config(str(_ROOT / "services/control-plane/alembic.ini"))
    config.set_main_option("script_location", str(_ROOT / "services/control-plane/alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    head_revision = ScriptDirectory.from_config(config).get_current_head()
    assert head_revision is not None

    # 独立空库往返，不能复用其他集成测试数据库或任何业务数据库。
    command.upgrade(config, _BASE_REVISION)
    command.upgrade(config, "head")
    command.check(config)
    assert asyncio.run(_dingtalk_outbox_schema(settings.database_url))
    command.downgrade(config, _BASE_REVISION)
    command.upgrade(config, "head")
    command.check(config)
    command.downgrade(config, _BASE_REVISION)

    organization_id, user_id, connector_id, deliveries = asyncio.run(
        _insert_legacy_deliveries(settings.database_url)
    )
    command.upgrade(config, "head")
    command.check(config)
    snapshot = asyncio.run(_delivery_snapshot(settings.database_url))
    assert snapshot == {
        deliveries[0]: ("UNKNOWN", None),
        deliveries[1]: ("UNKNOWN", None),
        deliveries[2]: ("SENT", "vendor-migration-receipt"),
    }
    for unsafe_state in ("UNKNOWN", "PENDING", "FAILED"):
        asyncio.run(_set_unresolved_state(settings.database_url, deliveries[:2], unsafe_state))
        with pytest.raises(RuntimeError, match="Reconcile all non-SENT"):
            command.downgrade(config, _INBOX_REVISION)
        assert asyncio.run(_revision(settings.database_url)) == head_revision
        assert asyncio.run(_delivery_snapshot(settings.database_url))[deliveries[2]] == (
            "SENT",
            "vendor-migration-receipt",
        )

    # 此处是测试 fixture 的人工对账，不代表真实厂商回执验证。
    asyncio.run(_set_unresolved_state(settings.database_url, deliveries[:2], "SENT"))
    command.downgrade(config, _INBOX_REVISION)
    command.upgrade(config, "head")
    command.check(config)
    installation_id = asyncio.run(
        _insert_installation(settings.database_url, organization_id, user_id, connector_id)
    )
    with pytest.raises(RuntimeError, match="populated IM admission ledger"):
        command.downgrade(config, _BASE_REVISION)
    assert asyncio.run(_installation_exists(settings.database_url, installation_id))
    command.upgrade(config, "head")
    command.check(config)


async def _require_empty_database(database_url: str) -> None:
    engine = _engine(database_url)
    try:
        async with engine.connect() as connection:
            tables = await connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                )
            )
            assert tables == 0, "IM migration test refuses a populated database"
    finally:
        await engine.dispose()


async def _insert_legacy_deliveries(database_url: str) -> tuple[UUID, UUID, UUID, list[UUID]]:
    engine = _engine(database_url)
    try:
        async with (
            async_sessionmaker(engine, expire_on_commit=False)() as session,
            session.begin(),
        ):
            organization = Organization(slug="im-migration-fixture", name="IM migration")
            session.add(organization)
            await session.flush()
            user = User(
                organization_id=organization.id,
                external_id="im-migration-adapter",
                email="im-migration@example.test",
                display_name="Migration adapter",
            )
            session.add(user)
            await session.flush()
            connector = Connector(
                organization_id=organization.id,
                name="im-migration-connector",
                connector_type="dingtalk-docs",
                status=ConnectorStatus.ACTIVE,
                environment="test",
            )
            workspace = Workspace(
                organization_id=organization.id, name="IM migration", owner_id=user.id
            )
            session.add_all([connector, workspace])
            await session.flush()
            thread = Thread(
                organization_id=organization.id,
                workspace_id=workspace.id,
                title="IM migration",
                created_by=user.id,
            )
            session.add(thread)
            await session.flush()
            ids = []
            for ordinal, state in enumerate(("PENDING", "FAILED", "SENT"), start=1):
                turn = Turn(
                    organization_id=organization.id,
                    thread_id=thread.id,
                    ordinal=ordinal,
                    created_by=user.id,
                    input_text="fixture",
                    sanitized_input="fixture",
                    created_at=utc_now(),
                )
                session.add(turn)
                await session.flush()
                run = Run(organization_id=organization.id, turn_id=turn.id)
                session.add(run)
                await session.flush()
                decision = PolicyDecision(
                    organization_id=organization.id,
                    run_id=run.id,
                    principal_id=user.id,
                    action="im.reply.deliver",
                    resource={},
                    context={},
                    risk_level=RiskLevel.L1,
                    effect=DecisionEffect.ALLOW,
                    input_fingerprint="a" * 64,
                    created_at=utc_now(),
                )
                session.add(decision)
                await session.flush()
                delivery = ImDelivery(
                    organization_id=organization.id,
                    run_id=run.id,
                    channel="dingtalk",
                    conversation_id="migration-conversation",
                    content_fingerprint="b" * 64,
                    status=ImDeliveryStatus(state),
                    policy_decision_id=decision.id,
                    requested_by=user.id,
                    vendor_message_id="vendor-migration-receipt" if state == "SENT" else None,
                )
                session.add(delivery)
                await session.flush()
                ids.append(delivery.id)
            return organization.id, user.id, connector.id, ids
    finally:
        await engine.dispose()


async def _delivery_snapshot(database_url: str) -> dict[UUID, tuple[str, str | None]]:
    engine = _engine(database_url)
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(
                select(ImDelivery.id, ImDelivery.status, ImDelivery.vendor_message_id)
            )
            return {row[0]: (str(row[1]), row[2]) for row in rows}
    finally:
        await engine.dispose()


async def _set_unresolved_state(database_url: str, ids: list[UUID], state: str) -> None:
    engine = _engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(
                update(ImDelivery).where(ImDelivery.id.in_(ids)).values(status=state)
            )
    finally:
        await engine.dispose()


async def _revision(database_url: str) -> str:
    engine = _engine(database_url)
    try:
        async with engine.connect() as connection:
            return str(await connection.scalar(text("SELECT version_num FROM alembic_version")))
    finally:
        await engine.dispose()


async def _dingtalk_outbox_schema(database_url: str) -> bool:
    """Confirm the durable robot ledger is present in the migration fixture."""

    engine = _engine(database_url)
    try:
        async with engine.connect() as connection:
            columns = {
                str(item)
                for item in await connection.scalars(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_schema = 'public' AND table_name = 'dingtalk_robot_outbox'"
                    )
                )
            }
            constraints = {
                str(item)
                for item in await connection.scalars(
                    text(
                        "SELECT conname FROM pg_constraint "
                        "WHERE conrelid = 'dingtalk_robot_outbox'::regclass"
                    )
                )
            }
            indexes = {
                str(item)
                for item in await connection.scalars(
                    text(
                        "SELECT indexname FROM pg_indexes "
                        "WHERE schemaname = 'public' AND tablename = 'dingtalk_robot_outbox'"
                    )
                )
            }
            return (
                {
                    "status",
                    "attempt_count",
                    "fencing_token",
                    "next_attempt_at",
                    "lease_expires_at",
                    "vendor_send_status",
                    "vendor_read_status",
                    "vendor_read_at",
                    "last_reconciled_at",
                    "reconciliation_attempt_count",
                    "conversation_type",
                    "recipient_conversation_id",
                    "audience_id",
                    "audience_member_fingerprint",
                    "delivery_mode",
                    "answer_classification",
                }
                <= columns
                and {
                    "ck_dingtalk_robot_outbox_attempts_nonnegative",
                    "ck_dingtalk_robot_outbox_fencing_nonnegative",
                    "ck_dingtalk_robot_outbox_reconciliation_attempts_nonnegative",
                    "ck_dingtalk_robot_outbox_dingtalk_outbox_conversation_target",
                    "ck_dingtalk_robot_outbox_dingtalk_outbox_conversation_type",
                    "ck_dingtalk_robot_outbox_dingtalk_outbox_answer_classification",
                    "uq_dingtalk_robot_outbox_run",
                    "uq_dingtalk_robot_outbox_inbox",
                }
                <= constraints
                and {
                    "ix_dingtalk_robot_outbox_last_reconciled_at",
                }
                <= indexes
            )
    finally:
        await engine.dispose()


async def _insert_installation(
    database_url: str, organization_id: UUID, user_id: UUID, connector_id: UUID
) -> UUID:
    engine = _engine(database_url)
    installation_id = uuid4()
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO im_installations "
                    "(id, organization_id, provider, external_corp_id, external_app_id, "
                    "connector_id, adapter_principal_id, status, created_by, verification_source) "
                    "VALUES (:id, :org, 'dingtalk', 'migration-corp', 'migration-app', :connector, "
                    ":user, 'ACTIVE', :user, 'migration-fixture')"
                ),
                {
                    "id": installation_id,
                    "org": organization_id,
                    "connector": connector_id,
                    "user": user_id,
                },
            )
        return installation_id
    finally:
        await engine.dispose()


async def _installation_exists(database_url: str, installation_id: UUID) -> bool:
    engine = _engine(database_url)
    try:
        async with engine.connect() as connection:
            return bool(
                await connection.scalar(
                    text("SELECT EXISTS (SELECT 1 FROM im_installations WHERE id = :id)"),
                    {"id": installation_id},
                )
            )
    finally:
        await engine.dispose()
