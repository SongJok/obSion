from __future__ import annotations

import asyncio
import json
import sys
from typing import Any
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from obsion.capabilities.connectors import ConnectorContext
from obsion.capabilities.dingtalk_docs import (
    DingTalkDocsDeniedError,
    DingTalkDocsResponseError,
    DingTalkDocsUnavailableError,
)
from obsion.capabilities.dingtalk_managed import (
    MANAGED_CONNECTOR_TYPE,
    MANAGED_READ_OPERATION,
    DingTalkManagedSdkExecutor,
    HostDwsReadRunner,
)
from obsion.capabilities.gateway import CapabilityGateway, GatewayStatus, OperatorGatewayRequest
from obsion.common.errors import ValidationError
from obsion.db.models import (
    AuditRecord,
    CapabilityBinding,
    CapabilityDefinition,
    CapabilityVersion,
    Connector,
    ImInstallation,
    ImPrincipalBinding,
    Policy,
    User,
)
from obsion.domain.enums import (
    DecisionEffect,
)
from obsion.knowledge.dingtalk_jsonml import extract_full_document
from obsion.security.auth import load_principal_by_id


def envelope(tree: Any = None) -> dict[str, Any]:
    return {
        "complete": True,
        "status": "success",
        "contractVersion": "doc.content.v1",
        "target": {"canonicalId": "doc_one1", "product": "doc"},
        "content": {
            "nodeId": "doc_one1",
            "success": True,
            "title": "合同流程",
            "revision": "17",
            "jsonml": json.dumps(
                tree
                if tree is not None
                else [
                    "root",
                    {},
                    [
                        "p",
                        {"blockquote": True},
                        ["span", {"data-type": "text"}, "报价需要财务复核。"],
                    ],
                    ["p", {}, ["span", {}, "交付前完成验收。"]],
                ],
                ensure_ascii=False,
            ),
            "logId": "not-body-log",
            "docUrl": "https://example.invalid/private?token=not-body",
        },
    }


def test_jsonml_keeps_quotes_and_excludes_transport_and_styling() -> None:
    body = extract_full_document(envelope(), "doc_one1")
    assert body.text == "> 报价需要财务复核。\n\n交付前完成验收。"
    assert body.complete and body.revision == "17"
    assert "not-body" not in body.text


@pytest.mark.parametrize(
    "changes",
    [
        {"complete": False},
        {"complete": 1},
        {"status": "partial_success"},
        {"contractVersion": "future"},
        {"target": {"canonicalId": "other", "product": "doc"}},
        {"content": {"success": True, "nodeId": "doc_one1"}},
    ],
)
def test_full_envelope_is_required(changes: dict[str, Any]) -> None:
    with pytest.raises(DingTalkDocsResponseError):
        extract_full_document({**envelope(), **changes}, "doc_one1")


@pytest.mark.parametrize(
    "tree", [[], ["p", {}, "wrong root"], ["root", {}, 3], ["root", {}, ["p", "bad attributes"]]]
)
def test_malformed_jsonml_is_not_silently_discarded(tree: Any) -> None:
    with pytest.raises(DingTalkDocsResponseError):
        extract_full_document(envelope(tree), "doc_one1")


@pytest.mark.parametrize(
    "element, gap",
    [
        (["img", {"src": "secret-url"}], "unsupported_element"),
        (["card", {"metadata": {"text": "not-body"}}], "unsupported_element"),
        (["future", {}, "not-body"], "unsupported_element"),
        (["container", {"subType": "unknown"}], "unsupported_container"),
        (["tc", {"rowSpan": 3}, "value"], "merged_table_cell"),
        (["tc", {"hidden": True}, "value"], "hidden_table_cell"),
        (["span", {"data-type": "mention"}], "non_text_span"),
        (["p", {"list": {"isOrdered": True}}, "first"], "ordered_list_labels"),
    ],
)
def test_unknown_and_non_text_elements_are_explicit_gaps(element: list[Any], gap: str) -> None:
    body = extract_full_document(envelope(["root", {}, element]), "doc_one1")
    assert not body.complete and gap in body.gaps
    assert "secret-url" not in body.text and "not-body" not in body.text


def test_plain_table_rows_keep_cell_boundaries_without_inventing_headers() -> None:
    body = extract_full_document(
        envelope(
            [
                "root",
                {},
                [
                    "table",
                    {},
                    ["tr", {}, ["tc", {}, ["p", {}, "产品"]], ["tc", {}, ["p", {}, "价格"]]],
                    ["tr", {}, ["tc", {}, "A"], ["tc", {}, "200"]],
                ],
            ]
        ),
        "doc_one1",
    )
    assert body.text == "产品 | 价格\nA | 200"
    assert body.complete


