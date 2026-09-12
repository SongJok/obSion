from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import replace
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_dingtalk_managed_reader import install_fixture

from obsion.capabilities.dingtalk_docs import DingTalkDocsResponseError
from obsion.capabilities.dingtalk_managed import MANAGED_CONNECTOR_TYPE, MANAGED_READ_OPERATION
from obsion.capabilities.gateway import GatewayResult, GatewayStatus
from obsion.common.errors import NotFoundError, ValidationError
from obsion.common.time import utc_now
from obsion.config import Settings
from obsion.db.models import DocumentVersion, ImInstallation, KnowledgeSyncItem, Policy
from obsion.db.project_source_models import ConnectorVersionRevocation
from obsion.domain.enums import DecisionEffect
from obsion.knowledge.service import KnowledgeService
from obsion.knowledge.sync import KnowledgeSyncService
from obsion.knowledge.sync_queue import checkpoint_source, claim_source
from obsion.main import create_app


@pytest.fixture(params=["sqlite", "postgresql"])
def sync_client(app_settings: Settings, request: pytest.FixtureRequest) -> Iterator[TestClient]:
    settings = app_settings
    if request.param == "postgresql":
        if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
            pytest.skip("Explicit disposable PostgreSQL validation required")
        url = os.environ["OBSION_DATABASE_URL"]
        assert url.startswith("postgresql+asyncpg://")
        settings = settings.model_copy(update={"database_url": url})
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.mark.parametrize(
    "revocation",
    [
        "none",
        "source",
        "binding",
        "installation",
        "expiry",
        "configuration",
        "credential",
        "grants",
        "connector",
        "version",
        "incomplete",
        "other_admin",
    ],
)
def test_external_documents_require_current_source_access_even_for_admin(
    sync_client: TestClient,
    revocation: str,
) -> None:
    client = sync_client

    async def run() -> None:
        async with client.app.state.database.sessions() as session, session.begin():
            principal, connector, binding = await install_fixture(
                session, client.app.state.settings
            )
            session.add(
                Policy(
                    organization_id=principal.organization_id,
                    name="source test allow",
                    version=1,
                    priority=1,
                    effect=DecisionEffect.ALLOW,
                    enabled=True,
                    conditions={"action": "knowledge.write"},
                    obligations=[],
                    reason="Test",
                    created_by=principal.id,
                )
            )
            await session.flush()
            knowledge = KnowledgeService(client.app.state.settings, client.app.state.object_store)
            sync = KnowledgeSyncService(knowledge)
            source = await sync.create_source(
                session,
                principal,
                connector_id=connector.id,
                binding_id=binding.id,
                correlation_id=uuid4(),
            )
            repeated = await sync.create_source(
                session,
                principal,
                connector_id=connector.id,
                binding_id=binding.id,
                correlation_id=uuid4(),
            )
            assert repeated.id == source.id
            # Explicit synthetic reader boundary fixture; real Gateway transport is
            # covered separately by test_dingtalk_managed_reader.
            result = GatewayResult(
                status=GatewayStatus.COMPLETED,
                connector_id=connector.id,
                policy_decision_id=uuid4(),
                output={
                    "operation": MANAGED_READ_OPERATION,
                    "adapter": MANAGED_CONNECTOR_TYPE,
                    "corp_id": "corp_test",
                    "binding_id": str(binding.id),
                    "reader_user_id": str(principal.id),
                    "node_id": "document_one",
                    "workspace_id": "workspace_one",
                    "title": "采购制度",
                    "revision": "4",
                    "parser_version": "test-jsonml",
                    "raw_checksum_sha256": "a" * 64,
                    "observed_at": utc_now().isoformat(),
                    "complete": True,
                    "gaps": [],
                    "text": "采购制度规定，报价需要财务复核。",
                },
            )
            if revocation == "none":
                assert result.output is not None
                for bad in (
                    {"complete": "yes"},
                    {"revision": " "},
                    {"observed_at": "invalid"},
                    {"observed_at": (utc_now() + timedelta(hours=1)).isoformat()},
                ):
                    malformed = replace(result, output={**result.output, **bad})
                    with pytest.raises(DingTalkDocsResponseError) as rejected:
                        await sync.accept_read(
                            session,
                            principal,
                            source_id=source.id,
                            result=malformed,
                            correlation_id=uuid4(),
                        )
                    assert rejected.value.code == "dingtalk_docs_response_invalid"
                    assert rejected.value.status_code == 503
            item = await sync.accept_read(
                session, principal, source_id=source.id, result=result, correlation_id=uuid4()
            )
            assert item.status == "READY" and item.document_id is not None
            document_id = item.document_id
            assert await knowledge.search(session, principal, query="采购财务复核")
            document, version = await knowledge.get_document(session, principal, document_id)
            assert version.version == 1 and document.acl["organization"] is False
            original_metadata = dict(version.metadata_json)
            original_expiry = item.access_expires_at
            assert result.output is not None
            result.output["observed_at"] = utc_now().isoformat()
            await sync.accept_read(
                session, principal, source_id=source.id, result=result, correlation_id=uuid4()
            )
            versions = (
                await session.scalars(
                    select(DocumentVersion).where(DocumentVersion.document_id == document_id)
                )
            ).all()
            assert len(versions) == 1 and versions[0].metadata_json == original_metadata
            assert item.access_expires_at > original_expiry
            if revocation == "none":
                result.output["revision"] = "5"
                await sync.accept_read(
                    session, principal, source_id=source.id, result=result, correlation_id=uuid4()
                )
                _, latest = await knowledge.get_document(session, principal, document_id)
                assert latest.version == 2 and latest.metadata_json["revision_id"] == "5"
            if revocation == "source":
                await sync.disable_source(
                    session, principal, source_id=source.id, correlation_id=uuid4()
                )
                registered = await sync.create_source(
                    session,
                    principal,
                    connector_id=connector.id,
                    binding_id=binding.id,
                    correlation_id=uuid4(),
                )
                assert not registered.active
            elif revocation == "binding":
                binding.active = False
            elif revocation == "installation":
                installation = await session.get(ImInstallation, binding.installation_id)
                installation.active = False
            elif revocation == "expiry":
                item.checked_at = utc_now() - timedelta(minutes=10)
                item.access_expires_at = utc_now() - timedelta(minutes=5)
            elif revocation == "configuration":
                connector.configuration = {**connector.configuration, "operator_id": "changed"}
            elif revocation == "credential":
                connector.credential_ref = "env://REPLACED"
            elif revocation == "grants":
                connector.declared_grants = []
            elif revocation == "connector":
                connector.status = "DISABLED"
            elif revocation == "version":
                session.add(
                    ConnectorVersionRevocation(
                        organization_id=principal.organization_id,
                        connector_version_id=source.connector_version_id,
                        revoked_by=principal.id,
                        reason_code="OPERATOR_REVOKED",
                    )
                )
            elif revocation == "incomplete":
                result.output.update(complete=False, gaps=["image_content"])
                partial = await sync.accept_read(
                    session, principal, source_id=source.id, result=result, correlation_id=uuid4()
                )
                assert partial.status == "PARTIAL" and partial.document_id == document_id
            elif revocation == "other_admin":
                principal = replace(principal, id=uuid4(), permissions=frozenset({"*"}))
            await session.flush()
            if revocation != "none":
                assert not await knowledge.search(session, principal, query="采购财务复核")
                with pytest.raises(NotFoundError):
                    await knowledge.get_document(session, principal, document_id)
                with pytest.raises(NotFoundError):
                    await knowledge.get_content(session, principal, document_id)
            else:
                assert await knowledge.search(session, principal, query="采购财务复核")
            await session.rollback()

    client.portal.call(run)


