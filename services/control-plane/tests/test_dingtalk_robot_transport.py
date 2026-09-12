import asyncio
import json
from uuid import uuid4

import httpx
import pytest

from obsion.application.dingtalk_outbox import _app_key, _robot_code
from obsion.capabilities.dingtalk_robot import (
    GROUP_SEND_PATH,
    MAX_RESPONSE_BYTES,
    MAX_TEXT_BYTES,
    ORIGIN,
    QUERY_PATH,
    SEND_PATH,
    TOKEN_PATH,
    DingTalkRobotTransport,
    RobotCredentials,
    RobotQueryState,
    RobotSendState,
)
from obsion.db.im_models import ImInstallation
from obsion.db.models import Connector
from obsion.domain.enums import ConnectorStatus

CREDENTIALS = RobotCredentials("example-app", "synthetic-app-secret")
TOKEN = "synthetic-robot-access-token"
USER = "example-user"
GROUP = "cid-open-group"


def _connector(configuration: dict[str, object]) -> Connector:
    return Connector(
        organization_id=uuid4(),
        name="robot",
        connector_type="dingtalk-robot",
        status=ConnectorStatus.ACTIVE,
        environment="test",
        endpoint="https://api.dingtalk.com",
        configuration=configuration,
        declared_grants=["im.reply.deliver"],
        allowed_egress=["https://api.dingtalk.com"],
    )


def _installation() -> ImInstallation:
    return ImInstallation(
        organization_id=uuid4(),
        provider="dingtalk",
        external_corp_id="corp",
        external_app_id="installation-app",
        connector_id=uuid4(),
        adapter_principal_id=uuid4(),
        verification_source="test",
    )


