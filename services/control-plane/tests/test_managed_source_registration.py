"""Managed source setup must work from an ordinary registry and admin API."""

from sqlalchemy import select

from obsion.capabilities.dingtalk_managed import MANAGED_CONNECTOR_TYPE, MANAGED_OPERATIONS
from obsion.db.models import CapabilityBinding, CapabilityDefinition, CapabilityVersion
from obsion.registry.builtins import bootstrap_builtin_registry


def test_managed_source_descriptors_are_registered_without_implicit_connections(client):
    response = client.get("/api/v1/capabilities")
    assert response.status_code == 200
    descriptors = {row["name"]: row for row in response.json()}
    assert descriptors.keys() >= MANAGED_OPERATIONS
    for name in MANAGED_OPERATIONS:
        descriptor = descriptors[name]
        assert descriptor["transport"] == "SDK"
        assert descriptor["risk"] == "L2" and descriptor["side_effect"] == "NONE"
        assert descriptor["permission"] == "knowledge.write"
        assert descriptor["timeout_seconds"] == 120
        assert descriptor["data_classification"] == "RESTRICTED"
        assert descriptor["input_schema"]["properties"]["operation"]["const"] == name

    async def check_registry():
        async with client.app.state.database.sessions() as session, session.begin():

            async def versions():
                return list(
                    await session.scalars(
                        select(CapabilityVersion.id)
                        .join(CapabilityDefinition)
                        .where(CapabilityDefinition.name.in_(MANAGED_OPERATIONS))
                        .order_by(CapabilityVersion.id)
                    )
                )

            before = await versions()
            assert len(before) == 2
            assert not list(
                await session.scalars(
                    select(CapabilityBinding.id).where(
                        CapabilityBinding.capability_version_id.in_(before)
                    )
                )
            )
            await bootstrap_builtin_registry(
                session,
                client.app.state.settings.dev_organization_id,
                client.app.state.settings.dev_user_id,
            )
            assert await versions() == before

    client.portal.call(check_registry)


def test_admin_can_register_a_managed_source_without_fixture_database_writes(client):
    user_id = str(client.app.state.settings.dev_user_id)

    def post(path, payload):
        response = client.post("/api/v1/" + path, json=payload)
        assert response.status_code in {200, 201}, response.text
        return response.json()

    installation = post(
        "admin/im-installations",
        {
            "channel": "dingtalk",
            "installation_id": "registration-test",
            "corp_id": "corp_test",
            "app_key": "app_test",
        },
    )
    binding = post(
        "admin/im-bindings",
        {
            "channel": "dingtalk",
            "sender_id": "vendor_user",
            "user_id": user_id,
            "installation_id": installation["id"],
        },
    )
    connector = post(
        "admin/connectors",
        {
            "name": "managed-registration-test",
            "connector_type": MANAGED_CONNECTOR_TYPE,
            "environment": "development",
            "status": "ACTIVE",
            "credential_ref": "env://OBSION_REGISTRATION_TEST_SECRET",
            "declared_grants": ["knowledge.write"],
            "allowed_egress": [],
            "configuration": {
                "corp_id": "corp_test",
                "user_id": "vendor_user",
                "operator_id": "union_test",
                "installation_id": installation["id"],
                "app_key_env": "OBSION_REGISTRATION_APP_KEY",
            },
        },
    )
    descriptors = client.get("/api/v1/capabilities").json()
    for descriptor in descriptors:
        if descriptor["name"] in MANAGED_OPERATIONS:
            post(
                "admin/capabilities/" + descriptor["id"] + "/bindings",
                {
                    "connector_id": connector["id"],
                    "environment": "development",
                    "resource_selector": {"source": "dingtalk-managed", "corp_id": "corp_test"},
                },
            )
    post(
        "admin/policies",
        {
            "name": "managed-registration-test",
            "effect": "ALLOW",
            "conditions": {"actions": ["knowledge.write"], "user_ids": [user_id]},
            "reason": "Synthetic administrator source registration test",
        },
    )
    source_path = "knowledge/sources/dingtalk/managed"
    connections = client.get("/api/v1/" + source_path + "/connections").json()
    assert any(item["connector_id"] == connector["id"] for item in connections)
    source = post(source_path, {"connector_id": connector["id"], "binding_id": binding["id"]})
    assert source["state"] == "QUEUED" and source["counts"]["available"] == 0
    paused = post(source_path + "/" + source["id"] + "/control", {"operation": "pause"})
    assert paused["state"] == "PAUSED"
