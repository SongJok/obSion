import base64
import copy
import hashlib
import json
from uuid import uuid4

import httpx
import pytest
from jsonschema import Draft202012Validator

from obsion.capabilities.codeup import (
    catalog_configuration,
    read_codeup,
    repository_configuration,
)
from obsion.capabilities.codeup_contract import (
    CODEUP_DISCOVERY_OPERATION,
    CODEUP_ORIGIN,
    MAX_RESPONSE_BYTES,
    output_schema,
)
from obsion.capabilities.connectors import ConnectorContext, HttpJsonExecutor
from obsion.common.errors import ObsionError
from obsion.config import Environment, Settings
from obsion.db.models import Connector
from obsion.domain.enums import ConnectorStatus
from obsion.security.identity import Principal

SHA = "a" * 40
NAME = "example/project"
CREDENTIAL = "synthetic-codeup-credential"


def connector() -> Connector:
    return Connector(
        id=uuid4(),
        organization_id=uuid4(),
        name="codeup-test",
        connector_type="codeup",
        environment="development",
        status=ConnectorStatus.ACTIVE,
        endpoint=CODEUP_ORIGIN,
        configuration={
            "protocol": "codeup.read.v1",
            "organization_id": "org-example",
            "repositories": {NAME: {"id": "123", "repository_id": str(uuid4())}},
            "allowed_repositories": [NAME],
        },
        declared_grants=["code.read"],
        allowed_egress=["openapi-rdc.aliyuncs.com:443"],
    )


def catalog_connector() -> Connector:
    return Connector(
        id=uuid4(),
        organization_id=uuid4(),
        name="codeup-catalog-test",
        connector_type="codeup-catalog",
        environment="development",
        status=ConnectorStatus.ACTIVE,
        endpoint=CODEUP_ORIGIN,
        configuration={
            "protocol": "codeup.catalog.v1",
            "organization_id": "org-example",
            "rate_limit_per_minute": 30,
        },
        declared_grants=["connectors.read"],
        allowed_egress=["openapi-rdc.aliyuncs.com:443"],
    )


def commit(sha: str = SHA) -> dict:
    return {
        "id": sha,
        "committedDate": "2026-09-05T10:00:00Z",
        "parentIds": [],
        "title": "修复问题",
        "message": "Fix a bounded bug",
        "authorEmail": "private@example.test",
    }


def file_data(text: str = "print('hello')\n") -> dict:
    raw = text.encode()
    blob = hashlib.sha1(
        b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False
    ).hexdigest()
    return {
        "filePath": "src/main.py",
        "commitId": SHA,
        "blobId": blob,
        "content": base64.b64encode(raw).decode(),
        "encoding": "base64",
        "size": len(raw),
    }


async def invoke(payload: dict, response: httpx.Response, target: Connector | None = None) -> dict:
    async def responder(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.host == "openapi-rdc.aliyuncs.com"
        assert request.headers["x-yunxiao-token"] == CREDENTIAL
        assert request.headers["accept-encoding"] == "identity"
        assert "authorization" not in request.headers
        assert CREDENTIAL not in str(request.url)
        return response

    return await read_codeup(
        target or connector(),
        payload,
        CREDENTIAL,
        request_timeout=5,
        transport=httpx.MockTransport(responder),
    )


def repository_item(identifier: int = 123, path: str = "example/project") -> dict:
    return {
        "id": identifier,
        "name": path.rsplit("/", 1)[-1],
        "pathWithNamespace": path,
        "visibility": "private",
        "archived": False,
        "accessLevel": 20,
        "creatorUid": "private-user-id",
        "webUrl": "https://codeup.example/private",
    }


@pytest.mark.asyncio
async def test_catalog_discovery_uses_fixed_readonly_route_and_drops_private_fields() -> None:
    target = catalog_connector()

    async def responder(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/oapi/v1/codeup/organizations/org-example/repositories"
        assert dict(request.url.params) == {
            "page": "2",
            "perPage": "2",
            "orderBy": "path",
            "sort": "asc",
            "archived": "false",
            "search": "project",
        }
        assert request.headers["x-yunxiao-token"] == CREDENTIAL
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(200, json=[repository_item()])

    result = await read_codeup(
        target,
        {
            "operation": CODEUP_DISCOVERY_OPERATION,
            "search": "project",
            "page": 2,
            "limit": 2,
        },
        CREDENTIAL,
        request_timeout=5,
        transport=httpx.MockTransport(responder),
    )
    Draft202012Validator(output_schema(CODEUP_DISCOVERY_OPERATION)).validate(result)
    assert result == {
        "operation": CODEUP_DISCOVERY_OPERATION,
        "connector_id": str(target.id),
        "items": [
            {
                "id": "123",
                "name": "project",
                "path": "example/project",
                "visibility": "private",
                "archived": False,
            }
        ],
        "count": 1,
        "next_page": None,
        "complete": True,
    }
    assert "creatorUid" not in json.dumps(result)
    assert "webUrl" not in json.dumps(result)


@pytest.mark.asyncio
async def test_catalog_full_page_requires_explicit_next_request() -> None:
    result = await invoke(
        {"operation": CODEUP_DISCOVERY_OPERATION, "page": 1, "limit": 2},
        httpx.Response(
            200,
            json=[repository_item(123, "example/one"), repository_item(124, "example/two")],
        ),
        catalog_connector(),
    )
    assert result["complete"] is False and result["next_page"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"operation": CODEUP_DISCOVERY_OPERATION, "limit": 21},
        {"operation": CODEUP_DISCOVERY_OPERATION, "search": " project"},
        {"operation": CODEUP_DISCOVERY_OPERATION, "repository": NAME},
        {"operation": "codeup.repository.get"},
    ],
)
async def test_catalog_contract_is_closed(payload: dict) -> None:
    with pytest.raises(ObsionError):
        await read_codeup(catalog_connector(), payload, CREDENTIAL, request_timeout=5)


