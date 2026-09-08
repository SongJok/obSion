import json
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from test_codeup_connector import CREDENTIAL, NAME, SHA, commit, file_data

from obsion.capabilities.codeup_contract import (
    CODEUP_DISCOVERY_OPERATION,
    CODEUP_OPERATIONS,
    CODEUP_ORIGIN,
)
from obsion.capabilities.connectors import HttpJsonExecutor
from obsion.capabilities.gateway import GatewayRequest
from obsion.common.time import utc_now
from obsion.db.models import (
    CodeRepositoryGrant,
    Connector,
    Evidence,
    Role,
    Run,
    Thread,
    Turn,
    Workspace,
)
from obsion.domain.enums import RunStatus, ThreadStatus, Visibility
from obsion.sandbox.source_configuration import configuration_snapshot
from obsion.security.auth import get_principal, load_principal_by_id
from obsion.security.identity import Principal


def configure(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("OBSION_CODEUP_TEST_CREDENTIAL", CREDENTIAL)
    created = client.post("/api/v1/code/repositories", json={"name": NAME})
    assert created.status_code == 201, created.text
    repository_id = created.json()["id"]
    conn = client.post(
        "/api/v1/admin/connectors",
        json={
            "name": "codeup-test",
            "connector_type": "codeup",
            "status": "ACTIVE",
            "environment": "development",
            "endpoint": CODEUP_ORIGIN,
            "credential_ref": "env://OBSION_CODEUP_TEST_CREDENTIAL",
            "configuration": {
                "protocol": "codeup.read.v1",
                "organization_id": "org-example",
                "repositories": {NAME: {"id": "123", "repository_id": repository_id}},
                "allowed_repositories": [NAME],
            },
            "declared_grants": ["code.read"],
            "allowed_egress": [CODEUP_ORIGIN],
        },
    )
    assert conn.status_code == 201, conn.text
    definitions = client.get("/api/v1/capabilities").json()
    for definition in definitions:
        if definition["name"] in CODEUP_OPERATIONS:
            bound = client.post(
                f"/api/v1/admin/capabilities/{definition['id']}/bindings",
                json={
                    "connector_id": conn.json()["id"],
                    "environment": "development",
                    "resource_selector": {"repository": NAME, "source": "codeup"},
                },
            )
            assert bound.status_code == 201, bound.text
    return repository_id


def install_transport(client: TestClient, responder) -> None:
    client.app.state.capability_gateway.executors["HTTP"] = HttpJsonExecutor(
        client.app.state.settings, transport=httpx.MockTransport(responder)
    )


def configure_catalog(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("OBSION_CODEUP_TEST_CREDENTIAL", CREDENTIAL)
    conn = client.post(
        "/api/v1/admin/connectors",
        json={
            "name": "codeup-catalog-test",
            "connector_type": "codeup-catalog",
            "status": "ACTIVE",
            "environment": "development",
            "endpoint": CODEUP_ORIGIN,
            "credential_ref": "env://OBSION_CODEUP_TEST_CREDENTIAL",
            "configuration": {
                "protocol": "codeup.catalog.v1",
                "organization_id": "org-example",
                "rate_limit_per_minute": 30,
            },
            "declared_grants": ["connectors.read"],
            "allowed_egress": [CODEUP_ORIGIN],
        },
    )
    assert conn.status_code == 201, conn.text
    connector_id = conn.json()["id"]
    definition = next(
        item
        for item in client.get("/api/v1/capabilities").json()
        if item["name"] == CODEUP_DISCOVERY_OPERATION
    )
    binding = client.post(
        f"/api/v1/admin/capabilities/{definition['id']}/bindings",
        json={
            "connector_id": connector_id,
            "environment": "development",
            "resource_selector": {
                "connector_id": connector_id,
                "source": "codeup-catalog",
            },
        },
    )
    assert binding.status_code == 201, binding.text
    return connector_id


def configure_mapping_source(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> tuple[str, str]:
    monkeypatch.setenv("OBSION_CODEUP_MAPPING_CREDENTIAL", CREDENTIAL)
    created = client.post("/api/v1/code/repositories", json={"name": NAME})
    assert created.status_code == 201, created.text
    repository_id = created.json()["id"]
    secret = client.post(
        "/api/v1/admin/secrets",
        json={
            "name": "codeup-mapping-reader",
            "external_ref": "env://OBSION_CODEUP_MAPPING_CREDENTIAL",
        },
    )
    assert secret.status_code == 201, secret.text
    connector = client.post(
        "/api/v1/admin/connectors",
        json={
            "name": "codeup-mapping-source",
            "connector_type": "codeup",
            "status": "ACTIVE",
            "environment": "development",
            "endpoint": CODEUP_ORIGIN,
            "credential_ref": "secret://codeup-mapping-reader",
            "configuration": {
                "protocol": "codeup.read.v1",
                "organization_id": "org-example",
                "repositories": {NAME: {"id": "123", "repository_id": repository_id}},
                "allowed_repositories": [NAME],
            },
            "declared_grants": ["code.read"],
            "allowed_egress": ["openapi-rdc.aliyuncs.com:443"],
        },
    )
    assert connector.status_code == 201, connector.text
    return connector.json()["id"], repository_id


def allow_codeup_mapping(client: TestClient) -> None:
    policy = client.post(
        "/api/v1/admin/policies",
        json={
            "name": f"codeup-mapping-{uuid4()}",
            "priority": 9999,
            "effect": "ALLOW",
            "conditions": {
                "actions": ["project_source.codeup.map"],
                "context": {"environment": "development"},
            },
            "reason": "Test-only verified Codeup repository mapping",
        },
    )
    assert policy.status_code == 201, policy.text


def map_codeup_repository(
    client: TestClient,
    connector_id: str,
    catalog_connector_id: str,
    repository_id: str,
    provider_repository_id: str,
    provider_path: str,
) -> httpx.Response:
    return client.post(
        f"/api/v1/admin/codeup/connectors/{connector_id}/mappings",
        json={
            "catalog_connector_id": catalog_connector_id,
            "repository_id": repository_id,
            "provider_repository_id": provider_repository_id,
            "provider_path": provider_path,
        },
        headers={"X-Request-ID": str(uuid4())},
    )


def discover(client: TestClient, connector_id: str, body: dict, request_id=None) -> httpx.Response:
    return client.post(
        f"/api/v1/admin/codeup/connectors/{connector_id}/repositories",
        json=body,
        headers={"X-Request-ID": str(request_id or uuid4())},
    )


def read(client: TestClient, repository_id: str, body: dict, request_id=None) -> httpx.Response:
    return client.post(
        f"/api/v1/code/repositories/{repository_id}/remote-read",
        json=body,
        headers={"X-Request-ID": str(request_id or uuid4())},
    )


def test_catalog_discovery_uses_policy_gateway_audit_and_drops_vendor_pii(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    connector_id = configure_catalog(client, monkeypatch)
    calls: list[httpx.Request] = []

    def responder(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "id": 123,
                    "name": "project",
                    "pathWithNamespace": "example/project",
                    "visibility": "private",
                    "archived": False,
                    "accessLevel": 20,
                    "creatorUid": "private-user",
                    "webUrl": "https://private.example/repo",
                }
            ],
        )

    install_transport(client, responder)
    request_id = uuid4()
    response = discover(
        client,
        connector_id,
        {"operation": CODEUP_DISCOVERY_OPERATION, "search": "project", "limit": 20},
        request_id,
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"] == [
        {
            "id": "123",
            "name": "project",
            "path": "example/project",
            "visibility": "private",
            "archived": False,
        }
    ]
    assert response.json()["connector_id"] == connector_id
    assert "creatorUid" not in response.text and "webUrl" not in response.text
    records = client.get("/api/v1/admin/audit?limit=100").json()
    record = next(item for item in records if item["correlation_id"] == str(request_id))
    assert record["action"] == "connectors.read" and record["outcome"] == "SUCCESS"
    assert record["metadata"]["source"] == "codeup"
    assert len(calls) == 1


def test_catalog_rejects_unprivileged_and_unbound_connector_before_remote_io(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    connector_id = configure_catalog(client, monkeypatch)
    install_transport(client, lambda request: pytest.fail("denied catalog reached network"))
    principal = Principal(
        client.app.state.settings.dev_user_id,
        client.app.state.settings.dev_organization_id,
        "viewer",
        "Viewer",
        permissions=frozenset(),
    )
    client.app.dependency_overrides[get_principal] = lambda: principal
    try:
        response = discover(client, connector_id, {"operation": CODEUP_DISCOVERY_OPERATION})
        assert response.status_code == 403, response.text
    finally:
        client.app.dependency_overrides.pop(get_principal)
    response = discover(client, str(uuid4()), {"operation": CODEUP_DISCOVERY_OPERATION})
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "codeup_configuration_invalid"


def test_codeup_rest_real_policy_and_audit_with_mock_vendor(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_id = configure(client, monkeypatch)
    calls = []

    def responder(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(
            200,
            json={"id": 123, "name": "project", "defaultBranch": "main", "visibility": "private"},
        )

    install_transport(client, responder)
    request_id = uuid4()
    response = read(client, repository_id, {"operation": "codeup.repository.get"}, request_id)
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["id"] == "123"
    assert response.json()["policy_decision_id"]
    records = client.get("/api/v1/admin/audit?limit=100").json()
    audited = [item for item in records if item["correlation_id"] == str(request_id)]
    assert len(audited) == 1 and audited[0]["outcome"] == "SUCCESS"
    assert audited[0]["action"] == "code.read"
    assert audited[0]["metadata"]["invocation_mode"] == "operator"
    assert "evidence_id" not in audited[0]["metadata"]
    assert len(calls) == 1 and calls[0].method == "GET"
    assert CREDENTIAL not in json.dumps(records)


@pytest.mark.parametrize(
    "body,data",
    [
        ({"operation": "codeup.commit.get", "commit_id": SHA}, commit()),
        ({"operation": "codeup.commits.list", "ref": "main", "limit": 1}, [commit()]),
        ({"operation": "codeup.file.read", "commit_id": SHA, "path": "src/main.py"}, file_data()),
    ],
)
def test_each_read_is_available_through_same_control_plane(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    body: dict,
    data: object,
) -> None:
    repository_id = configure(client, monkeypatch)
    install_transport(client, lambda request: httpx.Response(200, json=data))
    response = read(client, repository_id, body)
    assert response.status_code == 200, response.text
    assert response.json()["operation"] == body["operation"]


def test_missing_connection_has_actionable_message(client: TestClient) -> None:
    repository_id = client.post("/api/v1/code/repositories", json={"name": NAME}).json()["id"]
    response = read(client, repository_id, {"operation": "codeup.repository.get"})
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "codeup_configuration_invalid"
    assert "绑定" in response.json()["message"]


@pytest.mark.parametrize(
    "extra",
    [
        {"repository": "other/repo"},
        {"organization_id": "other"},
        {"credential": CREDENTIAL},
        {"url": "https://attacker.test"},
        {"path": "not-used"},
    ],
)
def test_rest_does_not_accept_identity_or_unused_fields(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    extra: dict,
) -> None:
    repository_id = configure(client, monkeypatch)
    response = read(client, repository_id, {"operation": "codeup.repository.get", **extra})
    assert response.status_code == 422, response.text
    assert CREDENTIAL not in response.text


def test_repo_deny_grant_precedes_credentials_and_remote_io(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_id = configure(client, monkeypatch)
    settings = client.app.state.settings

    async def deny() -> None:
        async with client.app.state.database.sessions() as session, session.begin():
            session.add(
                CodeRepositoryGrant(
                    organization_id=settings.dev_organization_id,
                    repository_id=UUID(repository_id),
                    effect="DENY",
                    subject_type="USER",
                    subject_value=str(settings.dev_user_id),
                    created_at=utc_now(),
                )
            )

    client.portal.call(deny)
    monkeypatch.delenv("OBSION_CODEUP_TEST_CREDENTIAL")
    install_transport(client, lambda request: pytest.fail("denied read reached network"))
    response = read(client, repository_id, {"operation": "codeup.repository.get"})
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "codeup_repository_denied"


def test_cross_tenant_and_unprivileged_requests_cannot_read(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_id = configure(client, monkeypatch)
    settings = client.app.state.settings
    install_transport(client, lambda request: pytest.fail("unauthorized read reached network"))
    principal = Principal(uuid4(), uuid4(), "outsider", "Outsider", permissions=frozenset({"*"}))
    client.app.dependency_overrides[get_principal] = lambda: principal
    try:
        assert (
            read(client, repository_id, {"operation": "codeup.repository.get"}).status_code == 404
        )
        principal = Principal(
            settings.dev_user_id,
            settings.dev_organization_id,
            "viewer",
            "Viewer",
            permissions=frozenset(),
        )
        assert (
            read(client, repository_id, {"operation": "codeup.repository.get"}).status_code == 403
        )
    finally:
        client.app.dependency_overrides.pop(get_principal)


def test_vendor_denial_is_audited_and_has_no_fake_evidence(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_id = configure(client, monkeypatch)
    install_transport(client, lambda request: httpx.Response(403, text=CREDENTIAL))
    request_id = uuid4()
    response = read(client, repository_id, {"operation": "codeup.repository.get"}, request_id)
    assert response.status_code == 403 and CREDENTIAL not in response.text
    records = client.get("/api/v1/admin/audit?limit=100").json()
    record = next(row for row in records if row["correlation_id"] == str(request_id))
    assert record["outcome"] == "FAILED"
    assert record["metadata"]["error_code"] == "codeup_upstream_denied"


def test_run_gateway_produces_restricted_code_evidence_and_rejects_resource_spoofing(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configure(client, monkeypatch)
    catalog_id = configure_catalog(client, monkeypatch)
    install_transport(client, lambda request: httpx.Response(200, json=commit()))

    async def execute() -> None:
        settings = client.app.state.settings
        async with client.app.state.database.sessions() as session, session.begin():
            principal = await load_principal_by_id(
                session, settings.dev_organization_id, settings.dev_user_id
            )
            workspace = Workspace(
                organization_id=principal.organization_id,
                owner_id=principal.id,
                name="Codeup verification",
                visibility=Visibility.PRIVATE,
            )
            session.add(workspace)
            await session.flush()
            thread = Thread(
                organization_id=principal.organization_id,
                workspace_id=workspace.id,
                created_by=principal.id,
                title="Read commit",
                status=ThreadStatus.ACTIVE,
            )
            session.add(thread)
            await session.flush()
            turn = Turn(
                organization_id=principal.organization_id,
                thread_id=thread.id,
                created_by=principal.id,
                input_text="Read commit",
                sanitized_input="Read commit",
                ordinal=1,
                created_at=utc_now(),
            )
            session.add(turn)
            await session.flush()
            run = Run(
                organization_id=principal.organization_id,
                turn_id=turn.id,
                status=RunStatus.RUNNING,
            )
            session.add(run)
            await session.flush()
            gateway = client.app.state.capability_gateway
            result = await gateway.invoke(
                session,
                GatewayRequest(
                    principal=principal,
                    capability_name="codeup.commit.get",
                    agent_name="external-client",
                    payload={
                        "operation": "codeup.commit.get",
                        "repository": NAME,
                        "commit_id": SHA,
                    },
                    resource={"repository": NAME, "source": "codeup"},
                    environment="development",
                    run_id=run.id,
                ),
            )
            assert result.status == "COMPLETED", result
            evidence = await session.get(Evidence, result.evidence_id)
            assert evidence.classification == "RESTRICTED" and evidence.evidence_type == "CODE"
            assert evidence.content["items"][0]["commit_id"] == SHA
            denied = await gateway.invoke(
                session,
                GatewayRequest(
                    principal=principal,
                    capability_name="codeup.commit.get",
                    agent_name="external-client",
                    payload={
                        "operation": "codeup.commit.get",
                        "repository": "other/repo",
                        "commit_id": SHA,
                    },
                    resource={"repository": NAME, "source": "codeup"},
                    environment="development",
                    run_id=run.id,
                ),
            )
            assert denied.status == "DENIED" and denied.evidence_id is None
            # An administrator's identity does not let an Agent Run enumerate
            # repositories outside its mapped project scope.
            catalog_denied = await gateway.invoke(
                session,
                GatewayRequest(
                    principal=principal,
                    capability_name=CODEUP_DISCOVERY_OPERATION,
                    agent_name="external-client",
                    payload={"operation": CODEUP_DISCOVERY_OPERATION},
                    resource={"connector_id": catalog_id, "source": "codeup-catalog"},
                    environment="development",
                    run_id=run.id,
                ),
            )
            assert catalog_denied.status == "DENIED"
            assert catalog_denied.evidence_id is None

    client.portal.call(execute)


def test_catalog_rotation_during_read_discards_metadata(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    connector_id = configure_catalog(client, monkeypatch)
    install_transport(client, lambda request: httpx.Response(200, json=[]))
    executor = client.app.state.capability_gateway.executors["HTTP"]
    original = executor.invoke

    async def rotate(connector, payload, credential, context):
        result = await original(connector, payload, credential, context)
        await context.session.execute(
            update(Connector)
            .where(Connector.id == connector.id)
            .values(credential_ref="env://ROTATED_TEST_CREDENTIAL")
            .execution_options(synchronize_session=False)
        )
        return result

    monkeypatch.setattr(executor, "invoke", rotate)
    response = discover(client, connector_id, {"operation": CODEUP_DISCOVERY_OPERATION})
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "codeup_repository_denied"
    assert "items" not in response.json()


@pytest.mark.parametrize("change", ["grant", "connector"])
def test_authorization_change_during_read_discards_result(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    repository_id = configure(client, monkeypatch)
    install_transport(
        client,
        lambda request: httpx.Response(
            200,
            json={"id": 123, "name": "project", "defaultBranch": "main", "visibility": "private"},
        ),
    )
    executor = client.app.state.capability_gateway.executors["HTTP"]
    original = executor.invoke

    async def revoke(connector, payload, credential, context):
        result = await original(connector, payload, credential, context)
        if change == "grant":
            context.session.add(
                CodeRepositoryGrant(
                    organization_id=context.principal.organization_id,
                    repository_id=UUID(repository_id),
                    effect="DENY",
                    subject_type="USER",
                    subject_value=str(context.principal.id),
                    created_at=utc_now(),
                )
            )
            await context.session.flush()
        else:
            # Keep the in-flight ORM object unchanged; the guard must read DB state.
            await context.session.execute(
                update(Connector)
                .where(Connector.id == connector.id)
                .values(credential_ref="env://ROTATED_TEST_CREDENTIAL")
                .execution_options(synchronize_session=False)
            )
        return result

    monkeypatch.setattr(executor, "invoke", revoke)
    request_id = uuid4()
    response = read(client, repository_id, {"operation": "codeup.repository.get"}, request_id)
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "codeup_repository_denied"
    assert "items" not in response.json()
    records = client.get("/api/v1/admin/audit?limit=100").json()
    record = next(row for row in records if row["correlation_id"] == str(request_id))
    assert record["outcome"] == "FAILED"
    assert record["metadata"]["error_code"] == "codeup_repository_denied"


def test_codeup_mapping_verifies_catalog_freezes_version_and_is_idempotent(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    connector_id, initial_repository_id = configure_mapping_source(client, monkeypatch)
    catalog_id = configure_catalog(client, monkeypatch)
    allow_codeup_mapping(client)
    second_repository = client.post("/api/v1/code/repositories", json={"name": "example/second"})
    assert second_repository.status_code == 201, second_repository.text
    second_repository_id = second_repository.json()["id"]
    install_transport(
        client,
        lambda request: httpx.Response(
            200,
            json=[
                {
                    "id": 456,
                    "name": "second",
                    "pathWithNamespace": "example/second",
                    "visibility": "private",
                    "archived": False,
                    "accessLevel": 20,
                }
            ],
        ),
    )

    mapped = map_codeup_repository(
        client,
        connector_id,
        catalog_id,
        second_repository_id,
        "456",
        "example/second",
    )
    assert mapped.status_code == 200, mapped.text
    first = mapped.json()
    assert first["outcome"] == "CREATED"
    assert first["repository_id"] == second_repository_id
    assert first["provider_repository_id"] == "456"
    assert first["connector_version_id"]
    assert "configuration" not in mapped.text
    assert "credential_ref" not in mapped.text

    replay = map_codeup_repository(
        client,
        connector_id,
        catalog_id,
        second_repository_id,
        "456",
        "example/second",
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["outcome"] == "UNCHANGED"
    assert replay.json()["connector_version_id"] == first["connector_version_id"]

    versions = client.get("/api/v1/admin/project-sources/versions")
    assert versions.status_code == 200, versions.text
    assert len(versions.json()) == 1
    assert versions.json()[0]["id"] == first["connector_version_id"]
    assert versions.json()[0]["connector_type"] == "codeup"
    assert initial_repository_id not in mapped.text


def test_codeup_mapping_rejects_duplicate_provider_id_for_another_local_repository(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    connector_id, _ = configure_mapping_source(client, monkeypatch)
    catalog_id = configure_catalog(client, monkeypatch)
    allow_codeup_mapping(client)
    second = client.post("/api/v1/code/repositories", json={"name": "example/second"})
    third = client.post("/api/v1/code/repositories", json={"name": "example/third"})
    assert second.status_code == 201 and third.status_code == 201

    def responder(request: httpx.Request) -> httpx.Response:
        path = f"example/{request.url.params.get('search', 'second')}"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 456,
                    "name": path.rsplit("/", 1)[-1],
                    "pathWithNamespace": path,
                    "visibility": "private",
                    "archived": False,
                    "accessLevel": 20,
                }
            ],
        )

    install_transport(client, responder)
    first = map_codeup_repository(
        client, connector_id, catalog_id, second.json()["id"], "456", "example/second"
    )
    assert first.status_code == 200, first.text
    conflict = map_codeup_repository(
        client, connector_id, catalog_id, third.json()["id"], "456", "example/third"
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["code"] == "project_source_conflict"
    assert "configuration" not in conflict.text
    assert "credential" not in conflict.text


def test_codeup_mapping_rechecks_catalog_before_local_mutation(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    connector_id, _ = configure_mapping_source(client, monkeypatch)
    catalog_id = configure_catalog(client, monkeypatch)
    allow_codeup_mapping(client)
    second = client.post("/api/v1/code/repositories", json={"name": "example/second"})
    assert second.status_code == 201, second.text
    install_transport(
        client,
        lambda request: httpx.Response(
            200,
            json=[
                {
                    "id": 456,
                    "name": "other",
                    "pathWithNamespace": "example/other",
                    "visibility": "private",
                    "archived": False,
                    "accessLevel": 20,
                }
            ],
        ),
    )
    response = map_codeup_repository(
        client,
        connector_id,
        catalog_id,
        second.json()["id"],
        "456",
        "example/second",
    )
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "codeup_repository_denied"
    versions = client.get("/api/v1/admin/project-sources/versions")
    assert versions.status_code == 200 and versions.json() == []


def test_codeup_mapping_requires_policy_and_connectors_write(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    connector_id, _ = configure_mapping_source(client, monkeypatch)
    catalog_id = configure_catalog(client, monkeypatch)
    second = client.post("/api/v1/code/repositories", json={"name": "example/second"})
    assert second.status_code == 201, second.text
    install_transport(
        client,
        lambda request: httpx.Response(
            200,
            json=[
                {
                    "id": 456,
                    "name": "second",
                    "pathWithNamespace": "example/second",
                    "visibility": "private",
                    "archived": False,
                    "accessLevel": 20,
                }
            ],
        ),
    )
    denied_by_policy = map_codeup_repository(
        client, connector_id, catalog_id, second.json()["id"], "456", "example/second"
    )
    assert denied_by_policy.status_code == 403, denied_by_policy.text
    assert denied_by_policy.json()["code"] == "project_source_denied"

    async def remove_write_permission() -> None:
        async with client.app.state.database.sessions() as session, session.begin():
            role = await session.scalar(
                select(Role).where(
                    Role.organization_id == client.app.state.settings.dev_organization_id,
                    Role.name == "admin",
                )
            )
            assert role is not None
            role.permissions = ["connectors.read"]
            await session.flush()

    client.portal.call(remove_write_permission)
    allowed_policy_but_missing_write = map_codeup_repository(
        client, connector_id, catalog_id, second.json()["id"], "456", "example/second"
    )
    assert allowed_policy_but_missing_write.status_code == 403
    assert allowed_policy_but_missing_write.json()["code"] == "project_source_denied"
    versions = client.get("/api/v1/admin/project-sources/versions")
    assert versions.status_code == 403


def test_codeup_source_snapshot_rejects_unhashable_allowed_repository() -> None:
    with pytest.raises(ValueError, match="project_source_configuration_invalid"):
        configuration_snapshot(
            {
                "connector_type": "codeup",
                "environment": "development",
                "endpoint": CODEUP_ORIGIN,
                "configuration": {
                    "protocol": "codeup.read.v1",
                    "organization_id": "org-example",
                    "repositories": {NAME: {"id": "123", "repository_id": str(uuid4())}},
                    "allowed_repositories": [{"unexpected": "object"}],
                },
                "credential_ref": None,
                "declared_grants": ["code.read"],
                "allowed_egress": ["openapi-rdc.aliyuncs.com:443"],
            },
            environment="development",
        )