def test_robot_identity_environment_references(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OBSION_TEST_DINGTALK_APP", "env-app")
    monkeypatch.setenv("OBSION_TEST_DINGTALK_CODE", "env-code")
    connector = _connector(
        {
            "protocol": "dingtalk.robot.oto.v1",
            "app_key_env": "OBSION_TEST_DINGTALK_APP",
            "robot_code_env": "OBSION_TEST_DINGTALK_CODE",
        }
    )
    installation = _installation()
    assert _app_key(connector, installation) == "env-app"
    assert _robot_code(connector, installation) == "env-code"


def test_robot_identity_missing_or_invalid_environment_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OBSION_MISSING_DINGTALK", raising=False)
    connector = _connector(
        {
            "protocol": "dingtalk.robot.oto.v1",
            "app_key_env": "OBSION_MISSING_DINGTALK",
            "robot_code_env": "not-an-environment-name",
        }
    )
    installation = _installation()
    assert _app_key(connector, installation) == ""
    assert _robot_code(connector, installation) == ""


def test_robot_identity_keeps_legacy_development_fallback() -> None:
    connector = _connector(
        {
            "protocol": "dingtalk.robot.oto.v1",
            "app_key": "configured-app",
            "robot_code": "configured-code",
        }
    )
    installation = _installation()
    assert _app_key(connector, installation) == "configured-app"
    assert _robot_code(connector, installation) == "configured-code"


def test_admin_activation_validates_environment_and_exposes_binding_count(
    client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OBSION_TEST_DINGTALK_APP", "env-app")
    monkeypatch.setenv("OBSION_TEST_DINGTALK_CODE", "env-code")
    monkeypatch.setenv("OBSION_TEST_DINGTALK_SECRET", "env-secret")
    created = client.post(
        "/api/v1/admin/connectors",
        json={
            "name": "activation-robot",
            "connector_type": "dingtalk-robot",
            "environment": "test",
            "status": "DRAFT",
            "endpoint": "https://api.dingtalk.com",
            "configuration": {
                "protocol": "dingtalk.robot.oto.v1",
                "app_key_env": "OBSION_TEST_DINGTALK_APP",
                "robot_code_env": "OBSION_TEST_DINGTALK_CODE",
            },
            "credential_ref": "env://OBSION_TEST_DINGTALK_SECRET",
            "declared_grants": ["im.reply.deliver"],
            "allowed_egress": ["https://api.dingtalk.com"],
        },
    )
    assert created.status_code == 201, created.text
    activated = client.post(f"/api/v1/admin/connectors/{created.json()['id']}/activate")
    assert activated.status_code == 200, activated.text
    assert activated.json()["status"] == "ACTIVE"
    assert activated.json()["enabled_binding_count"] == 0


def test_admin_activation_rejects_missing_robot_identity(client) -> None:
    created = client.post(
        "/api/v1/admin/connectors",
        json={
            "name": "activation-robot-missing",
            "connector_type": "dingtalk-robot",
            "environment": "test",
            "status": "DRAFT",
            "endpoint": "https://api.dingtalk.com",
            "configuration": {
                "protocol": "dingtalk.robot.oto.v1",
                "app_key_env": "OBSION_NOT_SET_APP",
                "robot_code_env": "OBSION_NOT_SET_CODE",
            },
            "credential_ref": "env://OBSION_NOT_SET_SECRET",
            "declared_grants": ["im.reply.deliver"],
            "allowed_egress": ["https://api.dingtalk.com"],
        },
    )
    assert created.status_code == 201, created.text
    response = client.post(f"/api/v1/admin/connectors/{created.json()['id']}/activate")
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "credential_unavailable"


async def send(responder, **kwargs):
    return await DingTalkRobotTransport(transport=httpx.MockTransport(responder)).send_text(
        CREDENTIALS, robot_code="example-robot", user_id=USER, text=kwargs.get("text", "已完成分析")
    )


def token_response():
    return httpx.Response(200, json={"accessToken": TOKEN, "expireIn": 7200})


@pytest.mark.asyncio
async def test_native_single_recipient_request_and_processing_key():
    calls = []

    def responder(request):
        calls.append(request)
        assert str(request.url).startswith(ORIGIN)
        assert not request.url.query
        assert request.headers["accept-encoding"] == "identity"
        body = json.loads(request.content)
        if request.url.path == TOKEN_PATH:
            assert body == {"appKey": CREDENTIALS.app_key, "appSecret": CREDENTIALS.app_secret}
            assert "x-acs-dingtalk-access-token" not in request.headers
            return token_response()
        assert request.url.path == SEND_PATH and request.method == "POST"
        assert request.headers["x-acs-dingtalk-access-token"] == TOKEN
        assert body == {
            "robotCode": "example-robot",
            "userIds": [USER],
            "msgKey": "sampleText",
            "msgParam": json.dumps({"content": "已完成分析"}, ensure_ascii=False),
        }
        return httpx.Response(200, json={"processQueryKey": "vendor-processing-key"})

    result = await send(responder)
    assert result.state == RobotSendState.ACCEPTED
    assert result.process_query_key == "vendor-processing-key"
    assert len(calls) == 2
    assert CREDENTIALS.app_secret not in repr(CREDENTIALS) + repr(result)
    assert TOKEN not in repr(result)


@pytest.mark.asyncio
async def test_native_group_request_uses_open_conversation_id():
    calls = []

    def responder(request):
        calls.append(request)
        if request.url.path == TOKEN_PATH:
            return token_response()
        assert request.url.path == GROUP_SEND_PATH and request.method == "POST"
        body = json.loads(request.content)
        assert body == {
            "robotCode": "example-robot",
            "openConversationId": GROUP,
            "msgKey": "sampleText",
            "msgParam": json.dumps({"content": "群内状态"}, ensure_ascii=False),
        }
        return httpx.Response(200, json={"processQueryKey": "group-processing-key"})

    result = await DingTalkRobotTransport(transport=httpx.MockTransport(responder)).send_group_text(
        CREDENTIALS,
        robot_code="example-robot",
        open_conversation_id=GROUP,
        text="群内状态",
    )
    assert result.state == RobotSendState.ACCEPTED
    assert result.process_query_key == "group-processing-key"
    assert [request.url.path for request in calls] == [TOKEN_PATH, GROUP_SEND_PATH]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("vendor_timestamp", "expected_millis"),
    [(1730000000000, 1730000000000), (1730000000, 1730000000000), (None, None), (0, 0)],
)
async def test_reconciliation_queries_status_without_posting_a_message(
    vendor_timestamp,
    expected_millis,
):
    calls = []

    def responder(request):
        calls.append(request)
        if request.url.path == TOKEN_PATH:
            assert request.method == "POST"
            return token_response()
        assert request.url.path == QUERY_PATH and request.method == "GET"
        assert dict(request.url.params) == {
            "processQueryKey": "vendor-processing-key",
            "robotCode": "example-robot",
        }
        assert request.content == b""
        assert request.headers["x-acs-dingtalk-access-token"] == TOKEN
        return httpx.Response(
            200,
            json={
                "sendStatus": "SUCCESS",
                "messageReadInfoList": [
                    {"userId": USER, "readStatus": "READ", "readTimestamp": vendor_timestamp}
                ],
            },
        )

    result = await DingTalkRobotTransport(transport=httpx.MockTransport(responder)).query_status(
        CREDENTIALS,
        robot_code="example-robot",
        user_id=USER,
        process_query_key="vendor-processing-key",
    )
    assert result.state == RobotQueryState.SUCCESS
    assert result.send_status == "SUCCESS"
    assert result.read_status == "READ"
    assert result.read_timestamp_ms == expected_millis
    assert [request.url.path for request in calls] == [TOKEN_PATH, QUERY_PATH]


@pytest.mark.asyncio
async def test_group_reconciliation_uses_group_query_without_user_filter():
    calls = []

    def responder(request):
        calls.append(request)
        if request.url.path == TOKEN_PATH:
            return token_response()
        assert request.url.path == QUERY_PATH and request.method == "GET"
        assert dict(request.url.params) == {
            "processQueryKey": "group-processing-key",
            "robotCode": "example-robot",
            "openConversationId": GROUP,
        }
        return httpx.Response(200, json={"sendStatus": "SUCCESS", "messageReadInfoList": []})

    result = await DingTalkRobotTransport(transport=httpx.MockTransport(responder)).query_status(
        CREDENTIALS,
        robot_code="example-robot",
        open_conversation_id=GROUP,
        process_query_key="group-processing-key",
    )
    assert result.state == RobotQueryState.SUCCESS
    assert [request.url.path for request in calls] == [TOKEN_PATH, QUERY_PATH]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"sendStatus": "PENDING", "messageReadInfoList": []},
        {"sendStatus": "SUCCESS", "messageReadInfoList": [{"userId": "other"}]},
        {
            "sendStatus": "SUCCESS",
            "messageReadInfoList": [{"userId": USER, "readStatus": "READ", "readTimestamp": -1}],
        },
    ],
)
async def test_reconciliation_invalid_or_unresolved_status_is_unknown(payload):
    def responder(request):
        return (
            token_response()
            if request.url.path == TOKEN_PATH
            else httpx.Response(200, json=payload)
        )

    result = await DingTalkRobotTransport(transport=httpx.MockTransport(responder)).query_status(
        CREDENTIALS,
        robot_code="example-robot",
        user_id=USER,
        process_query_key="vendor-processing-key",
    )
    assert result.state == RobotQueryState.UNKNOWN
    assert result.reason in {"reconciliation_response_invalid", "reconciliation_status_unknown"}