def test_jsonml_depth_is_bounded() -> None:
    tree: list[Any] = ["span", {}, "deep"]
    for _ in range(70):
        tree = ["span", {}, tree]
    with pytest.raises(DingTalkDocsResponseError):
        extract_full_document(envelope(["root", {}, tree]), "doc_one1")


def test_source_code_attribute_and_empty_leaf_placeholder_are_handled() -> None:
    tree = [
        "root",
        {},
        ["code", {"code": "print('hello')", "id": "not-body"}, ["span", {}, ""]],
        ["hr", {}, ["span", {}, ""]],
    ]
    body = extract_full_document(envelope(tree), "doc_one1")
    assert body.complete and body.text == "print('hello')"


class Runner:
    def __init__(self) -> None:
        self.calls = 0
        self.callback: Any = None

    async def read(self, *, profile: str, node_id: str) -> dict[str, Any]:
        assert profile == "corp_test:vendor_user" and node_id == "doc_one1"
        self.calls += 1
        if self.callback:
            await self.callback()
        return envelope()


class Native:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.role = "OWNER"
        self.member_type = "USER"
        self.corp = "corp_test"
        self.cursor: str | None = None
        self.second_page_invalid = False

    async def respond(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        if request.url.path == "/v1.0/oauth2/accessToken":
            return httpx.Response(200, json={"accessToken": "test-token", "expireIn": 7200})
        if request.url.path == "/v2.0/wiki/workspaces":
            return httpx.Response(
                200,
                json={
                    "workspaces": [
                        {
                            "workspaceId": "wiki_test",
                            "corpId": "corp_test",
                            "type": "TEAM",
                            "rootNodeId": "root_test",
                            "permissionRole": "OWNER",
                            "name": "企业知识",
                        }
                    ]
                },
            )
        if request.url.path == "/v2.0/wiki/nodes/doc_one1":
            return httpx.Response(
                200,
                json={
                    "node": {
                        "nodeId": "doc_one1",
                        "workspaceId": "wiki_test",
                        "type": "FILE",
                        "extension": "adoc",
                        "permissionRole": "OWNER",
                    }
                },
            )
        assert request.url.path == "/v2.0/storage/spaces/dentries/doc_one1/permissions/query"
        assert request.method == "POST"
        assert request.url.params["unionId"] == "union_test"
        option = json.loads(request.content)["option"]
        if self.second_page_invalid and option.get("nextToken"):
            return httpx.Response(200, json={})
        return httpx.Response(
            200,
            json={
                "permissions": [
                    {
                        "dentryUuid": "doc_one1",
                        "member": {
                            "type": self.member_type,
                            "id": "vendor_user",
                            "corpId": self.corp,
                        },
                        "role": {"id": self.role},
                    }
                ],
                "nextToken": self.cursor,
            },
        )


async def install_fixture(
    session: Any, settings: Any, *, app_key: str = "app_test"
) -> tuple[Any, Connector, Any]:
    user = await session.scalar(
        select(User).where(User.organization_id == settings.dev_organization_id)
    )
    principal = await load_principal_by_id(session, user.organization_id, user.id)
    installation = ImInstallation(
        organization_id=user.organization_id,
        channel="dingtalk",
        installation_id=f"reader_test_{user.organization_id}",
        corp_id="corp_test",
        app_key=app_key,
        active=True,
        created_by=user.id,
    )
    session.add(installation)
    await session.flush()
    binding = ImPrincipalBinding(
        organization_id=user.organization_id,
        installation_id=installation.id,
        channel="dingtalk",
        sender_id="vendor_user",
        user_id=user.id,
        active=True,
        created_by=user.id,
    )
    connector = Connector(
        organization_id=user.organization_id,
        name="managed_reader_test",
        connector_type=MANAGED_CONNECTOR_TYPE,
        status="ACTIVE",
        environment="development",
        endpoint=None,
        configuration={
            "corp_id": "corp_test",
            "user_id": "vendor_user",
            "operator_id": "union_test",
            "installation_id": str(installation.id),
            "app_key_env": "OBSION_READER_TEST_APP_KEY",
        },
        credential_ref="env://OBSION_READER_TEST_SECRET",
        declared_grants=["knowledge.write"],
        allowed_egress=[],
    )
    session.add_all([binding, connector])
    await session.flush()
    return principal, connector, binding


@pytest.mark.parametrize(
    "denial",
    [
        None,
        "preview",
        "other_corp",
        "organization_grant",
        "revoked_binding",
        "missing_permissions",
        "repeated_cursor",
        "revoke_during_read",
        "change_connection",
        "production",
        "command_config",
    ],
)
def test_managed_reader_has_current_identity_scope_and_full_acl(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    denial: str | None,
) -> None:
    monkeypatch.setenv("OBSION_READER_TEST_APP_KEY", "app_test")

    async def run() -> None:
        async with client.app.state.database.sessions() as session, session.begin():
            principal, connector, binding = await install_fixture(
                session, client.app.state.settings
            )
            runner, native = Runner(), Native()
            if denial == "preview":
                native.role = "READER"
            if denial == "other_corp":
                native.corp = "other_corp"
            if denial == "organization_grant":
                native.member_type = "ORG"
            if denial == "revoked_binding":
                binding.active = False
            if denial in {"repeated_cursor", "missing_permissions"}:
                native.cursor = "opaque cursor"
                native.second_page_invalid = denial == "missing_permissions"
            if denial == "production":
                connector.environment = "production"
            if denial == "command_config":
                connector.configuration = {**connector.configuration, "command": "arbitrary"}
            if denial in {"revoke_during_read", "change_connection"}:

                async def revoke() -> None:
                    if denial == "revoke_during_read":
                        native.role = "READER"
                    else:
                        connector.configuration = {
                            **connector.configuration,
                            "operator_id": "changed",
                        }
                        await session.flush()

                runner.callback = revoke
            await session.flush()
            executor = DingTalkManagedSdkExecutor(
                runner, transport=httpx.MockTransport(native.respond)
            )
            request = {
                "operation": MANAGED_READ_OPERATION,
                "node_id": "doc_one1",
                "workspace_id": "wiki_test",
            }
            context = ConnectorContext(
                principal=principal, run_id=None, step_id=None, session=session
            )
            if denial:
                with pytest.raises(
                    (DingTalkDocsDeniedError, DingTalkDocsResponseError, ValidationError)
                ):
                    await executor.invoke(connector, request, "test-secret", context)
                assert runner.calls == (
                    1 if denial in {"revoke_during_read", "change_connection"} else 0
                )
            else:
                result = await executor.invoke(connector, request, "test-secret", context)
                assert result.data["complete"] is True and "财务复核" in result.data["text"]
                assert result.data["reader_user_id"] == str(principal.id)
                assert result.data["binding_id"] == str(binding.id)
                assert "test-secret" not in json.dumps(result.data)
                assert runner.calls == 1 and native.calls.count("/v2.0/wiki/workspaces") == 2

    client.portal.call(run)


@pytest.mark.parametrize(
    "policy_effect,wrong_resource",
    [
        (DecisionEffect.ALLOW, False),
        (DecisionEffect.DENY, False),
        (DecisionEffect.ALLOW, True),
    ],
)
def test_managed_read_traverses_real_gateway_policy_and_audit(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    policy_effect: DecisionEffect,
    wrong_resource: bool,
) -> None:
    monkeypatch.setenv("OBSION_READER_TEST_APP_KEY", "app_test")
    monkeypatch.setenv("OBSION_READER_TEST_SECRET", "test-secret")

    async def run() -> None:
        async with client.app.state.database.sessions() as session:
            async with session.begin():
                principal, connector, _ = await install_fixture(session, client.app.state.settings)
                version = await session.scalar(
                    select(CapabilityVersion)
                    .join(CapabilityDefinition)
                    .where(
                        CapabilityDefinition.organization_id == principal.organization_id,
                        CapabilityDefinition.name == MANAGED_READ_OPERATION,
                    )
                    .order_by(CapabilityVersion.version.desc())
                )
                assert version is not None
                session.add(
                    CapabilityBinding(
                        organization_id=principal.organization_id,
                        capability_version_id=version.id,
                        connector_id=connector.id,
                        environment="development",
                        enabled=True,
                        resource_selector={"source": "dingtalk-managed"},
                    )
                )
                session.add(
                    Policy(
                        organization_id=principal.organization_id,
                        name="managed read test",
                        version=1,
                        priority=1,
                        effect=policy_effect,
                        enabled=True,
                        conditions={"action": "knowledge.write"},
                        obligations=[],
                        reason="Isolated test policy",
                        created_by=principal.id,
                    )
                )
            runner, native = Runner(), Native()
            gateway = CapabilityGateway(
                {
                    "SDK": DingTalkManagedSdkExecutor(
                        runner, transport=httpx.MockTransport(native.respond)
                    )
                }
            )
            result = await gateway.invoke_operator(
                session,
                OperatorGatewayRequest(
                    principal=principal,
                    capability_name=MANAGED_READ_OPERATION,
                    payload={
                        "operation": MANAGED_READ_OPERATION,
                        "node_id": "doc_one1",
                        "workspace_id": "wiki_test",
                    },
                    resource={
                        "source": "dingtalk-managed",
                        "corp_id": "corp_test",
                        "node_id": "doc_one1",
                        "workspace_id": "other_wiki" if wrong_resource else "wiki_test",
                    },
                    environment="development",
                    correlation_id=uuid4(),
                ),
            )
            assert result.status == (
                GatewayStatus.COMPLETED
                if policy_effect == DecisionEffect.ALLOW and not wrong_resource
                else GatewayStatus.DENIED
            )
            assert runner.calls == (
                1 if policy_effect == DecisionEffect.ALLOW and not wrong_resource else 0
            )
            audit = (
                await session.scalars(
                    select(AuditRecord).where(AuditRecord.action == "knowledge.write")
                )
            ).all()
            assert audit and audit[-1].policy_decision_id == result.policy_decision_id

    client.portal.call(run)


async def test_host_reader_ignores_global_current_organization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "obsion.capabilities.dingtalk_managed.shutil.which", lambda _: "/usr/bin/true"
    )
    runner = HostDwsReadRunner()
    calls: list[tuple[str, ...]] = []

    async def call(arguments: tuple[str, ...]) -> dict[str, Any]:
        calls.append(arguments)
        if arguments == ("profile", "list"):
            return {
                "success": True,
                "currentProfile": "different:user",
                "profiles": [
                    {
                        "profile": "corp_test:vendor_user",
                        "corpId": "corp_test",
                        "userId": "vendor_user",
                        "status": "active",
                    }
                ],
            }
        return envelope()

    monkeypatch.setattr(runner, "_call", call)
    await runner.read(profile="corp_test:vendor_user", node_id="doc_one1")
    assert calls[-1][:2] == ("--profile", "corp_test:vendor_user")


