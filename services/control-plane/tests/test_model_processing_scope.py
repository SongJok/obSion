from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from obsion.db.models import AuditRecord, ModelEndpoint
from obsion.security.auth import get_principal, load_principal_by_id


def endpoint(client, *, grant=True):
    response = client.post(
        "/api/v1/admin/models/endpoints",
        json={
            "name": "scope-test",
            "provider": "openai-compatible",
            "base_url": "http://localhost:9999/v1",
            "model_id": "kimi-k3-kimi",
            "credential_ref": "env://SYNTHETIC_SCOPE_TEST",
            "region": "test",
            "classifications": ["PUBLIC", "INTERNAL"],
            "capabilities": ["chat", "json_mode"],
            "limits": {"context_window": 32000},
            "enabled": True,
        },
    )
    assert response.status_code == 201, response.text
    identity = response.json()["id"]
    if grant:
        policy = client.post(
            "/api/v1/admin/policies",
            json={
                "name": "scope-grant",
                "priority": 100,
                "effect": "ALLOW",
                "conditions": {
                    "actions": ["models.write"],
                    "resource": {"endpoint_id": identity},
                    "context": {"operation": "model.processing_scope.update"},
                },
                "obligations": [],
                "reason": "Synthetic explicitly approved endpoint scope change",
            },
        )
        assert policy.status_code == 201, policy.text
    return identity


def test_processing_scope_preserves_endpoint_configuration_and_audits_change(client):
    identity = endpoint(client)

    async def snapshot():
        async with client.app.state.database.sessions() as session:
            row = await session.get(ModelEndpoint, UUID(identity))
            return {
                column.name: getattr(row, column.name) for column in ModelEndpoint.__table__.columns
            }

    before = client.portal.call(snapshot)
    response = client.patch(
        f"/api/v1/admin/models/endpoints/{identity}/processing-scope",
        json={"classifications": ["PUBLIC", "INTERNAL", "CONFIDENTIAL", "RESTRICTED"]},
    )
    assert response.status_code == 200, response.text
    assert set(response.json()) == {"id", "classifications"}
    after = client.portal.call(snapshot)
    assert after.pop("classifications") == response.json()["classifications"]
    before.pop("classifications")
    assert after.pop("updated_at") >= before.pop("updated_at")
    assert after == before

    async def audit():
        async with client.app.state.database.sessions() as session:
            return await session.scalar(
                select(AuditRecord).where(
                    AuditRecord.action == "model.processing_scope.update",
                    AuditRecord.resource_id == identity,
                )
            )

    row = client.portal.call(audit)
    assert row.outcome == "SUCCESS" and row.policy_decision_id is not None
    assert row.redacted_metadata["before"] == ["PUBLIC", "INTERNAL"]
    assert row.redacted_metadata["after"][-1] == "RESTRICTED"
    assert "SYNTHETIC_SCOPE_TEST" not in str(row.redacted_metadata)


@pytest.mark.parametrize("denial", ["DENY", "ASK", "no_permission", "foreign_org", "default_mask"])
def test_scope_cannot_bypass_policy_permission_or_organization(client, denial):
    identity = endpoint(client, grant=denial != "default_mask")
    if denial in {"DENY", "ASK"}:
        response = client.post(
            "/api/v1/admin/policies",
            json={
                "name": "scope-denial",
                "priority": 9999,
                "effect": denial,
                "conditions": {"actions": ["models.write"]},
                "obligations": [],
                "reason": "Synthetic processing scope policy",
            },
        )
        assert response.status_code == 201, response.text
    elif denial != "default_mask":

        async def restricted_principal():
            async with client.app.state.database.sessions() as session:
                row = await session.get(ModelEndpoint, UUID(identity))
                current = await load_principal_by_id(
                    session, row.organization_id, UUID("00000000-0000-7000-8000-000000000002")
                )
                return (
                    replace(current, permissions=frozenset())
                    if denial == "no_permission"
                    else replace(current, organization_id=uuid4())
                )

        principal = client.portal.call(restricted_principal)
        # A foreign organization must exist for the PolicyDecision FK. Use the
        # existing organization but move only the target to a different tenant.
        if denial == "foreign_org":
            from obsion.db.models import Organization

            async def foreign_endpoint():
                async with client.app.state.database.sessions() as session, session.begin():
                    session.add(
                        Organization(
                            id=principal.organization_id,
                            name="Other scope tenant",
                            slug="scope-other",
                        )
                    )
                    await session.flush()
                    row = await session.get(ModelEndpoint, UUID(identity))
                    row.organization_id = principal.organization_id

            client.portal.call(foreign_endpoint)
        else:
            client.app.dependency_overrides[get_principal] = lambda: principal
    response = client.patch(
        f"/api/v1/admin/models/endpoints/{identity}/processing-scope",
        json={"classifications": ["RESTRICTED"]},
    )
    assert response.status_code == (404 if denial == "foreign_org" else 403), response.text
    client.app.dependency_overrides.pop(get_principal, None)

    async def check():
        async with client.app.state.database.sessions() as session:
            row = await session.get(ModelEndpoint, UUID(identity))
            assert row.classifications == ["PUBLIC", "INTERNAL"]
            if denial != "foreign_org":
                audit = await session.scalar(
                    select(AuditRecord).where(
                        AuditRecord.action == "model.processing_scope.update",
                        AuditRecord.resource_id == identity,
                    )
                )
                assert audit.outcome == "DENIED" and audit.policy_decision_id is not None

    client.portal.call(check)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"classifications": []},
        {"classifications": ["INVALID"]},
        {"classifications": ["PUBLIC"], "credential_ref": "env://OTHER"},
    ],
)
def test_scope_rejects_invalid_values_and_unrelated_configuration(client, body):
    response = client.patch(f"/api/v1/admin/models/endpoints/{uuid4()}/processing-scope", json=body)
    assert response.status_code == 422