@pytest.mark.asyncio
async def test_reconciliation_transport_failure_does_not_retry_or_post():
    calls = []

    def responder(request):
        calls.append(request.url.path)
        if request.url.path == TOKEN_PATH:
            return token_response()
        raise httpx.ReadTimeout(TOKEN)

    result = await DingTalkRobotTransport(transport=httpx.MockTransport(responder)).query_status(
        CREDENTIALS,
        robot_code="example-robot",
        user_id=USER,
        process_query_key="vendor-processing-key",
    )
    assert result.state == RobotQueryState.UNKNOWN
    assert result.reason == "reconciliation_unavailable"
    assert calls == [TOKEN_PATH, QUERY_PATH]


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["authenticate", "send"])
@pytest.mark.parametrize(
    "failure",
    ["timeout", "disconnect", "429", "503", "redirect", "malformed", "compressed", "oversize"],
)
async def test_failures_never_retry_or_claim_delivery(stage, failure):
    calls = []

    def responder(request):
        calls.append(request.url.path)
        if stage == "send" and request.url.path == TOKEN_PATH:
            return token_response()
        if failure == "timeout":
            raise httpx.ReadTimeout(TOKEN)
        if failure == "disconnect":
            raise httpx.RemoteProtocolError(CREDENTIALS.app_secret)
        if failure in {"429", "503"}:
            return httpx.Response(int(failure), text=TOKEN)
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "https://attacker.invalid/"})
        if failure == "malformed":
            return httpx.Response(200, content=b"not-json")
        if failure == "compressed":
            return httpx.Response(200, headers={"Content-Encoding": "identity, gzip"}, content=b"")
        return httpx.Response(200, content=b" " * (MAX_RESPONSE_BYTES + 1))

    result = await send(responder)
    assert result.state == (
        RobotSendState.UNKNOWN if stage == "send" else RobotSendState.NOT_ATTEMPTED
    )
    assert calls == ([TOKEN_PATH, SEND_PATH] if stage == "send" else [TOKEN_PATH])
    assert result.process_query_key is None
    assert TOKEN not in repr(result) and CREDENTIALS.app_secret not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["invalidStaffIdList", "flowControlledStaffIdList"])