async def test_host_process_output_is_bounded_and_stderr_is_not_exposed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "obsion.capabilities.dingtalk_managed.shutil.which", lambda _: sys.executable
    )
    runner = HostDwsReadRunner()
    with pytest.raises(DingTalkDocsUnavailableError) as failure:
        await runner._call(("-c", "import sys; sys.stderr.write('private-token'); sys.exit(1)"))
    assert "private-token" not in str(failure.value)
    monkeypatch.setattr("obsion.capabilities.dingtalk_managed._MAX_OUTPUT_BYTES", 32)
    with pytest.raises(DingTalkDocsResponseError):
        await runner._call(("-c", "print('x'*1000)"))


async def test_cancelling_host_read_reaps_its_owned_child(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "obsion.capabilities.dingtalk_managed.shutil.which", lambda _: sys.executable
    )
    runner = HostDwsReadRunner()
    spawn = asyncio.create_subprocess_exec
    processes = []
    started = asyncio.Event()

    async def capture(*args: Any, **kwargs: Any) -> Any:
        process = await spawn(*args, **kwargs)
        processes.append(process)
        started.set()
        return process

    monkeypatch.setattr(
        "obsion.capabilities.dingtalk_managed.asyncio.create_subprocess_exec", capture
    )
    task = asyncio.create_task(runner._call(("-c", "import time; time.sleep(30)")))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(processes) == 1 and processes[0].returncode is not None