def test_reserved_source_without_access_record_is_not_readable(sync_client: TestClient) -> None:
    client = sync_client

    async def run() -> None:
        async with client.app.state.database.sessions() as session, session.begin():
            principal, _, _ = await install_fixture(session, client.app.state.settings)
            knowledge = KnowledgeService(client.app.state.settings, client.app.state.object_store)
            from obsion.domain.enums import Classification

            document, _, _ = await knowledge.ingest(
                session,
                principal,
                source="dingtalk-managed",
                external_id="unguarded",
                title="Unverified source",
                media_type="text/plain",
                filename="sample.txt",
                content=b"unverified external content",
                classification=Classification.INTERNAL,
                acl={"organization": True},
            )
            assert not await knowledge.search(session, principal, query="unverified external")
            with pytest.raises(NotFoundError):
                await knowledge.get_document(session, principal, document.id)
            await session.rollback()

    client.portal.call(run)


def test_sync_checkpoint_resumes_and_rejects_expired_worker(sync_client: TestClient) -> None:
    client = sync_client

    async def run() -> None:
        async with client.app.state.database.sessions() as session, session.begin():
            principal, connector, binding = await install_fixture(
                session, client.app.state.settings
            )
            session.add(
                Policy(
                    organization_id=principal.organization_id,
                    name="queue test allow",
                    version=1,
                    priority=1,
                    effect=DecisionEffect.ALLOW,
                    enabled=True,
                    conditions={"action": "knowledge.write"},
                    obligations=[],
                    reason="Test",
                    created_by=principal.id,
                )
            )
            await session.flush()
            service = KnowledgeSyncService(
                KnowledgeService(client.app.state.settings, client.app.state.object_store)
            )
            source = await service.create_source(
                session,
                principal,
                connector_id=connector.id,
                binding_id=binding.id,
                correlation_id=uuid4(),
            )
            claimed = await claim_source(session)
            assert claimed.id == source.id and claimed.generation == 1
            token = claimed.lease_token
            old_item = KnowledgeSyncItem(
                organization_id=principal.organization_id,
                source_id=source.id,
                node_id="previous_node",
                workspace_id="workspace_one",
                kind="adoc",
                title="Previous document",
                status="FAILED",
                seen_generation=0,
            )
            session.add(old_item)
            await session.flush()
            assert await claim_source(session) is None
            state = {"stage": "DISCOVER", "queue": ["pending_node"], "cursor": "opaque +/= "}
            assert await checkpoint_source(
                session, source_id=source.id, lease_token=token, generation=1, scan_state=state
            )
            resumed = await claim_source(session)
            assert old_item.status == "FAILED"
            assert resumed.generation == 1 and resumed.scan_state == state
            next_token = resumed.lease_token
            assert next_token != token
            assert not await checkpoint_source(
                session, source_id=source.id, lease_token=token, generation=1, scan_state={}
            )
            with pytest.raises(ValidationError) as error:
                await checkpoint_source(
                    session,
                    source_id=source.id,
                    lease_token=next_token,
                    generation=1,
                    scan_state=state,
                    complete=True,
                )
            assert getattr(error.value, "code", None) == "dingtalk_docs_operation_invalid"
            resumed.lease_expires_at = utc_now() - timedelta(seconds=1)
            await session.flush()
            assert not await checkpoint_source(
                session, source_id=source.id, lease_token=next_token, generation=1, scan_state=state
            )
            reclaimed = await claim_source(session)
            assert reclaimed.generation == 1 and reclaimed.scan_state == state
            complete = {
                "stage": "COMPLETE",
                "queue": [],
                "cursor": None,
                "enumeration_complete": True,
            }
            assert await checkpoint_source(
                session,
                source_id=source.id,
                lease_token=reclaimed.lease_token,
                generation=1,
                scan_state=complete,
                complete=True,
            )
            assert not source.scan_state and await claim_source(session) is None
            assert old_item.status == "MISSING"
            await session.rollback()

    client.portal.call(run)


@pytest.mark.parametrize("missing", ["app_key", "corp_id", "operator_id"])
def test_source_registration_rejects_missing_installation_identity(
    sync_client: TestClient,
    missing: str,
) -> None:
    from obsion.common.errors import AuthorizationError

    async def run() -> None:
        async with sync_client.app.state.database.sessions() as session, session.begin():
            principal, connector, binding = await install_fixture(
                session, sync_client.app.state.settings
            )
            installation = await session.get(ImInstallation, binding.installation_id)
            if missing == "app_key":
                installation.app_key = None
            elif missing == "corp_id":
                installation.corp_id = None
                connector.configuration = {**connector.configuration, "corp_id": None}
            else:
                connector.configuration = {**connector.configuration, "operator_id": " "}
            await session.flush()
            sync = KnowledgeSyncService(
                KnowledgeService(sync_client.app.state.settings, sync_client.app.state.object_store)
            )
            with pytest.raises(AuthorizationError, match="source identity"):
                await sync.create_source(
                    session,
                    principal,
                    connector_id=connector.id,
                    binding_id=binding.id,
                    correlation_id=uuid4(),
                )
            await session.rollback()

    sync_client.portal.call(run)