async def test_target_rejected_is_not_success_even_with_processing_key(field):
    def responder(request):
        return (
            token_response()
            if request.url.path == TOKEN_PATH
            else httpx.Response(200, json={field: [USER], "processQueryKey": "vendor-key"})
        )

    result = await send(responder)
    assert result.state == RobotSendState.REJECTED and result.process_query_key is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"processQueryKey": ""},
        {"processQueryKey": "x" * 501},
        {"processQueryKey": TOKEN},
        {"processQueryKey": "prefix-" + CREDENTIALS.app_secret},
        {"processQueryKey": ["wrong-type"]},
        {"processQueryKey": "contains\nnewline"},
        {"processQueryKey": "key", "invalidStaffIdList": ["another-user"]},
        {"processQueryKey": "key", "flowControlledStaffIdList": None},
        {"processQueryKey": "key", "invalidStaffIdList": [USER, USER]},
        {"code": "BadRequest", "processQueryKey": "key"},
    ],
)
async def test_malformed_or_reflected_receipts_remain_unknown(payload):
    def responder(request):
        return (
            token_response()
            if request.url.path == TOKEN_PATH
            else httpx.Response(200, json=payload)
        )

    result = await send(responder)
    assert result.state == RobotSendState.UNKNOWN and result.process_query_key is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"accessToken": TOKEN, "expireIn": True},
        {"accessToken": TOKEN, "expireIn": 0},
        {"accessToken": TOKEN, "expireIn": 999999},
        {"accessToken": "contains space", "expireIn": 7200},
        {"accessToken": CREDENTIALS.app_secret, "expireIn": 7200},
    ],
)
async def test_invalid_token_never_reaches_send(payload):
    calls = []

    def responder(request):
        calls.append(request.url.path)
        return httpx.Response(200, json=payload)

    result = await send(responder)
    assert calls == [TOKEN_PATH] and result.state == RobotSendState.NOT_ATTEMPTED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    [
        "",
        "  ",
        "a\x00b",
        "a\rb",
        "\ud800",
        "中" * (MAX_TEXT_BYTES // 3 + 1),
        CREDENTIALS.app_secret,
    ],
)
async def test_invalid_content_fails_before_network(text):
    with pytest.raises(ValueError):
        await send(lambda request: pytest.fail("invalid content reached network"), text=text)


@pytest.mark.asyncio
async def test_access_token_in_content_prevents_message_post():
    calls = []

    def responder(request):
        calls.append(request.url.path)
        return token_response()

    result = await send(responder, text="echo " + TOKEN)
    assert calls == [TOKEN_PATH] and result.state == RobotSendState.NOT_ATTEMPTED


@pytest.mark.asyncio
async def test_cancellation_propagates_without_retry():
    calls = []

    def responder(request):
        calls.append(request.url.path)
        if request.url.path == TOKEN_PATH:
            return token_response()
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await send(responder)
    assert calls == [TOKEN_PATH, SEND_PATH]


