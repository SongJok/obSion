from fastapi.testclient import TestClient

from obsion.config import Settings
from obsion.domain.enums import SystemRole
from obsion.main import create_app


def test_unauthenticated_request_cannot_create_thread(app_settings: Settings) -> None:
    with TestClient(create_app(app_settings)) as unauthenticated_client:
        response = unauthenticated_client.post(
            "/api/v1/threads",
            json={
                "workspace_id": "00000000-0000-7000-8000-000000000010",
                "title": "Must not be created",
            },
        )

    assert response.status_code == 403
    assert response.json()["code"] == "authentication_required"


def test_development_authentication_rejects_the_wrong_bearer_token(
    app_settings: Settings,
) -> None:
    with TestClient(
        create_app(app_settings),
        headers={"Authorization": "Bearer invalid-development-token"},
    ) as invalid_client:
        response = invalid_client.get("/api/v1/workspaces")

    assert response.status_code == 403
    assert response.json()["code"] == "invalid_token"


def test_phase2_departments_and_system_roles_are_organization_scoped(
    client: TestClient,
) -> None:
    roles = client.get("/api/v1/admin/roles")
    assert roles.status_code == 200, roles.text
    system_roles = {item["name"] for item in roles.json() if item["system"]}
    assert system_roles >= {role.value for role in SystemRole}
    assert next(item for item in roles.json() if item["name"] == "admin")["permissions"] == ["*"]

    parent = client.post(
        "/api/v1/admin/departments",
        json={"name": "Product", "description": "Product organization"},
    )
    assert parent.status_code == 201, parent.text
    child = client.post(
        "/api/v1/admin/departments",
        json={
            "name": "Analytics",
            "description": "Product analytics",
            "parent_id": parent.json()["id"],
        },
    )
    assert child.status_code == 201, child.text

    user = client.post(
        "/api/v1/admin/users",
        json={
            "external_id": "phase2-analyst",
            "email": "phase2-analyst@obsion.dev",
            "display_name": "Phase 2 Analyst",
            "department_id": child.json()["id"],
            "attributes": {},
        },
    )
    assert user.status_code == 201, user.text

    departments = client.get("/api/v1/admin/departments")
    assert departments.status_code == 200, departments.text
    analytics = next(item for item in departments.json() if item["name"] == "Analytics")
    assert analytics["parent_id"] == parent.json()["id"]
    assert analytics["active_user_count"] == 1

    users = client.get("/api/v1/admin/users")
    analyst = next(item for item in users.json() if item["id"] == user.json()["id"])
    assert analyst["department_id"] == child.json()["id"]
    assert analyst["department"] == "Analytics"


def test_custom_role_cannot_shadow_a_system_role(client: TestClient) -> None:
    response = client.post(
        "/api/v1/admin/roles",
        json={"name": "viewer", "description": "Shadow", "permissions": []},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "request_validation_failed"

    wildcard = client.post(
        "/api/v1/admin/roles",
        json={"name": "custom-admin", "description": "Shadow", "permissions": ["*"]},
    )
    assert wildcard.status_code == 422
    assert wildcard.json()["code"] == "request_validation_failed"