@pytest.mark.parametrize(
    "field,value",
    [
        ("connector_type", "codeup"),
        ("endpoint", "https://openapi-rdc.aliyuncs.com.example"),
        ("declared_grants", ["code.read"]),
        ("allowed_egress", ["*"]),
        ("configuration", {"protocol": "codeup.catalog.v1", "organization_id": "../bad"}),
    ],
)
def test_catalog_configuration_is_strict(field: str, value: object) -> None:
    target = catalog_connector()
    setattr(target, field, value)
    with pytest.raises(ObsionError):
        catalog_configuration(target)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation,data,extra",
    [
        (
            "codeup.repository.get",
            {"id": 123, "name": "project", "defaultBranch": "main", "visibility": "private"},
            {},
        ),
        ("codeup.commit.get", commit(), {"commit_id": SHA}),
        ("codeup.commits.list", [commit()], {"ref": "main", "limit": 2}),
        ("codeup.file.read", file_data(), {"path": "src/main.py", "commit_id": SHA}),
    ],
)
async def test_official_read_routes_normalize_results(
    operation: str, data: object, extra: dict
) -> None:
    result = await invoke(
        {"operation": operation, "repository": NAME, **extra}, httpx.Response(200, json=data)
    )
    Draft202012Validator(output_schema(operation)).validate(result)
    assert result["operation"] == operation
    assert result["count"] == 1 and result["complete"]
    assert "private@example.test" not in json.dumps(result)


@pytest.mark.asyncio
async def test_fixed_file_commit_path_and_hash_are_not_mutable_ref() -> None:
    async def responder(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path.startswith(
            b"/oapi/v1/codeup/organizations/org-example/repositories/123/files/src%2Fmain.py?"
        )
        assert request.url.params["ref"] == SHA
        return httpx.Response(200, json=file_data())

    payload = {
        "operation": "codeup.file.read",
        "repository": NAME,
        "path": "src/main.py",
        "commit_id": SHA,
    }
    result = await read_codeup(
        connector(),
        payload,
        CREDENTIAL,
        request_timeout=5,
        transport=httpx.MockTransport(responder),
    )
    assert result["items"][0]["content"] == "print('hello')\n"
    assert not result["items"][0]["redacted"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("commitId", "b" * 40),
        ("blobId", "b" * 40),
        ("size", 1),
        ("size", True),
        ("filePath", "other.py"),
        ("encoding", "hex"),
        ("content", "%%%"),
    ],
)
async def test_file_identity_and_content_mismatch_is_rejected(field: str, value: object) -> None:
    data = {**file_data(), field: value}
    with pytest.raises(ObsionError, match="校验") as caught:
        await invoke(
            {
                "operation": "codeup.file.read",
                "repository": NAME,
                "path": "src/main.py",
                "commit_id": SHA,
            },
            httpx.Response(200, json=data),
        )
    assert caught.value.code == "codeup_response_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "/etc/passwd",
        ".env",
        ".env.local",
        ".git/config",
        "a/../b",
        "a//b",
        "a%2fb",
        "a?b",
        "id.key",
        ".ssh/id_rsa",
        "a\\b",
        "a\x00b",
    ],
)
async def test_unsafe_paths_do_not_send_requests(path: str) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        pytest.fail("unsafe request reached network")

    with pytest.raises(ObsionError) as caught:
        await read_codeup(
            connector(),
            {"operation": "codeup.file.read", "repository": NAME, "path": path, "commit_id": SHA},
            CREDENTIAL,
            request_timeout=5,
            transport=httpx.MockTransport(responder),
        )
    assert caught.value.code == "codeup_operation_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch",
    [
        {"operation": "codeup.file.delete"},
        {"commit_id": "main"},
        {"commit_id": SHA + "\n"},
        {"url": "https://attacker.test"},
        {"organization_id": "other"},
    ],
)
async def test_operation_contract_cannot_expand_permissions(patch: dict) -> None:
    with pytest.raises(ObsionError):
        await read_codeup(
            connector(),
            {
                "operation": "codeup.file.read",
                "repository": NAME,
                "path": "src/main.py",
                "commit_id": SHA,
                **patch,
            },
            CREDENTIAL,
            request_timeout=5,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("endpoint", "http://openapi-rdc.aliyuncs.com"),
        ("endpoint", "https://openapi-rdc.aliyuncs.com.attacker.test"),
        ("endpoint", CODEUP_ORIGIN + "/other"),
        ("allowed_egress", ["*"]),
        ("allowed_egress", [CODEUP_ORIGIN, "attacker.test"]),
        ("declared_grants", ["*"]),
    ],
)
def test_strict_provider_configuration(field: str, value: object) -> None:
    target = connector()
    setattr(target, field, value)
    with pytest.raises(ObsionError) as caught:
        repository_configuration(target, NAME)
    assert caught.value.code == "codeup_configuration_invalid"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,code",
    [
        (401, "codeup_upstream_denied"),
        (403, "codeup_upstream_denied"),
        (404, "codeup_upstream_denied"),
        (429, "codeup_rate_limited"),
        (500, "codeup_upstream_unavailable"),
        (302, "codeup_upstream_unavailable"),
    ],
)
async def test_upstream_errors_do_not_echo_bodies_or_follow_redirects(
    status: int, code: str
) -> None:
    with pytest.raises(ObsionError) as caught:
        await invoke(
            {"operation": "codeup.repository.get", "repository": NAME},
            httpx.Response(status, text=CREDENTIAL, headers={"location": "https://attacker.test"}),
        )
    assert caught.value.code == code
    assert CREDENTIAL not in str(caught.value)


