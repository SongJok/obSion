from uuid import uuid4

from fastapi.testclient import TestClient

from obsion.security.auth import get_principal
from obsion.security.identity import Principal


def test_identical_document_reingest_rebinds_chunk_acl(client: TestClient) -> None:
    settings = client.app.state.settings
    content = b"The governed release process requires an owner and rollback plan."
    initial = client.post(
        "/api/v1/knowledge/documents",
        files={"file": ("release.md", content, "text/markdown")},
        data={
            "source": "phase12",
            "external_id": "release-policy",
            "title": "Release policy",
            "classification": "INTERNAL",
            "acl": '{"organization": true}',
        },
    )
    assert initial.status_code == 201, initial.text

    other_user_id = uuid4()
    tightened = client.post(
        "/api/v1/knowledge/documents",
        files={"file": ("release.md", content, "text/markdown")},
        data={
            "source": "phase12",
            "external_id": "release-policy",
            "title": "Release policy restricted",
            "classification": "INTERNAL",
            "acl": (
                '{"users": ["'
                + str(settings.dev_user_id)
                + '"], "deny_users": ["'
                + str(other_user_id)
                + '"]}'
            ),
        },
    )
    assert tightened.status_code == 201, tightened.text
    assert tightened.json()["version"] == initial.json()["version"]

    restricted = Principal(
        id=other_user_id,
        organization_id=settings.dev_organization_id,
        external_id="phase12-restricted",
        display_name="Restricted reader",
        permissions=frozenset({"knowledge.read.internal"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: restricted
    try:
        search = client.post(
            "/api/v1/knowledge/search",
            json={"query": "release rollback", "limit": 20},
        )
        assert search.status_code == 200, search.text
        assert search.json() == []
    finally:
        client.app.dependency_overrides.pop(get_principal, None)