@pytest.mark.asyncio
@pytest.mark.parametrize("encoded", [True, False])
async def test_response_stream_is_bounded_and_closed(encoded):
    class VendorStream(httpx.AsyncByteStream):
        chunks = 0
        closed = False

        async def __aiter__(self):
            for _ in range(100):
                self.chunks += 1
                yield b" " * 8192

        async def aclose(self):
            self.closed = True

    stream = VendorStream()

    def responder(request):
        if request.url.path == TOKEN_PATH:
            return token_response()
        return httpx.Response(
            200, stream=stream, headers={"Content-Encoding": "gzip" if encoded else "identity"}
        )

    result = await send(responder)
    assert result.state == RobotSendState.UNKNOWN
    assert stream.closed
    assert stream.chunks == (0 if encoded else MAX_RESPONSE_BYTES // 8192 + 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["", "a b", "a\nb", "中文", "a" * 256])
async def test_invalid_recipient_is_rejected_before_authentication(identity):
    transport = DingTalkRobotTransport(
        transport=httpx.MockTransport(lambda request: pytest.fail("invalid recipient reached I/O"))
    )
    with pytest.raises(ValueError):
        await transport.send_text(
            CREDENTIALS, robot_code="example-robot", user_id=identity, text="ok"
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [False, True])
@pytest.mark.parametrize("slow_phase", ["token", "message"])
async def test_send_has_total_deadline_and_preserves_unknown_boundary(
    monkeypatch, group, slow_phase
):
    monkeypatch.setattr("obsion.capabilities.dingtalk_robot.SEND_DEADLINE_SECONDS", 0.03)
    paths = []

    async def responder(request):
        paths.append(request.url.path)
        if request.url.path == TOKEN_PATH:
            if slow_phase == "token":
                await asyncio.sleep(1)
            return token_response()
        await asyncio.sleep(1)
        raise AssertionError("The total send deadline should cancel this transport")

    robot = DingTalkRobotTransport(transport=httpx.MockTransport(responder))
    if group:
        call = robot.send_group_text(
            CREDENTIALS, robot_code="example-robot", open_conversation_id=GROUP, text="状态"
        )
    else:
        call = robot.send_text(CREDENTIALS, robot_code="example-robot", user_id=USER, text="状态")
    result = await asyncio.wait_for(call, 0.5)
    if slow_phase == "token":
        assert result.state == RobotSendState.NOT_ATTEMPTED
        assert paths == [TOKEN_PATH]
    else:
        assert result.state == RobotSendState.UNKNOWN
        assert paths == [TOKEN_PATH, GROUP_SEND_PATH if group else SEND_PATH]
        assert result.process_query_key is None


@pytest.mark.asyncio
@pytest.mark.parametrize("group", [False, True])
@pytest.mark.parametrize("approval", [True, False, None, 1, "timeout"])
async def test_final_authorization_runs_after_token_and_before_post(monkeypatch, group, approval):
    calls = []
    send_path = GROUP_SEND_PATH if group else SEND_PATH

    def responder(request):
        calls.append(request.url.path)
        if request.url.path == TOKEN_PATH:
            return token_response()
        assert calls == [TOKEN_PATH, "authorize", send_path]
        return httpx.Response(200, json={"processQueryKey": "final-authorized-receipt"})

    async def authorize():
        assert calls == [TOKEN_PATH]
        calls.append("authorize")
        if approval == "timeout":
            await asyncio.sleep(1)
        return approval

    if approval == "timeout":
        monkeypatch.setattr("obsion.capabilities.dingtalk_robot.SEND_DEADLINE_SECONDS", 0.03)
    robot = DingTalkRobotTransport(transport=httpx.MockTransport(responder))
    if group:
        call = robot.send_group_text(
            CREDENTIALS,
            robot_code="example-robot",
            open_conversation_id=GROUP,
            text="状态",
            authorize_send=authorize,
        )
    else:
        call = robot.send_text(
            CREDENTIALS,
            robot_code="example-robot",
            user_id=USER,
            text="状态",
            authorize_send=authorize,
        )
    result = await asyncio.wait_for(call, 0.5)
    if approval is True:
        assert calls == [TOKEN_PATH, "authorize", send_path]
        assert result.state == RobotSendState.ACCEPTED
    else:
        assert calls == [TOKEN_PATH, "authorize"]
        assert result.state == RobotSendState.NOT_ATTEMPTED
        assert result.process_query_key is None
        if approval != "timeout":
            assert result.reason == "delivery_not_authorized"