@pytest.mark.parametrize("after", ["active", "expired", "wrong_corp", "duplicate"])
async def test_host_refreshes_only_expired_pinned_identity_and_rechecks(
    monkeypatch: pytest.MonkeyPatch,
    after: str,
) -> None:
    monkeypatch.setattr(
        "obsion.capabilities.dingtalk_managed.shutil.which", lambda _: sys.executable
    )
    runner = HostDwsReadRunner()
    calls: list[tuple[str, ...]] = []
    refreshed = False

    async def call(arguments: tuple[str, ...]) -> dict[str, Any]:
        nonlocal refreshed
        calls.append(arguments)
        if arguments == ("--profile", "corp_test:vendor_user", "auth", "status"):
            refreshed = True
            return {"success": True}
        if arguments == ("profile", "list"):
            row = {
                "profile": "corp_test:vendor_user",
                "corpId": "corp_test",
                "userId": "vendor_user",
                "status": "active" if refreshed and after != "expired" else "expired",
            }
            if refreshed and after == "wrong_corp":
                row["corpId"] = "other_corp"
            return {
                "success": True,
                "profiles": [row, row] if refreshed and after == "duplicate" else [row],
            }
        assert arguments[:4] == ("--profile", "corp_test:vendor_user", "doc", "+fetch")
        return envelope()

    monkeypatch.setattr(runner, "_call", call)
    if after == "active":
        assert await runner.read(profile="corp_test:vendor_user", node_id="doc_one1") == envelope()
        assert len(calls) == 4
    else:
        with pytest.raises(DingTalkDocsDeniedError):
            await runner.read(profile="corp_test:vendor_user", node_id="doc_one1")
        assert len(calls) == 3
    assert calls[1] == ("--profile", "corp_test:vendor_user", "auth", "status")
    assert all("switch" not in args and "login" not in args for args in calls)
