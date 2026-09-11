"""Real REST/Policy/Gateway with an explicitly simulated read-only vendor."""

from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select, update

from obsion.capabilities.connectors import HttpJsonExecutor
from obsion.db.models import Connector, Role

TOKEN = "catalog-pat-sentinel"


def configure(client, monkeypatch):
    monkeypatch.setenv("OBSION_CATALOG_TEST_TOKEN", TOKEN)
    response = client.post(
        "/api/v1/admin/connectors",
        json={
            "name": "pat-visible-catalog",
            "connector_type": "yunxiao",
            "status": "ACTIVE",
            "environment": "development",
            "endpoint": "https://openapi-rdc.aliyuncs.com",
            "credential_ref": "env://OBSION_CATALOG_TEST_TOKEN",
            "configuration": {"protocol": "yunxiao.devops.v1"},
            "declared_grants": ["code.read"],
            "allowed_egress": ["https://openapi-rdc.aliyuncs.com"],
        },
    )
    assert response.status_code == 201, response.text
    connector_id = response.json()["id"]
    definition = next(
        d
        for d in client.get("/api/v1/capabilities").json()
        if d["name"] == "yunxiao.repositories.list"
    )
    response = client.post(
        f"/api/v1/admin/capabilities/{definition['id']}/bindings",
        json={
            "connector_id": connector_id,
            "environment": "development",
            "resource_selector": {"connector_id": connector_id, "source": "yunxiao-catalog"},
        },
    )
    assert response.status_code == 201, response.text
    return connector_id


def discover(client, connector_id, **body):
    return client.post(
        f"/api/v1/admin/yunxiao/connectors/{connector_id}/repositories",
        json={"operation": "yunxiao.repositories.list", **body},
    )


def test_catalog_all_organizations_pages_are_audited_without_auto_access(client, monkeypatch):
    connector_id = configure(client, monkeypatch)
    original_repositories = client.get("/api/v1/code/repositories").json()
    calls = []

    def vendor(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.headers["x-yunxiao-token"] == TOKEN
        if request.url.path.endswith("/platform/organizations"):
            return httpx.Response(200, json=[{"id": "org-a"}, {"id": "org-b"}])
        organization = request.url.path.split("/")[-2]
        return httpx.Response(
            200,
            headers={"x-total": "1"},
            json=[
                {
                    "id": 1,
                    "name": organization,
                    "pathWithNamespace": f"{organization}/project",
                    "archived": False,
                    "description": TOKEN,
                    "creatorEmail": "private@example.invalid",
                }
            ],
        )

    client.app.state.capability_gateway.executors["HTTP"] = HttpJsonExecutor(
        client.app.state.settings, transport=httpx.MockTransport(vendor)
    )
    pages = [discover(client, connector_id, page=p, per_page=1) for p in (1, 2)]
    assert [p.status_code for p in pages] == [200, 200], [p.text for p in pages]
    assert [p.json()["complete"] for p in pages] == [False, True]
    assert [p.json()["total"] for p in pages] == [2, 2]
    assert [p.json()["items"][0]["organization_id"] for p in pages] == ["org-a", "org-b"]
    assert TOKEN not in str([p.json() for p in pages])
    assert "description" not in pages[0].text and "creatorEmail" not in pages[0].text
    assert client.get("/api/v1/code/repositories").json() == original_repositories
    audits = client.get("/api/v1/admin/audit?limit=100").json()
    successful = [a for a in audits if a["action"] == "code.read" and a["outcome"] == "SUCCESS"]
    assert len(successful) == 2
    assert TOKEN not in str(audits)
    assert len(calls) == 6


@pytest.mark.parametrize(
    "body", [{"page": True}, {"per_page": 101}, {"url": "https://evil.invalid"}]
)
def test_catalog_rejects_invalid_input(client, body):
    assert discover(client, uuid4(), **body).status_code == 422


def test_catalog_requires_current_admin_permissions_before_credentials(client, monkeypatch):
    connector_id = configure(client, monkeypatch)
    gateway = client.app.state.capability_gateway

    async def no_credentials(*args, **kwargs):
        pytest.fail("unauthorized inventory reached credentials")

    monkeypatch.setattr(gateway.credentials, "resolve", no_credentials)

    async def remove_admin():
        async with client.app.state.database.sessions() as session, session.begin():
            role = await session.scalar(select(Role).where(Role.name == "admin"))
            role.permissions = ["code.read"]

    client.portal.call(remove_admin)
    response = discover(client, connector_id)
    assert response.status_code == 403, response.text


def test_catalog_discards_result_when_connector_revoked_during_read(client, monkeypatch):
    connector_id = configure(client, monkeypatch)
    executor = HttpJsonExecutor(
        client.app.state.settings,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])),
    )
    original = executor.invoke

    async def revoke(connector, payload, credential, context):
        result = await original(connector, payload, credential, context)
        await context.session.execute(
            update(Connector)
            .where(Connector.id == connector.id)
            .values(status="DISABLED")
            .execution_options(synchronize_session=False)
        )
        return result

    monkeypatch.setattr(executor, "invoke", revoke)
    client.app.state.capability_gateway.executors["HTTP"] = executor
    response = discover(client, connector_id)
    assert response.status_code == 403, response.text
    assert response.json()["code"] == "codeup_repository_denied"


def test_catalog_unknown_connector_has_configuration_guidance(client):
    response = discover(client, uuid4())
    assert response.status_code == 422
    assert response.json()["code"] == "codeup_configuration_invalid"


def test_capability_binding_replay_is_idempotent_and_scope_change_is_conflict(client, monkeypatch):
    connector_id = configure(client, monkeypatch)
    definition = next(
        d
        for d in client.get("/api/v1/capabilities").json()
        if d["name"] == "yunxiao.repositories.list"
    )
    body = {
        "connector_id": connector_id,
        "environment": "development",
        "resource_selector": {"connector_id": connector_id, "source": "yunxiao-catalog"},
    }
    path = f"/api/v1/admin/capabilities/{definition['id']}/bindings"
    first = client.post(path, json=body)
    replay = client.post(path, json=body)
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    conflict = client.post(path, json={**body, "resource_selector": {}})
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "capability_binding_conflict"