@pytest.mark.asyncio
async def test_response_byte_budget() -> None:
    with pytest.raises(ObsionError) as caught:
        await invoke(
            {"operation": "codeup.repository.get", "repository": NAME},
            httpx.Response(200, content=b"x" * (MAX_RESPONSE_BYTES + 1)),
        )
    assert caught.value.code == "codeup_response_too_large"


@pytest.mark.asyncio
async def test_full_commit_page_does_not_claim_exhaustion() -> None:
    result = await invoke(
        {
            "operation": "codeup.commits.list",
            "repository": NAME,
            "ref": "main",
            "page": 2,
            "limit": 1,
        },
        httpx.Response(200, json=[commit()]),
    )
    assert not result["complete"] and result["next_page"] == 3


@pytest.mark.asyncio
async def test_provider_cannot_echo_credential_into_code_evidence() -> None:
    text = CREDENTIAL + "\npassword=private-value\n" + "pt-" + "x" * 40
    result = await invoke(
        {
            "operation": "codeup.file.read",
            "repository": NAME,
            "path": "src/main.py",
            "commit_id": SHA,
        },
        httpx.Response(200, json=file_data(text)),
    )
    item = result["items"][0]
    assert CREDENTIAL not in json.dumps(result)
    assert "private-value" not in item["content"]
    assert "x" * 40 not in item["content"]
    assert item["redacted"]
    assert item["content_sha256"] == hashlib.sha256(item["content"].encode()).hexdigest()


@pytest.mark.asyncio
async def test_http_dispatch_uses_native_codeup_not_generic_post() -> None:
    target = connector()

    async def responder(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(200, json=commit())

    executor = HttpJsonExecutor(
        Settings(environment=Environment.TEST), transport=httpx.MockTransport(responder)
    )
    context = ConnectorContext(
        Principal(uuid4(), target.organization_id, "test", "Test"), uuid4(), None
    )
    result = await executor.invoke(
        target,
        {"operation": "codeup.commit.get", "repository": NAME, "commit_id": SHA},
        CREDENTIAL,
        context,
    )
    assert result.source == "codeup" and result.resource.startswith("codeup://")


@pytest.mark.parametrize(
    "extra",
    [
        {"headers": {"authorization": CREDENTIAL}},
        {"operation_paths": {}},
        {"organization_id": "../other"},
        {"rate_limit_per_minute": True},
        {"rate_limit_per_minute": 0},
        {"rate_limit_per_minute": 601},
        {"rate_limit_per_minute": "60"},
    ],
)
def test_configuration_is_closed(extra: dict) -> None:
    target = connector()
    target.configuration = {**copy.deepcopy(target.configuration), **extra}
    with pytest.raises(ObsionError):
        repository_configuration(target, NAME)


@pytest.mark.asyncio
async def test_final_allowed_page_is_incomplete_without_an_unusable_next_page() -> None:
    result = await invoke(
        {
            "operation": "codeup.commits.list",
            "repository": NAME,
            "ref": "main",
            "page": 100,
            "limit": 1,
        },
        httpx.Response(200, json=[commit()]),
    )
    assert result["complete"] is False and result["next_page"] is None
    Draft202012Validator(output_schema("codeup.commits.list")).validate(result)


@pytest.mark.asyncio
async def test_compressed_body_is_rejected_before_consumption() -> None:
    class UnreadableBody(httpx.AsyncByteStream):
        async def __aiter__(self):
            pytest.fail("compressed response body was consumed")
            yield b""  # pragma: no cover

    with pytest.raises(ObsionError) as caught:
        await invoke(
            {"operation": "codeup.repository.get", "repository": NAME},
            httpx.Response(200, headers={"Content-Encoding": "gzip"}, stream=UnreadableBody()),
        )
    assert caught.value.code == "codeup_response_invalid"
